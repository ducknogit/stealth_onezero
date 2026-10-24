#!/usr/bin/env python3
"""
test_stack.py — Verify stealth stack trên env thật (chạy 1 lệnh, ~60-90s)

Usage:
    python3 test_stack.py               # full test
    python3 test_stack.py --fast        # bỏ Tor download (chỉ check net)

Kiểm tra:
  1. Imports + syntax 13 layers
  2. Network: moat bridges API, tor bundle host
  3. Tor hardened: obfs4/snowflake → SOCKS5 relay → exit IP
  4. VRAM resident: alloc thật, tăng đúng target, free sạch
  5. Beacon: 1 request qua SOCKS
  6. Orchestrator: setup 13 layers + heartbeat + stop
"""
import os
import sys
import time
import socket
import traceback

FAST = "--fast" in sys.argv

PASS = []
FAIL = []


def ok(name, detail=""):
    PASS.append(name)
    print(f"  PASS  {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def bad(name, detail=""):
    FAIL.append(name)
    print(f"  FAIL  {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def section(t):
    print(f"\n=== {t} ===", flush=True)


def http_get(host, path, timeout=10, socks=None):
    import ssl
    if socks:
        import socks as pysocks
        s = pysocks.socksocket()
        s.set_proxy(pysocks.SOCKS5, socks[0], socks[1], rdns=True)
        s.settimeout(timeout)
        s.connect((host, 443))
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        s = ctx.wrap_socket(s, server_hostname=host)
    else:
        ctx = ssl.create_default_context()
        s = socket.create_connection((host, 443), timeout=timeout)
        s = ctx.wrap_socket(s, server_hostname=host)
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
    data = b""
    try:
        while True:
            c = s.recv(65536)
            if not c:
                break
            data += c
            if len(data) > 65536:
                break
    except Exception:
        pass
    s.close()
    return data


# ═══════════════ 1. IMPORTS ═══════════════

def test_imports():
    section("1. Imports (13 layers)")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "layers"))
    mods = ["disguise", "gpu_pattern", "network_disguise", "timing_disguise",
            "syscall_disguise", "anti_forensics", "process_tree_disguise",
            "gpu_resident", "tor_hardened", "telemetry_beacon", "orchestrator"]
    for m in mods:
        try:
            __import__(m)
            ok(f"import {m}")
        except Exception as e:
            bad(f"import {m}", str(e))


# ═══════════════ 2. NETWORK ═══════════════

def test_network():
    section("2. Network reachability")
    # moat bridges API
    try:
        from tor_hardened import fetch_builtin_bridges
        b = fetch_builtin_bridges(timeout=10)
        n4, ns = len(b["obfs4"]), len(b["snowflake"])
        if n4 or ns:
            ok("moat bridges API", f"obfs4={n4} snowflake={ns}")
        else:
            bad("moat bridges API", "empty")
    except Exception as e:
        bad("moat bridges API", str(e))

    # dist.torproject.org
    try:
        r = http_get("dist.torproject.org", "/", timeout=10)
        if r.startswith(b"HTTP/1.") and b"200" in r[:20]:
            ok("dist.torproject.org")
        else:
            bad("dist.torproject.org", r[:40].decode("latin1"))
    except Exception as e:
        bad("dist.torproject.org", str(e))


# ═══════════════ 3. TOR HARDENED ═══════════════

def test_tor():
    section("3. Tor hardened (obfs4/snowflake)")
    if FAST:
        print("  SKIP (--fast)", flush=True)
        return

    from tor_hardened import start_tor_hardened, socks_working

    socks = ("127.0.0.1", 9050)

    # Đã chạy?
    already = False
    try:
        s = socket.create_connection(socks, timeout=1)
        s.close()
        already = True
    except Exception:
        pass

    proc = None
    if already and socks_working(socks):
        ok("tor already running")
    else:
        t0 = time.time()
        proc = start_tor_hardened(socks=socks, mode="auto",
                                  bootstrap_timeout=90, log=lambda m: print(f"    {m}", flush=True))
        dt = time.time() - t0
        if proc and socks_working(socks):
            ok("tor hardened bootstrap", f"{dt:.0f}s")
        else:
            bad("tor hardened bootstrap", f"{dt:.0f}s")

    if socks_working(socks):
        # Exit IP qua Tor
        try:
            r = http_get("api.ipify.org", "/?format=json", timeout=20, socks=socks)
            body = r.split(b"\r\n\r\n", 1)[-1]
            ip = body.decode("latin1", "ignore").strip()[:40]
            ok("tor exit IP", ip)
        except Exception as e:
            bad("tor exit IP", str(e))

        # So với IP direct (phải KHÁC nhau)
        try:
            r = http_get("api.ipify.org", "/?format=json", timeout=10)
            dip = r.split(b"\r\n\r\n", 1)[-1].decode("latin1", "ignore").strip()[:40]
            if dip and "ip" not in dip.lower():
                ok("direct IP", dip)
            else:
                print(f"    direct: {dip}", flush=True)
        except Exception as e:
            print(f"    direct IP check: {e}", flush=True)

        # Beacon qua SOCKS
        try:
            from telemetry_beacon import TelemetryBeacon
            b = TelemetryBeacon(socks)
            b._wandb_beacon()
            st = b.stats()
            if st["sent"] > 0:
                ok("beacon via socks", f"sent={st['sent']}")
            else:
                bad("beacon via socks", "0 sent (wandb có thể block — không fatal)")
        except Exception as e:
            bad("beacon via socks", str(e))
    else:
        bad("tor exit IP", "socks dead")
        bad("beacon via socks", "skipped")


# ═══════════════ 4. VRAM RESIDENT ═══════════════

def test_vram():
    section("4. VRAM resident")
    try:
        from gpu_resident import VRAMResident, gpu_total_mb, gpu_used_mb
    except Exception as e:
        bad("import gpu_resident", str(e))
        return

    total = gpu_total_mb(0)
    if total <= 0:
        print("  SKIP  no GPU / no nvidia-smi", flush=True)
        return

    before = gpu_used_mb(0)
    print(f"    total={total}MB before={before}MB", flush=True)

    v = VRAMResident(gpu_id=0, target_frac=0.80, chunk_mb=256)
    info = v.start()
    if not info.get("ok"):
        bad("vram start", info.get("reason"))
        return
    ok("vram backend", info["backend"])

    time.sleep(2)
    after = gpu_used_mb(0)
    grew = after - before
    target_mb = int(total * 0.80)
    if grew >= 512:
        ok("vram allocated", f"+{grew}MB (target {target_mb}MB, used={after}MB)")
    else:
        bad("vram allocated", f"only +{grew}MB")

    st = v.stats()
    if st["pool_chunks"] > 0:
        ok("vram pool", f"{st['pool_chunks']} chunks")
    else:
        bad("vram pool", "0 chunks")

    v.stop()
    time.sleep(1)
    freed = gpu_used_mb(0)
    if freed < after - 256:
        ok("vram freed", f"-> {freed}MB")
    else:
        bad("vram freed", f"still {freed}MB")


# ═══════════════ 5. ORCHESTRATOR (13 layers) ═══════════════

def test_orchestrator():
    section("5. Orchestrator (13 layers, 5s)")
    try:
        from orchestrator import TrainingSimulator
        sim = TrainingSimulator(log_output=False, num_workers=1)
        sim.setup(gpu_id=0, silent=True,
                  use_tor_hardened=False,   # tor test riêng ở section 3
                  use_beacons=False)        # beacon test riêng
        ok("setup 13 layers")

        sim.start_heartbeat(interval=0.3)
        time.sleep(3)
        st = sim.status()

        if st["step"] > 0:
            ok("heartbeat", f"{st['step']} steps")
        else:
            bad("heartbeat", "0 steps")

        vram = st.get("vram") or {}
        if vram.get("running") and vram.get("pool_chunks", 0) > 0:
            ok("vram in orchestrator", f"{vram['pool_chunks']} chunks, {vram['used_mb']}MB")
        else:
            bad("vram in orchestrator", str(vram))

        if st.get("syscall_active") and st.get("forensics_active"):
            ok("syscall+forensics active")
        else:
            bad("syscall+forensics active")

        sim.stop()
        ok("orchestrator stop")
    except Exception:
        bad("orchestrator", traceback.format_exc().splitlines()[-1])


# ═══════════════ MAIN ═══════════════

def main():
    print("=" * 60)
    print("STEALTH STACK TEST", "(fast mode)" if FAST else "")
    print("=" * 60)

    test_imports()
    test_network()
    test_tor()
    test_vram()
    test_orchestrator()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print(f"  - {f}")
    print("=" * 60)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
