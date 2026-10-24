#!/usr/bin/env python3
"""
load_scheduler.py — Variable load pattern cho GPU mining.
Mục đích: tránh utilization anomaly detection (flat-line 100% GPU).

Chạy miner như subprocess, throttle/thay đổi power limit theo lịch random.
Không cần root — dùng nvidia-smi (có sẵn CUDA toolkit) hoặc fallback time-based.

Usage:
    python3 load_scheduler.py -- ./onezerominer --algo=... --pool=...
    python3 load_scheduler.py --gpu-ctrl nvidia-smi -- ./miner ...
    
Modes:
    - random-gaps: mine N phút, pause M phút (random)
    - power-cycle: mine full, downclock, mine full (power limit cycling)
    - sine: sinusoidal load pattern
"""
import os
import sys
import time
import signal
import random
import argparse
import subprocess
from typing import Optional, List

# ── Logging ──────────────────────────────────────────────────────────────────
VERBOSE = False

def log(msg):
    sys.stderr.write("[sched] {}\n".format(msg))
    sys.stderr.flush()

def log_err(msg):
    sys.stderr.write("[sched:err] {}\n".format(msg))
    sys.stderr.flush()


# ── GPU control ──────────────────────────────────────────────────────────────
class GPUController:
    """Điều khiển GPU power limit / clock. Fallback nếu không có nvidia-smi."""
    
    def __init__(self, mode: str = "nvidia-smi", gpu_id: int = 0):
        self.mode = mode
        self.gpu_id = gpu_id
        self.available = self._detect()
        self.orig_power: Optional[int] = None
    
    def _detect(self) -> bool:
        if self.mode == "none":
            return False
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=power.limit", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                self.orig_power = int(r.stdout.strip().split("\n")[self.gpu_id])
                log("GPU power limit hiện tại: {}W".format(self.orig_power))
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError):
            pass
        
        log("nvidia-smi không available — dùng time-based throttle")
        return False
    
    def set_power_limit(self, watts: Optional[int]):
        """Set power limit. None = reset về mặc định."""
        if not self.available:
            return
        
        target = watts if watts is not None else self.orig_power
        if target is None:
            return
        
        try:
            subprocess.run(
                ["nvidia-smi", "-pl", str(target), "-i", str(self.gpu_id)],
                capture_output=True, timeout=5,
            )
            log("power limit → {}W".format(target))
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            log_err("set_power_limit fail: {}".format(e))
    
    def get_utilization(self) -> Optional[int]:
        """Đọc GPU utilization %."""
        if not self.available:
            return None
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits", "-i", str(self.gpu_id)],
                capture_output=True, text=True, timeout=5,
            )
            return int(r.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            return None
    
    def reset(self):
        """Reset về power limit gốc."""
        self.set_power_limit(None)


# ── Load patterns ────────────────────────────────────────────────────────────
class LoadPattern:
    """Sinh load pattern theo strategy."""
    
    def __init__(self, strategy: str, **kwargs):
        self.strategy = strategy
        self.kwargs = kwargs
    
    def next_cycle(self) -> dict:
        """Trả về {duration_sec, power_watts} cho cycle tiếp theo."""
        if self.strategy == "random-gaps":
            return self._random_gaps()
        elif self.strategy == "power-cycle":
            return self._power_cycle()
        elif self.strategy == "sine":
            return self._sine()
        else:
            return {"duration": 600, "power": None}
    
    def _random_gaps(self) -> dict:
        """Mine N phút, pause M phút."""
        mine_min = self.kwargs.get("mine_min", 30)
        mine_max = self.kwargs.get("mine_max", 120)
        pause_min = self.kwargs.get("pause_min", 2)
        pause_max = self.kwargs.get("pause_max", 15)
        in_pause = self.kwargs.get("_in_pause", False)
        
        if in_pause:
            duration = random.uniform(pause_min, pause_max) * 60
            power = 0  # pause = power min
        else:
            duration = random.uniform(mine_min, mine_max) * 60
            power = None  # normal
        
        # Flip state
        self.kwargs["_in_pause"] = not in_pause
        
        return {"duration": duration, "power": power}
    
    def _power_cycle(self) -> dict:
        """Alternate giữa high power và low power."""
        high_w = self.kwargs.get("high_w", 250)
        low_w = self.kwargs.get("low_w", 120)
        high_min = self.kwargs.get("high_min", 10)
        low_min = self.kwargs.get("low_min", 5)
        in_low = self.kwargs.get("_in_low", False)
        
        if in_low:
            duration = random.uniform(low_min * 0.8, low_min * 1.2) * 60
            power = low_w
        else:
            duration = random.uniform(high_min * 0.8, high_min * 1.2) * 60
            power = high_w
        
        self.kwargs["_in_low"] = not in_low
        return {"duration": duration, "power": power}
    
    def _sine(self) -> dict:
        """Sinusoidal power pattern."""
        import math
        period = self.kwargs.get("period_min", 60) * 60
        p_min = self.kwargs.get("power_min", 100)
        p_max = self.kwargs.get("power_max", 250)
        
        t = time.time()
        phase = (t % period) / period * 2 * math.pi
        power = p_min + (p_max - p_min) * (0.5 + 0.5 * math.sin(phase))
        
        # Step nhỏ (30s)
        return {"duration": 30, "power": int(power)}


# ── Scheduler ────────────────────────────────────────────────────────────────
class MinerScheduler:
    """Quản lý miner process + load pattern."""
    
    def __init__(self, miner_cmd: List[str], pattern: LoadPattern,
                 gpu: GPUController, dry_run: bool = False):
        self.miner_cmd = miner_cmd
        self.pattern = pattern
        self.gpu = gpu
        self.dry_run = dry_run
        self.miner_proc: Optional[subprocess.Popen] = None
        self.running = False
    
    def _start_miner(self):
        """Khởi động miner process."""
        if self.dry_run:
            log("[dry-run] would start: {}".format(" ".join(self.miner_cmd)))
            return
        
        log("starting miner: {}".format(" ".join(self.miner_cmd[:3]) + " ..."))
        self.miner_proc = subprocess.Popen(
            self.miner_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log("miner PID={}".format(self.miner_proc.pid))
    
    def _stop_miner(self):
        """Dừng miner process."""
        if self.miner_proc and self.miner_proc.poll() is None:
            log("stopping miner (SIGTERM)...")
            self.miner_proc.terminate()
            try:
                self.miner_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.miner_proc.kill()
                self.miner_proc.wait()
            log("miner stopped")
        self.miner_proc = None
    
    def _pause_gpu(self):
        """Giảm GPU power (không kill process)."""
        if not self.dry_run:
            self.gpu.set_power_limit(100)  # low power
    
    def _resume_gpu(self):
        """Reset GPU power."""
        if not self.dry_run:
            self.gpu.reset()
    
    def run(self):
        """Main loop: mine → pause → mine → ..."""
        self.running = True
        log("scheduler started (strategy={})".format(self.pattern.strategy))
        
        # Start miner
        self._start_miner()
        
        cycle = 0
        try:
            while self.running:
                cycle += 1
                spec = self.pattern.next_cycle()
                duration = spec["duration"]
                power = spec["power"]
                
                # Áp dụng power limit
                if power == 0:
                    # Pause mode — giảm power tối thiểu
                    log("cycle {}: PAUSE {:.0f}s".format(cycle, duration))
                    self._pause_gpu()
                elif power is not None:
                    log("cycle {}: POWER {}W for {:.0f}s".format(cycle, power, duration))
                    if not self.dry_run:
                        self.gpu.set_power_limit(power)
                else:
                    # Normal mining — reset power
                    log("cycle {}: FULL POWER for {:.0f}s".format(cycle, duration))
                    self._resume_gpu()
                
                # Chờ duration (check miner vẫn sống)
                deadline = time.time() + duration
                while time.time() < deadline and self.running:
                    if self.miner_proc and self.miner_proc.poll() is not None:
                        log_err("miner died! exit code={}".format(self.miner_proc.returncode))
                        # Restart
                        self._start_miner()
                    time.sleep(5)
        
        except KeyboardInterrupt:
            log("interrupted")
        
        finally:
            self.running = False
            self._stop_miner()
            self.gpu.reset()
            log("scheduler stopped")
    
    def stop(self):
        self.running = False


# ── Signal handling ──────────────────────────────────────────────────────────
def setup_signals(scheduler: MinerScheduler):
    def handler(sig, frame):
        log("signal {} received".format(sig))
        scheduler.stop()
    
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    global VERBOSE
    
    ap = argparse.ArgumentParser(
        description="Variable load scheduler cho mining",
        usage="%(prog)s [options] -- <miner_cmd...>",
    )
    ap.add_argument("--strategy", choices=["random-gaps", "power-cycle", "sine"],
                    default="random-gaps", help="Load pattern strategy")
    ap.add_argument("--mine-min", type=float, default=30, help="Min mine time (min)")
    ap.add_argument("--mine-max", type=float, default=120, help="Max mine time (min)")
    ap.add_argument("--pause-min", type=float, default=2, help="Min pause time (min)")
    ap.add_argument("--pause-max", type=float, default=15, help="Max pause time (min)")
    ap.add_argument("--high-w", type=int, default=250, help="High power limit (W)")
    ap.add_argument("--low-w", type=int, default=120, help="Low power limit (W)")
    ap.add_argument("--gpu-id", type=int, default=0, help="GPU index")
    ap.add_argument("--no-gpu-ctrl", action="store_true", help="No GPU control (time-only)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    
    # Mọi args sau "--" là miner command
    args, miner_cmd = ap.parse_known_args()
    VERBOSE = args.verbose
    
    if miner_cmd and miner_cmd[0] == "--":
        miner_cmd = miner_cmd[1:]
    
    if not miner_cmd:
        log_err("Missing miner command. Usage: {} -- ./onezerominer --algo=...".format(ap.prog))
        sys.exit(1)
    
    # GPU controller
    gpu = GPUController(mode="none" if args.no_gpu_ctrl else "nvidia-smi",
                        gpu_id=args.gpu_id)
    
    # Load pattern
    pattern = LoadPattern(
        args.strategy,
        mine_min=args.mine_min,
        mine_max=args.mine_max,
        pause_min=args.pause_min,
        pause_max=args.pause_max,
        high_w=args.high_w,
        low_w=args.low_w,
    )
    
    # Scheduler
    scheduler = MinerScheduler(
        miner_cmd=miner_cmd,
        pattern=pattern,
        gpu=gpu,
        dry_run=args.dry_run,
    )
    
    setup_signals(scheduler)
    scheduler.run()


if __name__ == "__main__":
    main()
