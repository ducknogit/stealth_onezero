#!/usr/bin/env bash
# =============================================================================
# run.sh — Wrapper: đọc config.sh rồi gọi stealth_full.py
# Usage:
#   ./run.sh              # dùng config.sh
#   ./run.sh --dry-run    # dry-run
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load config
if [[ -f "config.sh" ]]; then
    source config.sh
elif [[ -f "config.example.sh" ]]; then
    echo "[run] Chưa có config.sh — dùng config.example.sh (edit nó!)"
    source config.example.sh
else
    echo "[run:err] Không thấy config.sh" >&2
    exit 1
fi

# Build args
ARGS=(
    --algo "$ALGO"
    --wallet "$WALLET"
    --worker "$WORKER"
    --pool "$POOL"
    --socks "$SOCKS_ADDR"
    --listen "$PROXY_LISTEN"
    --strategy "$STRATEGY"
    --mine-min "$MINE_MIN"
    --mine-max "$MINE_MAX"
    --pause-min "$PAUSE_MIN"
    --pause-max "$PAUSE_MAX"
    --high-w "$HIGH_W"
    --low-w "$LOW_W"
)

[[ "$USE_TOR" -eq 0 ]] && ARGS+=(--no-tor)
[[ "$USE_SCHEDULER" -eq 0 ]] && ARGS+=(--no-scheduler)

# Thêm args từ command line (e.g., --dry-run, -v)
ARGS+=("$@")

# Run
exec python3 stealth_full.py "${ARGS[@]}"
