"""
telemetry_beacon.py — Layer 6b: Fake ML telemetry traffic

Vấn đề: Stratum traffic có pattern đặc trưng (submit đều, size đều).
Kể cả qua Tor, exit→pool leg vẫn thấy pattern.

Giải pháp: TEARML BEACON — gửi/nhận traffic giả như ML workload:
- WandB metric POST (mỗi 15-60s, body 2-8KB)
- HuggingFace model download GET (hiếm, lớn 10-100MB)
- S3/GCS checkpoint upload (hiếm, 50-500KB)
- GPyTorch/DeterminedAI health check (rất nhỏ, đều)

Tất cả qua cùng SOCKS5 → cùng Tor circuit → exit thấy
"client này đang train + telemetrize", không chỉ stratum.

Quan trọng: beacon dùng TLS browser-like → JA3 giống browser,
không giống Python/Go mining client.
"""
import os
import sys
import time
import json
import random
import socket
import ssl
import struct
import threading
from typing import Optional, Tuple, List


# ═══════════════════════════════════════════════════════════════
# FAKE TELEMETRY PAYLOADS
# ═══════════════════════════════════════════════════════════════

MODELS = ["resnet50", "llama-7b", "gpt2", "vit-large", "convnext-huge",
          "whisper-large", "sam-vit", "deit3-large"]

FAKE_WANDB_METRICS = {
    "_runtime": lambda: round(time.time() % 100000, 1),
    "_timestamp": lambda: time.time(),
    "_step": lambda: random.randint(0, 500000),
    "train/loss": lambda: round(abs(random.gauss(2.0, 0.5)), 5),
    "train/acc": lambda: round(min(0.999, abs(random.gauss(0.7, 0.15))), 5),
    "train/lr": lambda: round(random.choice([1e-4, 3e-4, 1e-3, 5e-4]), 8),
    "gpu_mem_allocated": lambda: round(random.uniform(40, 88), 2),
    "gpu_util": lambda: random.randint(72, 99),
    "data/sec": lambda: random.randint(800, 4096),
}


def build_wandb_payload() -> bytes:
    """WandB-style metric POST body (JSON lines)."""
    lines = []
    n = random.randint(5, 20)
    for _ in range(n):
        m = {k: f() for k, f in FAKE_WANDB_METRICS.items()}
        lines.append(json.dumps(m, separators=(",", ":")))
    body = "\n".join(lines)
    # Pad để size ổn định (không leak pattern)
    pad_to = random.choice([2048, 4096, 8192])
    if len(body) < pad_to:
        body += " " * (pad_to - len(body))
    return body.encode()


def build_hf_headers(model: Optional[str] = None) -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/131.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://huggingface.co/",
        "Origin": "https://huggingface.co",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }


# ═══════════════════════════════════════════════════════════════
# TOR-AWARE HTTP CLIENT
# ═══════════════════════════════════════════════════════════════

class TorHttpClient:
    """
    Minimal HTTP/1.1 client qua SOCKS5 + TLS.
    Browser-like TLS context (Chrome cipher order).
    """

    # Chrome 131 cipher list (thứ tự JA3-ish)
    CHROME_CIPHERS = (
        "TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:"
        "TLS_CHACHA20_POLY1305_SHA256:"
        "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
        "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:"
        "ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:"
        "ECDHE-ECDSA-AES128-SHA256:ECDHE-RSA-AES128-SHA256:"
        "ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA:"
        "ECDHE-ECDSA-AES256-SHA384:ECDHE-RSA-AES256-SHA384:"
        "ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES256-SHA:"
        "AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA256:"
        "AES256-SHA256:AES128-SHA:AES256-SHA"
    )

    def __init__(self, socks: Tuple[str, int], timeout: float = 15.0):
        self.socks = socks
        self.timeout = timeout

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        try:
            ctx.set_ciphers(self.CHROME_CIPHERS)
        except ssl.SSLError:
            pass
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            ctx.set_alpn_protocols(["http/1.1"])  # tránh negotiate h2 (cần HPACK)
        except Exception:
            pass
        return ctx

    def _connect(self, host: str, port: int = 443, tls: bool = True):
        import socks  # pysocks
        s = socks.socksocket()
        s.set_proxy(socks.SOCKS5, self.socks[0], self.socks[1], rdns=True)
        s.settimeout(self.timeout)
        s.connect((host, port))
        if tls:
            ctx = self._ssl_context()
            s = ctx.wrap_socket(s, server_hostname=host)
        return s

    def request(self, method: str, host: str, path: str,
                headers: Optional[dict] = None, body: bytes = b"",
                port: int = 443, tls: bool = True,
                read_max: int = 256 * 1024) -> Optional[bytes]:
        """
        Gửi HTTP request, trả về response body (giới hạn read_max) hoặc None.
        """
        try:
            s = self._connect(host, port, tls)
            hdrs = {
                "Host": host,
                "Connection": "keep-alive",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            }
            if headers:
                hdrs.update(headers)
            if body:
                hdrs["Content-Length"] = str(len(body))

            req = f"{method} {path} HTTP/1.1\r\n"
            for k, v in hdrs.items():
                req += f"{k}: {v}\r\n"
            req += "\r\n"
            s.sendall(req.encode() + body)

            # Read response (tới max)
            data = b""
            end = time.time() + self.timeout
            while time.time() < end and len(data) < read_max:
                try:
                    chunk = s.recv(min(65536, read_max - len(data)))
                    if not chunk:
                        break
                    data += chunk
                    # Đủ headers + có body → dừng sớm cho small requests
                    if b"\r\n\r\n" in data and method == "POST":
                        break
                except socket.timeout:
                    break
            s.close()
            return data if data else None
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════
# BEACON SCHEDULER
# ═══════════════════════════════════════════════════════════════

class TelemetryBeacon:
    """
    Lịch beacon như ML workload thật:
    
    - wandb metrics POST:  every 15-60s  (2-8KB)
    - HF heartbeat GET:     every 60-300s (tiny)
    - checkpoint upload:    every 30-90 min (50-500KB, POST)
    - model shard download: every 15-60 min (1-10MB, GET, stream)
    - determinedai health:  every 30-90s (tiny)
    
    Mọi request qua cùng SOCKS (Tor circuit).
    """

    WANDB_HOSTS = ["api.wandb.ai", "api.core.weave.wandb.ai"]
    HF_HOSTS = ["huggingface.co", "cdn-lfs.huggingface.co"]
    S3_HOSTS = ["s3.amazonaws.com", "storage.googleapis.com"]
    HEALTH_HOSTS = ["api.determined.ai", "mlflow.svc.mlflow.svc.cluster.local"]

    def __init__(self, socks: Tuple[str, int], enabled: bool = True):
        self.client = TorHttpClient(socks)
        self.enabled = enabled
        self._running = False
        self._stats = {"sent": 0, "failed": 0, "bytes": 0}

    # ── individual beacon kinds ─────────────────────────────────

    def _wandb_beacon(self):
        host = random.choice(self.WANDB_HOSTS)
        body = build_wandb_payload()
        path = random.choice([
            "/api/v1/users/testuser/sweeps/x1/graphics",
            "/api/v1/runs",
            "/graphql",
        ])
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": "https://wandb.ai/",
            "Origin": "https://wandb.ai",
        }
        r = self.client.request("POST", host, path, headers, body)
        if r:
            self._stats["sent"] += 1
            self._stats["bytes"] += len(body)
        else:
            self._stats["failed"] += 1

    def _hf_beacon(self):
        host = random.choice(self.HF_HOSTS)
        model = random.choice(MODELS)
        path = f"/api/models/{model}/revision/main"
        headers = {
            "Accept": "application/json",
            "Referer": "https://huggingface.co/models",
        }
        r = self.client.request("GET", host, path, headers, read_max=16384)
        if r:
            self._stats["sent"] += 1
        else:
            self._stats["failed"] += 1

    def _health_beacon(self):
        host = random.choice(self.HEALTH_HOSTS)
        path = random.choice(["/api/v1/health", "/api/health", "/health"])
        r = self.client.request("GET", host, path, read_max=4096)
        if r:
            self._stats["sent"] += 1
        else:
            self._stats["failed"] += 1

    def _checkpoint_upload(self):
        """Fake checkpoint upload — body lớn (50-500KB)."""
        host = random.choice(self.S3_HOSTS)
        size = random.randint(50, 500) * 1024
        body = os.urandom(size)  # random = nén không được, size thật
        path = random.choice([
            "/bucket/checkpoints/step-1000/model.safetensors",
            "/b/ml-jobs/o/runs/r-77/final.pt",
        ])
        headers = {"Content-Type": "application/octet-stream"}
        r = self.client.request("POST", host, path, headers, body,
                                read_max=8192)
        if r:
            self._stats["sent"] += 1
            self._stats["bytes"] += len(body)
        else:
            self._stats["failed"] += 1

    # ── scheduler loop ──────────────────────────────────────────

    def _loop(self):
        # Next-fire times
        t_wandb = 0.0
        t_health = 0.0
        t_hf = time.time() + random.uniform(30, 120)
        t_ckpt = time.time() + random.uniform(1800, 5400)

        while self._running:
            now = time.time()

            if now >= t_wandb:
                self._wandb_beacon()
                t_wandb = now + random.uniform(15, 60)

            if now >= t_health:
                self._health_beacon()
                t_health = now + random.uniform(30, 90)

            if now >= t_hf:
                self._hf_beacon()
                t_hf = now + random.uniform(900, 3600)

            if now >= t_ckpt:
                self._checkpoint_upload()
                t_ckpt = now + random.uniform(1800, 5400)

            time.sleep(random.uniform(2, 5))

    def start(self):
        if not self.enabled or self._running:
            return
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def stop(self):
        self._running = False

    def stats(self) -> dict:
        return dict(self._stats)


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE
# ═══════════════════════════════════════════════════════════════

def start_beacons(socks: Tuple[str, int] = ("127.0.0.1", 9050),
                  enabled: bool = True) -> TelemetryBeacon:
    b = TelemetryBeacon(socks, enabled=enabled)
    b.start()
    return b
