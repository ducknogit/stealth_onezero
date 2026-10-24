"""
gpu_resident.py — Layer 5b: VRAM RESIDENT — chiếm VRAM thật, stable như training

Yêu cầu: 96GB VRAM → ăn gần đầy (target ~88%), KHÔNG nhấp nháy,
utilization ổn định như train AI thật.

Backends (chọn theo thứ tự):
1. torch (CUDA): allocate big tensors + matmul compute loop → util THẬT 80-95%
2. cuda-ctypes: cuMemAlloc trực tiếp qua libcuda → VRAM THẬT, util=0
   (bù bởi miner nếu GPU-mining, hoặc chỉ cần memory signal)

Stable training profile:
- Steady-state pool: target_frac * total (weights + activations + optimizer states)
- Compute loop: forward/backward-like matmul, duty ~90%
- Churn: thay 1-2 chunk mỗi 2-10 phút (fragmentation thật, không down về 0)
- Checkpoint spike hiếm: +2-4% rồi trả về
"""
import os
import sys
import time
import random
import ctypes
import threading
import subprocess
from typing import Optional, List


# ═══════════════════════════════════════════════════════════════
# GPU INFO
# ═══════════════════════════════════════════════════════════════

def gpu_total_mb(gpu_id: int = 0) -> int:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total",
             "--format=csv,noheader,nounits", f"-i={gpu_id}"],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return int(r.stdout.strip())
    except Exception:
        pass
    # pynvml fallback
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(gpu_id)
        return pynvml.nvmlDeviceGetMemoryInfo(h).total // 2**20
    except Exception:
        return 0


def gpu_used_mb(gpu_id: int = 0) -> int:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits", f"-i={gpu_id}"],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return int(r.stdout.strip())
    except Exception:
        pass
    return 0


# ═══════════════════════════════════════════════════════════════
# CUDA CTYPES BACKEND — cuMemAlloc không cần torch
# ═══════════════════════════════════════════════════════════════

class CudaCtypes:
    """Alloc VRAM trực tiếp qua CUDA Driver API (ctypes)."""

    def __init__(self, gpu_id: int = 0):
        self.gpu_id = gpu_id
        self._ok = False
        self._ptrs: List[int] = []
        self._lib = None
        self._ctx = None
        self._load()

    def _load(self):
        for name in ["libcuda.so.1", "libcuda.so", "nvcuda.dll", "cuda.dll"]:
            try:
                lib = ctypes.CDLL(name, use_errno=True)
                if hasattr(lib, "cuInit"):
                    self._lib = lib
                    break
            except Exception:
                continue
        if self._lib is None:
            return
        try:
            lib = self._lib
            lib.cuInit.argtypes = [ctypes.c_uint]
            lib.cuInit.restype = ctypes.c_int
            if lib.cuInit(0) != 0:
                return
            dev = ctypes.c_int()
            lib.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
            lib.cuDeviceGet.restype = ctypes.c_int
            if lib.cuDeviceGet(ctypes.byref(dev), self.gpu_id) != 0:
                return
            # CUDA 12 dùng cuCtxCreate_v2; thử cả hai
            ctx = ctypes.c_ulonglong(0)
            created = -1
            for fname, restype in [("cuCtxCreate_v2", ctypes.c_int),
                                   ("cuCtxCreate", ctypes.c_int)]:
                try:
                    fn = getattr(lib, fname)
                    fn.argtypes = [ctypes.POINTER(ctypes.c_ulonglong),
                                   ctypes.c_uint, ctypes.c_int]
                    fn.restype = restype
                    if fn(ctypes.byref(ctx), 0, dev.value) == 0:
                        created = 0
                        break
                except Exception:
                    continue
            if created != 0:
                return
            self._ctx = ctx
            # Versioned symbols: Windows export cả cuMemAlloc (32-bit compat,
            # fail với INVALID_CONTEXT) và cuMemAlloc_v2 (đúng) → ưu tiên _v2
            self._alloc_fn = None
            self._free_fn = None
            for alloc_name, free_name in [("cuMemAlloc_v2", "cuMemFree_v2"),
                                          ("cuMemAlloc", "cuMemFree")]:
                if hasattr(lib, alloc_name) and hasattr(lib, free_name):
                    fn = getattr(lib, alloc_name)
                    fn.argtypes = [ctypes.POINTER(ctypes.c_ulonglong), ctypes.c_size_t]
                    fn.restype = ctypes.c_int
                    fr = getattr(lib, free_name)
                    fr.argtypes = [ctypes.c_ulonglong]
                    fr.restype = ctypes.c_int
                    self._alloc_fn = fn
                    self._free_fn = fr
                    break
            if self._alloc_fn is None:
                return
            self._ok = True
        except Exception:
            self._ok = False

    @property
    def available(self) -> bool:
        return self._ok

    def alloc(self, size_bytes: int) -> bool:
        if not self._ok:
            return False
        try:
            ptr = ctypes.c_ulonglong(0)
            # Set current context (thread-local → gọi mỗi lần, an toàn)
            if hasattr(self._lib, "cuCtxSetCurrent"):
                self._lib.cuCtxSetCurrent.argtypes = [ctypes.c_ulonglong]
                self._lib.cuCtxSetCurrent(self._ctx)
            r = self._alloc_fn(ctypes.byref(ptr), size_bytes)
            if r == 0 and ptr.value:
                self._ptrs.append(ptr.value)
                return True
        except Exception:
            pass
        return False

    def free(self, ptr: int) -> bool:
        if not self._ok:
            return False
        try:
            if hasattr(self._lib, "cuCtxSetCurrent"):
                self._lib.cuCtxSetCurrent.argtypes = [ctypes.c_ulonglong]
                self._lib.cuCtxSetCurrent(self._ctx)
            return self._free_fn(ptr) == 0
        except Exception:
            return False

    def free_all(self):
        if not self._ok:
            return
        for p in self._ptrs:
            try:
                self.free(p)
            except Exception:
                pass
        self._ptrs.clear()


# ═══════════════════════════════════════════════════════════════
# VRAM RESIDENT — Main
# ═══════════════════════════════════════════════════════════════

class VRAMResident:
    """
    Chiếm VRAM stable như training:
    - target_frac (mặc định 0.88) của tổng VRAM
    - steady: không nhấp nháy về 0
    - compute loop (torch): utilization thật 80-95%
    - churn: thay chunk nhỏ mỗi 2-10 phút (fragmentation)
    - checkpoint spike hiếm (+2-4%, trả về ngay)
    """

    def __init__(self, gpu_id: int = 0, target_frac: float = 0.88,
                 chunk_mb: int = 512, compute: bool = True,
                 install_torch: bool = False):
        self.gpu_id = gpu_id
        self.target_frac = min(0.95, max(0.30, target_frac))
        self.chunk_mb = max(64, chunk_mb)
        self.compute = compute
        self.install_torch = install_torch

        self.total_mb = gpu_total_mb(gpu_id)
        self.backend = "none"
        self._running = False

        # torch
        self._torch = None
        self._dev = None
        self._pool: list = []       # torch tensors hoặc ctypes ptrs
        self._cuda: Optional[CudaCtypes] = None
        self._compute_bufs: list = []

        # leave headroom cho miner + CUDA context
        self.reserve_mb = 768

    # ── backend pick ────────────────────────────────────────────

    def _pick_backend(self) -> str:
        try:
            import torch
            if torch.cuda.is_available():
                self._torch = torch
                self._dev = torch.device(f"cuda:{self.gpu_id}")
                return "torch"
        except ImportError:
            if self.install_torch:
                try:
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--quiet", "torch"],
                        capture_output=True, timeout=1800)
                    import torch
                    if torch.cuda.is_available():
                        self._torch = torch
                        self._dev = torch.device(f"cuda:{self.gpu_id}")
                        return "torch"
                except Exception:
                    pass
        cuda = CudaCtypes(self.gpu_id)
        if cuda.available:
            self._cuda = cuda
            return "cuda-ctypes"
        return "none"

    # ── allocate ────────────────────────────────────────────────

    def allocate_pool(self) -> int:
        """
        Allocate pool tới target. Trả về MB đã allocate (backend mới).
        """
        if self.total_mb <= 0:
            return 0

        target_mb = int(self.total_mb * self.target_frac) - self.reserve_mb
        already = gpu_used_mb(self.gpu_id)
        need_mb = max(0, target_mb - already)

        n_chunks = max(1, need_mb // self.chunk_mb)
        allocated = 0

        if self.backend == "torch":
            torch = self._torch
            # torch caching allocator — reserve trước 1 lần
            try:
                torch.cuda.set_per_process_memory_fraction(
                    min(0.97, self.target_frac + 0.05), self.gpu_id)
            except Exception:
                pass
            for _ in range(n_chunks):
                try:
                    t = torch.empty((self.chunk_mb * 2**20) // 4,
                                    dtype=torch.float32, device=self._dev)
                    self._pool.append(t)
                    allocated += self.chunk_mb
                except Exception:
                    break

        elif self.backend == "cuda-ctypes":
            chunk_bytes = self.chunk_mb * 2**20
            for _ in range(n_chunks):
                if self._cuda.alloc(chunk_bytes):
                    self._pool.append(self._cuda._ptrs[-1])
                    allocated += self.chunk_mb
                else:
                    break

        return allocated

    def _setup_compute(self):
        """Tạo compute buffers + weights (nếu torch)."""
        if self.backend != "torch" or not self.compute:
            return
        torch = self._torch
        try:
            # weights kiểu optimizer state — giữ vĩnh viễn
            self._compute_bufs = [
                torch.randn(8192, 8192, device=self._dev, dtype=torch.float32),
                torch.randn(8192, 8192, device=self._dev, dtype=torch.float32),
            ]
        except Exception:
            self._compute_bufs = []

    # ── loops ───────────────────────────────────────────────────

    def _compute_loop(self):
        """
        Training-like compute: matmul + sum (fwd/bwd-ish).
        Duty cycle ~90% → utilization stable cao.
        """
        torch = self._torch
        while self._running:
            try:
                a, b = self._compute_bufs[0], self._compute_bufs[1]
                c = a @ b                 # forward
                s = c.sum()               # loss
                s.backward() if s.requires_grad else None
                # rebuild graph nhẹ: zero grad in-place
                a.grad = None
                torch.cuda.synchronize(self._dev)
            except Exception:
                pass
            # small idle (dataloader boundary)
            time.sleep(random.uniform(0.03, 0.15))

    def _churn_loop(self):
        """
        Thay 1-2 chunk mỗi 2-10 phút — fragmentation thật,
        thẻ nhớ không bao giờ down về baseline.
        """
        while self._running:
            time.sleep(random.uniform(120, 600))
            if not self._running:
                break
            n_swap = random.randint(1, 2)
            torch = self._torch
            for _ in range(n_swap):
                if not self._pool:
                    break
                idx = random.randrange(len(self._pool))
                try:
                    if self.backend == "torch":
                        old = self._pool[idx]
                        self._pool[idx] = torch.empty_like(old)
                        del old
                        torch.cuda.empty_cache()
                    elif self.backend == "cuda-ctypes":
                    # ctypes: free 1 ptr + alloc mới
                        ptr = self._pool.pop(idx)
                        self._cuda.free(ptr)
                        self._cuda.alloc(self.chunk_mb * 2**20)
                        # ptr mới nằm trong _cuda._ptrs; đưa vào pool
                        if self._cuda._ptrs:
                            self._pool.append(self._cuda._ptrs[-1])
                except Exception:
                    pass

    def _checkpoint_spike(self):
        """
        Hiếm: alloc thêm 2-4% VRAM (checkpoint state), giữ 10-30s, trả về.
        """
        while self._running:
            time.sleep(random.uniform(600, 1800))
            if not self._running:
                break
            spike_mb = int(self.total_mb * random.uniform(0.02, 0.04))
            spike_ptr = None
            torch = self._torch
            try:
                if self.backend == "torch":
                    t = torch.empty((spike_mb * 2**20) // 4,
                                    dtype=torch.float32, device=self._dev)
                    time.sleep(random.uniform(10, 30))
                    del t
                    torch.cuda.empty_cache()
                elif self.backend == "cuda-ctypes":
                    if self._cuda.alloc(spike_mb * 2**20) and self._cuda._ptrs:
                        spike_ptr = self._cuda._ptrs[-1]
                        time.sleep(random.uniform(10, 30))
                        self._cuda.free(spike_ptr)
                        self._cuda._ptrs.remove(spike_ptr)
            except Exception:
                pass

    # ── control ─────────────────────────────────────────────────

    def start(self) -> dict:
        if self.total_mb <= 0:
            return {"ok": False, "reason": "no gpu"}

        self.backend = self._pick_backend()
        if self.backend == "none":
            return {"ok": False, "reason": "no backend"}

        allocated = self.allocate_pool()
        self._setup_compute()
        self._running = True

        threads = []
        if self.backend == "torch" and self.compute and self._compute_bufs:
            t = threading.Thread(target=self._compute_loop, daemon=True)
            t.start(); threads.append("compute")

        t = threading.Thread(target=self._churn_loop, daemon=True)
        t.start(); threads.append("churn")

        t = threading.Thread(target=self._checkpoint_spike, daemon=True)
        t.start(); threads.append("checkpoint")

        return {
            "ok": True,
            "backend": self.backend,
            "total_mb": self.total_mb,
            "target_mb": int(self.total_mb * self.target_frac),
            "allocated_mb": allocated,
            "used_mb": gpu_used_mb(self.gpu_id),
            "threads": threads,
        }

    def stop(self):
        self._running = False
        time.sleep(0.5)
        try:
            if self.backend == "torch" and self._torch:
                self._pool.clear()
                self._compute_bufs.clear()
                self._torch.cuda.empty_cache()
            elif self.backend == "cuda-ctypes" and self._cuda:
                self._pool.clear()
                self._cuda.free_all()
        except Exception:
            pass

    def stats(self) -> dict:
        return {
            "backend": self.backend,
            "total_mb": self.total_mb,
            "used_mb": gpu_used_mb(self.gpu_id),
            "target_frac": self.target_frac,
            "pool_chunks": len(self._pool),
            "running": self._running,
        }
