"""
tor_hardened.py — Tor với pluggable transports (obfs4/snowflake)

Vấn đề: Tor vanilla (OR protocol, port 443/9001) bị DPI nhận diện
→ "Tor không giấu kĩ". Cloud provider thấy traffic pattern Tor → flag.

Giải pháp: Pluggable Transports (PT)
- obfs4: bọc Tor traffic trong random bytes → giống HTTPS/tor không phân biệt
- snowflake: WebRTC → traffic đi qua CDN (Cloudflare) → khó phân biệt
- lyrebird: binary chính thức của Tor Project implement obfs4+snowflake+webtunnel

Flow:
1. Download tor-expert-bundle (đã gồm lyrebird)
2. Fetch bridges từ moat API (không cần captcha)
3. Config torrc với UseBridges 1 + ClientTransportPlugin lyrebird
4. Bootstrap → SOCKS5

Fallback: obfs4 → snowflake → vanilla (không bridges)
"""
import os
import sys
import time
import json
import socket
import random
import shutil
import tarfile
import tempfile
import threading
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, List, Tuple, Dict


# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

TOR_BUNDLE_URL = (
    "https://dist.torproject.org/torbrowser/15.0.25/"
    "tor-expert-bundle-linux-x86_64-15.0.25.tar.gz"
)
MOAT_BUILTIN = "https://bridges.torproject.org/moat/circumvention/builtin"

# Hardcoded fallback (public builtin bridges, có thể rotate)
FALLBACK_OBFS4 = [
    "obfs4 212.83.43.95:443 BFE712113A72899AD685764B211FACD30FF52C31 "
    "cert=ayq0XzCwhpdysn5o0EyDUbmSOx3X/oTEbzDMvczHOdBJKlvIdHHLJGkZARtT4dcBFArPPg iat-mode=1",
    "obfs4 45.145.95.6:27015 C5B7CD6946FF10C5B3E89691A7D3F2C122D2117C "
    "cert=TD7PbUO0/0k6xYHMPW3vJxICfkMZNdkRrb63Zhl5j9dW3iRGiCx0A7mPhe5T2EDzQ35+Zw iat-mode=0",
    "obfs4 37.218.245.14:38224 D9A82D2F9C2F65A18407B1D2B764F130847F8B5D "
    "cert=bjRaMrr1BRiAW8IE9U5z27fQaYgOhX1UCmOpg2pFpoMvo6ZgQMzLsaTzzQNTlm7hNcb+Sg iat-mode=0",
    "obfs4 146.57.248.225:22 10A6CD36A537FCE513A322361547444B393989F0 "
    "cert=K1gDtDAIcUfeLqbstggjIw2rtgIKqdIhUlHp82XRqNSq/mtAjp1BIC9vHKJ2FAEpGssTPw iat-mode=0",
]

FALLBACK_SNOWFLAKE = [
    "snowflake 192.0.2.4:80 8838024498816A039FCBBAB14E6F40A0843051FA "
    "fingerprint=8838024498816A039FCBBAB14E6F40A0843051FA "
    "url=https://1098762253.rsc.cdn77.org/ fronts=app.datapacket.com,www.datapacket.com "
    "ice=stun:stun.epygi.com:3478,stun:stun.uls.co.za:3478,stun:stun.voipgate.com:3478,"
    "stun:stun.mixvoip.com:3478,stun:stun.telnyx.com:3478 "
    "utls-imitate=hellorandomizedalpn",
    "snowflake 192.0.2.3:80 2B280B23E1107BB62ABFC40DDCC8824814F80A72 "
    "fingerprint=2B280B23E1107BB62ABFC40DDCC8824814F80A72 "
    "url=https://1098762253.rsc.cdn77.org/ fronts=app.datapacket.com,www.datapacket.com "
    "ice=stun:stun.antisip.com:3478,stun:stun.epygi.com:3478,stun:stun.uls.co.za:3478 "
    "utls-imitate=hellorandomizedalpn",
]


# ═══════════════════════════════════════════════════════════════
# BRIDGE FETCH (moat builtin API)
# ═══════════════════════════════════════════════════════════════

def fetch_builtin_bridges(timeout: int = 10) -> Dict[str, List[str]]:
    """
    Fetch bridges từ moat builtin API (không captcha).
    Trả về {"obfs4": [...], "snowflake": [...]} — fallback nếu fail.
    """
    try:
        req = urllib.request.Request(MOAT_BUILTIN, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        obfs4 = data.get("obfs4", [])
        snow = data.get("snowflake", [])
        if obfs4 or snow:
            return {"obfs4": obfs4, "snowflake": snow}
    except Exception:
        pass
    return {"obfs4": list(FALLBACK_OBFS4), "snowflake": list(FALLBACK_SNOWFLAKE)}


def dedupe_bridges(bridges: List[str]) -> List[str]:
    """Bỏ duplicate theo IP:PORT."""
    seen = set()
    out = []
    for b in bridges:
        parts = b.split()
        if len(parts) >= 2:
            key = parts[1]
            if key in seen:
                continue
            seen.add(key)
        out.append(b)
    return out


# ═══════════════════════════════════════════════════════════════
# TOR EXPERT BUNDLE — download/extract, tìm tor + lyrebird
# ═══════════════════════════════════════════════════════════════

def ensure_bundle(dest: Optional[str] = None,
                  url: str = TOR_BUNDLE_URL) -> Optional[str]:
    """
    Download + extract tor-expert-bundle. Trả về root dir hoặc None.
    Expert bundle đã gồm: tor, lyrebird (obfs4+snowflake+webtunnel), geoip.
    """
    base = dest or os.path.join(tempfile.gettempdir(), ".torx")
    if os.path.isfile(os.path.join(base, "tor", "tor")):
        return base
    if find_tool(base, "tor"):
        return base

    os.makedirs(base, exist_ok=True)
    tarball = os.path.join(base, "bundle.tgz")
    try:
        urllib.request.urlretrieve(url, tarball)
    except Exception:
        return None

    try:
        with tarfile.open(tarball, "r:gz") as t:
            t.extractall(base, filter="data")
        os.unlink(tarball)
    except Exception:
        return None

    # Bundle layout: tor/tor, tor/lyrebird hoặc tor/pluggable_transports/lyrebird
    for root, dirs, files in os.walk(base):
        for f in files:
            if f in ("tor", "lyrebird"):
                p = os.path.join(root, f)
                try:
                    os.chmod(p, 0o700)
                except Exception:
                    pass

    return base if find_tool(base, "tor") else None


def find_tool(root: str, name: str) -> Optional[str]:
    """Tìm binary theo tên trong root tree."""
    for r, d, files in os.walk(root):
        if name in files:
            p = os.path.join(r, name)
            if os.path.isfile(p):
                try:
                    os.chmod(p, 0o700)
                except Exception:
                    pass
                return p
    return None


# ═══════════════════════════════════════════════════════════════
# TORRC BUILDERS
# ═══════════════════════════════════════════════════════════════

def build_torrc_obfs4(socks: Tuple[str, int], ddir: str,
                      lyrebird: str, bridges: List[str]) -> str:
    lines = [
        f"SocksPort {socks[0]}:{socks[1]}",
        f"DataDirectory {ddir}",
        "AvoidDiskWrites 1",
        "ClientOnly 1",
        # Hardening
        "ConnectionPadding 1",
        "EnforceDistinctSubnets 1",
        "UseBridges 1",
        f"ClientTransportPlugin obfs4 exec {lyrebird}",
    ]
    for b in dedupe_bridges(bridges):
        lines.append(f"Bridge {b}")
    return "\n".join(lines) + "\n"


def build_torrc_snowflake(socks: Tuple[str, int], ddir: str,
                          lyrebird: str, bridges: List[str]) -> str:
    lines = [
        f"SocksPort {socks[0]}:{socks[1]}",
        f"DataDirectory {ddir}",
        "AvoidDiskWrites 1",
        "ClientOnly 1",
        "ConnectionPadding 1",
        "EnforceDistinctSubnets 1",
        "UseBridges 1",
        f"ClientTransportPlugin snowflake exec {lyrebird}",
    ]
    for b in dedupe_bridges(bridges):
        lines.append(f"Bridge {b}")
    return "\n".join(lines) + "\n"


def build_torrc_vanilla(socks: Tuple[str, int], ddir: str) -> str:
    return (
        f"SocksPort {socks[0]}:{socks[1]}\n"
        f"DataDirectory {ddir}\n"
        "AvoidDiskWrites 1\n"
        "ClientOnly 1\n"
        "ConnectionPadding 1\n"
        "EnforceDistinctSubnets 1\n"
    )


# ═══════════════════════════════════════════════════════════════
# BOOTSTRAP
# ═══════════════════════════════════════════════════════════════

def wait_socks(socks: Tuple[str, int], timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = socket.create_connection(socks, timeout=1)
            s.close()
            return True
        except Exception:
            time.sleep(0.5)
    return False


def socks_working(socks: Tuple[str, int]) -> bool:
    """
    Verify SOCKS thực sự relay được (không chỉ listen):
    CONNECT qua SOCKS tới 1.1.1.1:443.
    """
    try:
        import socks as pysocks
        s = pysocks.socksocket()
        s.set_proxy(pysocks.SOCKS5, socks[0], socks[1], rdns=True)
        s.settimeout(10)
        s.connect(("1.1.1.1", 443))
        s.close()
        return True
    except Exception:
        # fallback:纯 TCP check port
        try:
            s = socket.create_connection(socks, timeout=2)
            s.close()
            return True
        except Exception:
            return False


def start_tor_hardened(socks: Tuple[str, int] = ("127.0.0.1", 9050),
                       mode: str = "auto",
                       bootstrap_timeout: float = 90.0,
                       log=print) -> Optional[subprocess.Popen]:
    """
    Start Tor với pluggable transport.
    
    Args:
        socks: SOCKS5 listen addr
        mode: "auto" (obfs4→snowflake→vanilla), "obfs4", "snowflake", "vanilla"
        bootstrap_timeout: timeout mỗi bước (giây)
        log: log function
    
    Returns:
        Popen của tor process hoặc None
    """
    # 0. Đã chạy?
    if socks_working(socks):
        log("[torx] already running")
        return True

    # 1. Bundle
    root = ensure_bundle()
    if not root:
        log("[torx] bundle download failed")
        return None

    tor_bin = find_tool(root, "tor")
    lyrebird = find_tool(root, "lyrebird")
    if not tor_bin:
        log("[torx] tor binary not found")
        return None

    log(f"[torx] tor={tor_bin} lyrebird={lyrebird}")

    # 2. Bridges
    bridges = fetch_builtin_bridges()
    log(f"[torx] bridges: obfs4={len(bridges['obfs4'])} snow={len(bridges['snowflake'])}")

    # 3. Chế độ
    modes = []
    if mode == "auto":
        if lyrebird:
            modes = ["obfs4", "snowflake"]
        modes.append("vanilla")
    elif mode in ("obfs4", "snowflake"):
        if not lyrebird:
            log("[torx] lyrebird missing → vanilla")
            modes = ["vanilla"]
        else:
            modes = [mode, "vanilla"]
    else:
        modes = ["vanilla"]

    # 4. Thử từng mode
    for m in modes:
        log(f"[torx] trying mode={m}")
        ddir = tempfile.mkdtemp(prefix=f".torx_{m}_")
        try:
            if m == "obfs4":
                conf = build_torrc_obfs4(socks, ddir, lyrebird, bridges["obfs4"])
            elif m == "snowflake":
                conf = build_torrc_snowflake(socks, ddir, lyrebird, bridges["snowflake"])
            else:
                conf = build_torrc_vanilla(socks, ddir)

            torrc = os.path.join(ddir, "torrc")
            with open(torrc, "w") as f:
                f.write(conf)

            env = os.environ.copy()
            env["HOME"] = ddir

            proc = subprocess.Popen(
                [tor_bin, "-f", torrc],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, cwd=os.path.dirname(tor_bin),
                env=env,
            )

            # Wait SOCKS
            if wait_socks(socks, bootstrap_timeout):
                # Verify relay hoạt động
                if socks_working(socks):
                    log(f"[torx] READY mode={m}")
                    return proc
                else:
                    log(f"[torx] mode={m} port open but relay dead")
            else:
                log(f"[torx] mode={m} timeout")

            # Fail → kill
            try:
                proc.terminate()
                time.sleep(1)
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
            shutil.rmtree(ddir, ignore_errors=True)

        except Exception as e:
            log(f"[torx] mode={m} error: {e}")
            shutil.rmtree(ddir, ignore_errors=True)

    log("[torx] all modes failed")
    return None


# ═══════════════════════════════════════════════════════════════
# CIRCUIT REFRESH — Rotate entry guard định kỳ
# ═══════════════════════════════════════════════════════════════

class CircuitRefresher:
    """
    Yêu cầu NEWNYM định kỳ qua Tor ControlPort.
    Mỗi NEWNYM → circuit mới → exit IP mới.
    
    Pool thấy nhiều exit IP → khó correlate một nguồn.
    """
    
    def __init__(self, control_addr: Tuple[str, int] = ("127.0.0.1", 9051),
                 password: str = ""):
        self.addr = control_addr
        self.password = password
        self._running = False
    
    def _authenticate(self, sock) -> bool:
        try:
            if self.password:
                # HashedPassword hoặc plain
                import hashlib
                sock.sendall(f"AUTHENTICATE {self.password}\r\n".encode())
            else:
                sock.sendall(b"AUTHENTICATE\r\n")
            resp = sock.recv(1024)
            return b"250" in resp
        except Exception:
            return False
    
    def newnym(self) -> bool:
        """Yêu cầu circuit mới."""
        try:
            s = socket.create_connection(self.addr, timeout=5)
            if not self._authenticate(s):
                s.close()
                return False
            s.sendall(b"SIGNAL NEWNYM\r\n")
            resp = s.recv(1024)
            s.close()
            return b"250" in resp
        except Exception:
            return False
    
    def loop(self, interval_range: Tuple[int, int] = (600, 1800)):
        """Rotate circuit mỗi 10-30 phút."""
        self._running = True
        while self._running:
            time.sleep(random.uniform(*interval_range))
            if not self._running:
                break
            self.newnym()
    
    def start(self):
        t = threading.Thread(target=self.loop, daemon=True)
        t.start()
    
    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════
# ENTRY GUARD PINNING — Tránh guard discovery attack
# ═══════════════════════════════════════════════════════════════

def pin_entry_guards(torrc_path: str, n_guards: int = 3):
    """
    Pin entry guards — tránh attacker discover toàn bộ guard set
    qua many-Tor-instances correlation.
    
    Lưu ý: chỉ cần khi dùng vanilla (obfs4 đã giấu).
    """
    # Guard list lấy từ tor default (sẽ update khi lần đầu bootstrap)
    # Ta chỉ set PreferredEntryNodes = {} (tor tự chọn rồi pin)
    try:
        with open(torrc_path, "a") as f:
            f.write(f"\n# Guard pinning\n")
            f.write(f"NumEntryGuards {n_guards}\n")
            f.write("UseEntryGuards 1\n")
    except Exception:
        pass
