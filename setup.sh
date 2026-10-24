#!/usr/bin/env bash
# =============================================================================
# setup.sh — Pre-flight setup cho stealth_onezero
# Chạy 1 lần trước khi dùng stealth_full.py
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log()  { printf '\033[0;36m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[setup:warn]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[0;32m[setup:ok]\033[0m %s\n' "$*"; }

log "Stealth OneZero Setup"
log "====================="

# 1. Permissions
log "Setting permissions..."
chmod +x stealth_full.py stealth.py stratum_proxy.py load_scheduler.py 2>/dev/null || true
chmod +x stealth.sh stealth-deploy.sh setup.sh 2>/dev/null || true
ok "Permissions set"

# 2. Check Python
log "Checking Python..."
if command -v python3 >/dev/null 2>&1; then
    PY_VER=$(python3 --version 2>&1 | awk '{print $2}')
    ok "Python3 found: $PY_VER"
elif command -v python >/dev/null 2>&1; then
    PY_VER=$(python --version 2>&1 | awk '{print $2}')
    warn "Only python2 found: $PY_VER — một số features có thể không hoạt động"
else
    warn "Không có Python — sẽ dùng bash fallback (stealth.sh)"
fi

# 3. Check pip & pysocks
log "Checking pysocks (optional, cho Tor)..."
if command -v pip3 >/dev/null 2>&1; then
    if python3 -c "import socks" 2>/dev/null; then
        ok "pysocks đã cài"
    else
        log "Cài pysocks..."
        pip3 install --user pysocks 2>/dev/null && ok "pysocks installed" || \
            warn "Không cài được pysocks — Tor sẽ không hoạt động"
    fi
else
    warn "Không có pip3 — bỏ qua pysocks"
fi

# 4. Check miner binary
log "Checking miner binary..."
if [[ -x "bin/onezerominer" ]]; then
    ok "Miner binary: bin/onezerominer"
elif command -v onezerominer >/dev/null 2>&1; then
    ok "Miner binary: $(command -v onezerominer)"
else
    warn "Không thấy onezerominer binary!"
    warn "  → Copy binary vào bin/ hoặc thêm vào \$PATH"
    warn "  → Download từ: https://github.com/EhssanD/onezerominer/releases"
fi

# 5. Check nvidia-smi
log "Checking nvidia-smi (optional, cho GPU control)..."
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    ok "nvidia-smi found: $GPU_NAME"
else
    warn "Không có nvidia-smi — sẽ dùng time-only throttle"
fi

# 6. Check Tor directory
log "Checking Tor data directory..."
TOR_DIR="$HOME/.local/share/stealth-miner"
if [[ -d "$TOR_DIR" && -x "$TOR_DIR/tor/tor" ]]; then
    ok "Tor đã bootstrap: $TOR_DIR"
else
    log "Tor sẽ tự download khi chạy lần đầu (~15MB)"
fi

# 7. Test imports
log "Testing Python imports..."
if command -v python3 >/dev/null 2>&1; then
    python3 -c "import socket, json, argparse, subprocess, threading" 2>/dev/null && \
        ok "Core modules OK" || \
        warn "Thiếu core Python modules"
fi

log ""
log "====================="
log "Setup complete!"
log ""
log "Next steps:"
log "  1. Edit stealth_full.py → điền wallet & pool"
log "  2. Test: python3 stealth_full.py --algo pearlhash --wallet TEST --pool stratum+ssl://pool.example.com:4444 --no-tor --dry-run"
log "  3. Run:  python3 stealth_full.py --algo pearlhash --wallet YOUR_WALLET --pool stratum+ssl://your-pool:port"
log "====================="
