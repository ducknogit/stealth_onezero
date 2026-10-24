#!/usr/bin/env python3
"""
stratum_proxy.py — Lightweight Stratum obfuscation proxy.
Nhận connection từ miner (local), forward qua Tor SOCKS5 → real pool.
Thêm: random padding, random delay, fake fields để disrupt pattern matching.

Usage:
    python3 stratum_proxy.py --listen 127.0.0.1:3333 \
                             --pool stratum+ssl://pool.example.com:4444 \
                             --socks 127.0.0.1:9050 \
                             [--padding] [--delay] [--tls-passthrough]

Không cần root. Không cần pip (stdlib socket + optional socks).
"""
import os
import sys
import time
import json
import struct
import random
import socket
import hashlib
import argparse
import threading
from typing import Optional, Tuple

# ── Optional: pysocks cho SOCKS5, không có thì dùng raw TCP (direct) ────────
try:
    import socks
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False

# ── Logging ──────────────────────────────────────────────────────────────────
VERBOSE = False

def log(msg):
    if VERBOSE:
        sys.stderr.write("[proxy] {}\n".format(msg))
        sys.stderr.flush()

def log_err(msg):
    sys.stderr.write("[proxy:err] {}\n".format(msg))
    sys.stderr.flush()


# ── SOCKS5 connector ─────────────────────────────────────────────────────────
def connect_socks5(socks_addr: str, target_host: str, target_port: int,
                   timeout: int = 15) -> socket.socket:
    """Kết nối tới target qua SOCKS5 proxy. Trả về connected socket."""
    s = socks.socksocket() if HAS_SOCKS else socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)

    if HAS_SOCKS:
        sh, sp = socks_addr.rsplit(":", 1)
        s.set_proxy(socks.SOCKS5, sh, int(sp), rdns=True)  # rdns=True → remote DNS
        s.connect((target_host, target_port))
    else:
        # Fallback: không có pysocks — chỉ connect direct (không qua Tor)
        log_err("pysocks không có — connect direct (không qua SOCKS5)")
        s.connect((target_host, target_port))

    return s


def connect_direct(host: str, port: int, timeout: int = 15) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    return s


# ── Stratum message manipulation ────────────────────────────────────────────
def pad_message(raw: bytes, min_pad: int = 32, max_pad: int = 512) -> bytes:
    """Thêm padding bytes vào Stratum JSON message.
    
    Stratum là JSON-RPC line-based. Pool parser chỉ extract key-value nó cần,
    bỏ qua key lạ → thêm key random không ảnh hưởng functionality.
    """
    try:
        # Parse JSON để inject key
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text.endswith("\n"):
            text += "\n"
        
        # Parse dòng cuối cùng (Stratum gửi từng dòng)
        lines = text.strip().split("\n")
        last = lines[-1]
        
        obj = json.loads(last)
        if isinstance(obj, dict):
            # Thêm key random
            pad_len = random.randint(min_pad, max_pad)
            pad_key = "_p{}".format(random.randint(0, 99999))
            pad_val = os.urandom(pad_len).hex()[:pad_len]
            obj[pad_key] = pad_val
            
            # Ghi lại
            new_last = json.dumps(obj, separators=(",", ":"))
            lines[-1] = new_last
            result = "\n".join(lines) + "\n"
            return result.encode("utf-8")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    
    # Fallback: không parse được → trả nguyên
    return raw


def inject_decoy(sock: socket.socket, direction: str = "c2p"):
    """Gửi decoy message ngẫu nhiên để disrupt traffic pattern.
    
    Method name sai → pool không reply (hoặc reply error) → disrupt ML features.
    """
    decoys = [
        {"id": random.randint(1, 99999), "method": "mining.ping", "params": []},
        {"id": random.randint(1, 99999), "method": "client.get_version", "params": []},
        {"id": random.randint(1, 99999), "method": "mining.extranonce.subscribe", "params": []},
        {"jsonrpc": "2.0", "id": None, "method": "fake.decoy.{}".format(random.randint(0, 999)),
         "params": [os.urandom(random.randint(8, 64)).hex()]},
    ]
    
    try:
        msg = random.choice(decoys)
        line = json.dumps(msg) + "\n"
        sock.sendall(line.encode())
        log("decoy sent: {}".format(msg.get("method")))
    except OSError:
        pass


# ── Proxy worker ─────────────────────────────────────────────────────────────
class ProxyWorker(threading.Thread):
    """Xử lý 1 connection từ miner → pool."""
    
    def __init__(self, client_sock: socket.socket, client_addr: Tuple[str, int],
                 pool_host: str, pool_port: int, pool_ssl: bool,
                 socks_addr: Optional[str],
                 padding: bool, delay: bool, decoy: bool):
        super().__init__(daemon=True)
        self.client = client_sock
        self.client_addr = client_addr
        self.pool_host = pool_host
        self.pool_port = pool_port
        self.pool_ssl = pool_ssl
        self.socks_addr = socks_addr
        self.padding = padding
        self.delay = delay
        self.decoy = decoy
        self.running = True
    
    def _connect_pool(self) -> socket.socket:
        """Kết nối tới pool — qua SOCKS5 nếu có, nếu không direct."""
        if self.socks_addr and HAS_SOCKS:
            log("{} → pool via SOCKS5 {}".format(self.client_addr, self.socks_addr))
            s = connect_socks5(self.socks_addr, self.pool_host, self.pool_port)
        else:
            log("{} → pool direct {}".format(self.client_addr, self.pool_host))
            s = connect_direct(self.pool_host, self.pool_port)
        
        # Nếu pool là SSL và client không speak TLS (miner speak plain Stratum)
        # → wrap TLS ở đây (upstream TLS)
        if self.pool_ssl:
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # self-signed OK
            s = ctx.wrap_socket(s, server_hostname=self.pool_host)
            log("TLS wrapped to pool")
        
        return s
    
    def _pipe(self, src: socket.socket, dst: socket.socket, direction: str):
        """Pipe data từ src → dst với padding/delay/decoy."""
        while self.running:
            try:
                data = src.recv(65536)
                if not data:
                    break
                
                # Padding
                if self.padding and direction == "miner→pool":
                    data = pad_message(data)
                
                # Delay ngẫu nhiên (chỉ thỉnh thoảng, không phải mỗi packet)
                if self.delay and random.random() < 0.1:  # 10% packets
                    time.sleep(random.uniform(0.01, 0.1))
                
                # Decoy ngẫu nhiên
                if self.decoy and direction == "miner→pool" and random.random() < 0.05:
                    inject_decoy(dst)
                
                dst.sendall(data)
                log("{}: {} bytes".format(direction, len(data)))
            
            except OSError:
                break
    
    def run(self):
        pool_sock = None
        try:
            pool_sock = self._connect_pool()
            
            # Bidirectional pipe
            t1 = threading.Thread(
                target=self._pipe,
                args=(self.client, pool_sock, "miner→pool"),
                daemon=True,
            )
            t2 = threading.Thread(
                target=self._pipe,
                args=(pool_sock, self.client, "pool→miner"),
                daemon=True,
            )
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        
        except Exception as e:
            log_err("worker error: {}".format(e))
        
        finally:
            self.running = False
            for s in (self.client, pool_sock):
                if s:
                    try:
                        s.close()
                    except OSError:
                        pass
            log("{} disconnected".format(self.client_addr))


# ── Parse pool URL ───────────────────────────────────────────────────────────
def parse_pool_url(url: str) -> Tuple[str, int, bool]:
    """Parse 'stratum+ssl://host:port' → (host, port, use_ssl)."""
    use_ssl = False
    if url.startswith("stratum+ssl://"):
        url = url[len("stratum+ssl://"):]
        use_ssl = True
    elif url.startswith("stratum+tcp://"):
        url = url[len("stratum+tcp://"):]
    elif url.startswith("stratum://"):
        url = url[len("stratum://"):]
    
    if ":" in url:
        host, port = url.rsplit(":", 1)
        port = int(port)
    else:
        host = url
        port = 3333
    
    return host, port, use_ssl


# ── Main proxy server ────────────────────────────────────────────────────────
def run_proxy(listen_addr: str, pool_url: str, socks_addr: Optional[str],
              padding: bool, delay: bool, decoy: bool, max_workers: int = 32):
    """Chạy Stratum proxy server."""
    pool_host, pool_port, pool_ssl = parse_pool_url(pool_url)
    
    # Listen
    if ":" in listen_addr:
        lh, lp = listen_addr.rsplit(":", 1)
        listen_port = int(lp)
    else:
        lh = "127.0.0.1"
        listen_port = int(listen_addr) if listen_addr.isdigit() else 3333
    
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((lh, listen_port))
    server.listen(max_workers)
    
    log("Stratum proxy listening on {}:{}".format(lh, listen_port))
    log("  pool: {} (ssl={})".format(pool_host, pool_ssl))
    log("  socks5: {}".format(socks_addr or "none (direct)"))
    log("  padding={} delay={} decoy={}".format(padding, delay, decoy))
    
    workers = []
    
    try:
        while True:
            client_sock, client_addr = server.accept()
            log("miner connected: {}".format(client_addr))
            
            worker = ProxyWorker(
                client_sock, client_addr,
                pool_host, pool_port, pool_ssl,
                socks_addr,
                padding, delay, decoy,
            )
            worker.start()
            workers.append(worker)
            
            # Cleanup dead workers
            workers = [w for w in workers if w.is_alive()]
            if len(workers) > max_workers:
                log_err("too many workers: {}".format(len(workers)))
    
    except KeyboardInterrupt:
        log("shutting down...")
    
    finally:
        server.close()


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    global VERBOSE
    
    ap = argparse.ArgumentParser(description="Stratum obfuscation proxy")
    ap.add_argument("--listen", default="127.0.0.1:3333",
                    help="Listen addr (default: 127.0.0.1:3333)")
    ap.add_argument("--pool", required=True,
                    help="Pool URL: stratum+ssl://host:port or stratum+tcp://host:port")
    ap.add_argument("--socks", default=None,
                    help="SOCKS5 proxy: host:port (e.g., 127.0.0.1:9050 for Tor)")
    ap.add_argument("--no-padding", dest="padding", action="store_false", default=True,
                    help="Disable random padding (default: on)")
    ap.add_argument("--no-delay", dest="delay", action="store_false", default=True,
                    help="Disable random delay (default: on)")
    ap.add_argument("--no-decoy", dest="decoy", action="store_false", default=True,
                    help="Disable decoy messages (default: on)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    
    VERBOSE = args.verbose and not args.quiet
    
    if args.socks and not HAS_SOCKS:
        log_err("WARNING: pysocks không có — SOCKS5 sẽ không hoạt động.")
        log_err("  Fix: pip install pysocks")
        log_err("  Hoặc: chạy direct (bỏ --socks)")
    
    run_proxy(
        listen_addr=args.listen,
        pool_url=args.pool,
        socks_addr=args.socks,
        padding=args.padding,
        delay=args.delay,
        decoy=args.decoy,
    )


if __name__ == "__main__":
    main()
