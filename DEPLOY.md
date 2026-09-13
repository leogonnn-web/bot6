# TRIADA Deployment Guide — AWS

## Prerequisites
- AWS EC2 instance (t3.small minimum, t3.medium recommended)
- Docker + Docker Compose installed
- Bybit API key with spot trading permissions

## Quick Start

```bash
# 1. Clone and enter project
git clone <repo-url> && cd bot4-main

# 2. Configure environment
cp .env.example .env
nano .env  # Set real BYBIT_API_KEY, BYBIT_API_SECRET, GRAFANA_PASSWORD

# 3. Set dry_run to false for live trading
# Edit shared/config.json → "dry_run": false

# 4. Launch entire stack
docker compose up -d --build

# 5. Verify all services are healthy
docker compose ps
```

## Services

| Service | Port | URL |
|---------|------|-----|
| HYDRA Bot (Python) | 9090 | `http://host:9090/metrics` (also `/maintenance` — operator IP only) |
| Prometheus | 9092 | `http://host:9092` |
| Grafana | 3000 | `http://host:3000` |

All ports are restricted to `var.your_ip_cidr` in `terraform/main.tf`. The Go arbitrage
engine and scalper were moved to separate repositories (`triada-arb`, `triada-scalper`) on
2026-09-09; if an old `hydra-arb` container is still present on the host, run
`docker compose up -d --remove-orphans`.

## Architecture

```
┌─────────────┐   capital_state.json   ┌────────────────────────────┐
│  HYDRA Bot  │ ─────────────────────► │  triada-arb (separate repo,│
│  (Python)   │   shared Docker volume │  optional, detection only) │
│  :9090      │                        └────────────────────────────┘
└──────┬──────┘
       │ metrics
       ▼
┌──────────────┐        ┌──────────────┐
│  Prometheus  │ ◄───── │              │
│  :9092       │        │   Grafana    │
│              │ ─────► │   :3000      │
└──────────────┘        └──────────────┘
       ▲
       │ health / heartbeat
┌──────────────┐
│  watchdog    │ ── docker.sock ──► restart hydra-bot
└──────────────┘
```

## Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f hydra-bot
docker compose logs -f watchdog
```

## Common Operations

```bash
# Restart bot only
docker compose restart hydra-bot

# Update and rebuild
git pull
docker compose up -d --build

# Stop everything
docker compose down

# Stop + wipe volumes (data reset)
docker compose down -v
```

## Monitoring Alerts
Alerts are configured in `monitoring/alert_rules.yml`:
- **BalanceCriticallyLow** — balance < $15, trading frozen
- **BalanceLow** — balance < $25, grid disabled
- **OrderErrorSpike** — order failures > 0.1/s
- **SlippageHigh** — avg slippage > 0.5%
- **BotStateStuck** — state unchanged for 30min

## Security Checklist
- [ ] `.env` file has real API keys (never commit to git)
- [ ] `GRAFANA_PASSWORD` changed from default
- [ ] EC2 Security Group: 3000, 9090, 9092 restricted to `your_ip_cidr` (terraform/main.tf) — 9090 serves the unauthenticated `POST /maintenance`
- [ ] `terraform apply` re-run after any SG change (the tf file alone changes nothing)
- [ ] Bybit API key has IP whitelist + spot-only permissions
