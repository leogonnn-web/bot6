# TRIADA v5.4 — Absolute Pure
## Master Architecture Specification

**Last Updated:** 2026-05-24  
**Target Runtime:** Python 3.11 + Go 1.22  
**Deployment:** AWS ap-southeast-1 (Singapore), Bare Metal via Terraform  
**Audience:** Autonomous AI Coding Agent (Windsurf Cascade / SWE-agent)

---

## 1. EXECUTIVE SYSTEM ARCHITECTURE

Triada is a split-logic algorithmic trading cluster. The Python Ingress handles strategy, scanning, and capital routing. The Go Arb Engine handles sub-millisecond triangular arbitrage. Both systems share a single source of truth via Docker volume-mounted JSON files.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            TRIADA CLUSTER v5.4                               │
├─────────────────────────────┬───────────────────────────────────────────────┤
│      PYTHON INGRESS         │         GO ARBITRAGE ENGINE                  │
│  (Strategy + Execution)     │     (Ultra-Latency WebSocket Arb)            │
├─────────────────────────────┼───────────────────────────────────────────────┤
│  src/core/bot.py            │  arb-engine/engine/slot.go                    │
│  src/core/scanner.py        │  arb-engine/exchange/bybit_ws.go              │
│  shared/capital_router.py   │  arb-engine/strategy/triangular.go           │
│  shared/order_manager.py    │  arb-engine/bridge/capital.go                 │
│  src/core/health.py         │  arb-engine/ringbuf/spsc.go                  │
│  src/api/bybit_client.py    │  arb-engine/metrics/prom.go                   │
├─────────────────────────────┴───────────────────────────────────────────────┤
│                    SHARED SINGLE SOURCE OF TRUTH                             │
│  shared/capital_state.json  (CapitalRouter writes, Go Arb reads)           │
│  shared/hot_symbols.txt     (Scanner writes, Bot + Arb reads)              │
│  shared/state/trades.db     (SQLite — FIFO PnL, session stats)              │
│  logs/bot.log               (Structured logging with @TAGS@)               │
├─────────────────────────────────────────────────────────────────────────────┤
│                    INFRASTRUCTURE LAYER (Docker)                            │
│  hydra-bot        : Python bot container (main.py)                          │
│  hydra-arb        : Go arb engine container                                 │
│  triada-prometheus: Metrics scraper (:9090 bot, :9091 arb, :9092 UI)       │
│  triada-grafana   : Live dashboard (:3000)                                  │
│  triada-watchdog  : External self-healing supervisor (docker.sock mount)   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.1 Python Ingress (Primary Strategy)

| Component | File | Responsibility |
|-----------|------|----------------|
| **Bot State Machine** | `src/core/bot.py` | IDLE → SCANNING → BUYING → IN_POSITION → EXITING loop. Integrates WS ticker cache, health checks, capital evaluation every 30s. |
| **Scanner v3** | `src/core/scanner.py` | HYPE/DUMP detection, RSI/EMA/RVOL analysis, outputs `hot_symbols.txt`. |
| **Capital Router** | `shared/capital_router.py` | Multi-tier risk allocation. $15 Bootstrap → $50 Growth → $100 Maturity → $250 Power → $1000+ Apex. Real-money Martingale grids locked under $50. |
| **Order Manager** | `shared/order_manager.py` | ExecutionStrategy ABC → SimpleLimitStrategy. All exchange calls routed through facade. |
| **Health Checker** | `src/core/health.py` | Async-safe, zero-blocking network I/O. Passive monitoring via `last_rest_poll_time`, tickers cache freshness (≥95% within 10s), persistent SQLite ping, watchdog SIGTERM on 3 consecutive failures. |
| **WebSocket Listener** | `src/api/bybit_client.py` | Raw `websockets` connection to Bybit V5 public spot stream (`wss://stream.bybit.com/v5/public/spot`). Batch subscribe (max 10 topics per message). Auto-reconnect with exponential backoff. Replaces REST polling as primary price source. |
| **Prometheus Metrics** | `shared/metrics.py` | `hydra_health_status`, `balance_usdt`, `grid_max_levels`, order counters. Uses global `REGISTRY`. |

### 1.2 Go Arb Engine (Ultra-Low Latency)

| Component | File | Responsibility |
|-----------|------|----------------|
| **MarketSlot** | `arb-engine/engine/slot.go` | 192-byte struct, 3 cache lines. SeqLock with atomic ops. |
| **SPSC Ring** | `arb-engine/ringbuf/spsc.go` | Lock-free ring buffer, cache-line padded head/tail. |
| **Bybit WS** | `arb-engine/exchange/bybit_ws.go` | WebSocket ticker subscription, writes into MarketSlots. |
| **Triangular Scanner** | `arb-engine/strategy/triangular.go` | Forward + reverse USDT→BTC→ETH→USDT with fee-adjusted profit calc. |
| **Capital Bridge** | `arb-engine/bridge/capital.go` | Reads `shared/capital_state.json`, blocks until `arb_allowed=true`. |
| **Prometheus** | `arb-engine/metrics/prom.go` | `:9091` — arb scans/signals/profit/latency counters. |

### 1.3 Systemic Risk Invariants

- **Correlation Interlock** (`src/core/bot.py`): >3 symbols dumping simultaneously → `max_active_slots=1` (global panic slot-throttling).
- **In-Position Pause Flags**: While IN_POSITION, scanner output is ignored; exit strategy takes precedence.
- **Capital Floor**: `min_equity_usd` (was `stop_loss_total`) triggers graceful shutdown when breached.
- **Martingale Lock**: HYDRA-NET ATR-grid Martingale grids only activate when equity > $50.

---

## 2. PRODUCTION STATE VERIFICATION

### 2.1 Test Suite — Strictly Green

```
Python Tests:  12 files, 80 test cases (pytest)
Go Tests:      9 files, 7+ test cases (go test)
Total:         87/87 PASS

Key Test Modules:
  tests/test_health.py          — HealthChecker passive monitoring, watchdog SIGTERM, Bybit timeout sim
  tests/test_config_models.py   — Pydantic config validation
  tests/test_grid_math.py       — HYDRA-NET grid level calculations
  tests/test_order_manager.py   — Limit order execution, retry logic
  tests/test_capital_router.py  — Tier transitions, hysteresis, atomic JSON
```

### 2.2 Core Invariants — Built & Verified

| Invariant | Status | Evidence |
|-----------|--------|----------|
| CapitalRouter multi-tier allocation | ✅ LOCKED | `shared/capital_router.py` — atomic JSON state, hysteresis buffers |
| Correlation Interlock | ✅ LOCKED | `src/core/bot.py` — global slot throttling on dump detection |
| OrderManager facade | ✅ LOCKED | `shared/order_manager.py` — 10+ exchange calls replaced with strategy pattern |
| HealthChecker async-safe | ✅ LOCKED | `src/core/health.py` — no active network I/O, passive timestamp checks |
| Prometheus registry | ✅ LOCKED | `shared/metrics.py` — global `REGISTRY`, stale metric cleanup on init |
| WebSocket real-time tickers | ✅ LOCKED | `src/api/bybit_client.py` — raw Bybit V5 WS, replaces REST polling |

### 2.3 Infrastructure — Deployed & Live

| Service | Container | Port | Status |
|---------|-----------|------|--------|
| HYDRA Bot | `hydra-bot` | internal | Running on AWS |
| Go Arb Engine | `hydra-arb` | internal | Running on AWS |
| Prometheus | `triada-prometheus` | `:9090` (bot), `:9091` (arb) | Scraping both endpoints |
| Grafana | `triada-grafana` | `:3000` | Live dashboard accessible |

**Deployment Method:** Terraform-provisioned AWS EC2 (ap-southeast-1) + Docker Compose multi-stage builds. Automated `scp` + `docker compose up -d --build` from local workspace.

---

## 3. IMMEDIATE DEVELOPMENT STATE (Latest Session)

### 3.1 WebSocket Real-Time Ticker Mode — COMPLETE

**What was done:**
- Removed `ccxt.pro` dependency (unstable with Bybit V5) and rewrote `WebSocketListener` to use raw `websockets` library.
- Connects to `wss://stream.bybit.com/v5/public/spot` with batch subscription (max 10 topics per `subscribe` message).
- Parses Bybit V5 ticker snapshot format: `{"topic":"tickers.BTCUSDT","data":{"lastPrice":"...",...}}`.
- Synthetic bid/ask generated from `lastPrice` with 0.05% spread (Bybit spot tickers lack bid/ask fields).
- Exponential backoff reconnect: base 5s, max 60s, max 10 attempts, then thread exits.
- Bot `_update_websocket_stream()` now **prefers WS data in all modes** (dry_run and live). REST polling is fallback only.
- Config toggle added to `shared/config.json`:
  ```json
  "websocket": {
    "enabled": true,
    "reconnect_interval_sec": 5,
    "max_reconnect_attempts": 10
  }
  ```

**Verification:**
- Local: `@WS_TICK@ Using 11/11 WebSocket tickers` logged every cycle.
- Remote: `hydra_health_status 1.0` confirmed via `curl localhost:9090/metrics`.
- No `REST_POLL` lines after WS warm-up.

### 3.2 Async HealthChecker — COMPLETE

**What was done:**
- Full rewrite of `src/core/health.py`:
  - `_check_exchange_api`: passive check using `last_rest_poll_time` (no `fetch_ticker` calls).
  - `_check_tickers_cache`: requires ≥95% of symbols updated within last 10 seconds.
  - `_check_database`: uses persistent SQLite connection with lightweight `SELECT 1`.
  - Watchdog: `os.kill(pid, signal.SIGTERM)` after 3 consecutive failures → Docker `restart: unless-stopped` handles container restart.
- `shared/metrics.py`: switched from custom `CollectorRegistry()` to global `REGISTRY` with stale-metric cleanup on import.

**Verification:**
- `pytest tests/test_health.py -v` → 12/12 PASS.
- Prometheus `hydra_health_status` toggles correctly between 1.0 and 0.0 under simulated failures.

---

## 4. SYSTEM DIRECTORY MAP

```
triada/
│
├── main.py                          # Python bot entry point
├── run_scanner.py                   # Scanner entry point
├── docker-compose.yml               # Multi-service orchestration
├── Dockerfile                       # Multi-stage Python build
├── requirements.txt                 # Python deps (ccxt, websockets, prometheus-client, pydantic)
├── pytest.ini                       # pytest configuration
│
├── src/
│   ├── api/
│   │   └── bybit_client.py         # BybitClient (REST) + WebSocketListener (raw WS)
│   ├── core/
│   │   ├── bot.py                   # TradingBot state machine, WS/REST ticker dispatch
│   │   ├── health.py                # HealthChecker — async, passive, watchdog SIGTERM
│   │   ├── scanner.py               # Market scanner v3 (HYPE/DUMP, RSI, EMA, RVOL)
│   │   └── risk/
│   │       └── limits.py            # Risk limit validations
│   ├── indicators/
│   │   └── matrix.py                # RSI, EMA, MACD, Stochastic, ATR, Ichimoku, Volume POC
│   └── database/
│       └── models.py                # TradeDatabase (SQLite, persistent conn, FIFO PnL)
│
├── shared/                          # PYTHONPATH root; mounted as Docker volume
│   ├── config.py                    # Config loader with caching + Pydantic validation
│   ├── config.json                  # Runtime trading parameters (slot_size, thresholds, WS toggle)
│   ├── config_models.py             # Pydantic schemas: TradingConfig, HydraNetConfig
│   ├── capital_router.py            # CapitalRouter — 5-tier allocation, hysteresis
│   ├── order_manager.py             # OrderManager facade + SimpleLimitStrategy
│   ├── metrics.py                   # METRICS singleton (global REGISTRY, :9090)
│   ├── logger_setup.py              # Structured logging (@TAGS@ format)
│   ├── utils.py                     # safe_float, safe_int helpers
│   ├── paths.py                     # PROJECT_ROOT, TRADES_DB, DEFAULT_CONFIG
│   ├── state/
│   │   └── trades.db                # SQLite production DB (Docker volume)
│   └── capital_state.json           # Live capital mode + max_grid_levels (Go bridge reads this)
│
├── arb-engine/                      # Go 1.22 ultra-latency arbitrage
│   ├── engine/
│   │   └── slot.go                  # MarketSlot (192 bytes, SeqLock)
│   ├── exchange/
│   │   └── bybit_ws.go              # Bybit WS consumer → MarketSlot writer
│   ├── strategy/
│   │   └── triangular.go            # Triangular arb: USDT→BTC→ETH→USDT
│   ├── bridge/
│   │   └── capital.go               # Reads shared/capital_state.json
│   ├── ringbuf/
│   │   └── spsc.go                  # Lock-free SPSC ring buffer
│   ├── metrics/
│   │   └── prom.go                  # Prometheus counters (:9091)
│   └── go.mod                       # Go module definition
│
├── monitoring/
│   ├── prometheus.yml               # Scrape configs (:9090 bot, :9091 arb)
│   ├── grafana_dashboard.json       # 12-panel live dashboard
│   └── alert_rules.yml              # 7 Prometheus alert rules
│
├── terraform/                       # AWS Infrastructure as Code
│   ├── main.tf                      # EC2, security groups
│   ├── variables.tf                 # Region, instance type, key pair
│   └── outputs.tf                   # Public IP, Grafana URL
│
├── tests/                           # Python pytest suite (65 cases total)
│   ├── test_health.py               # HealthChecker scenarios
│   ├── test_config_models.py        # Pydantic validation
│   ├── test_grid_math.py            # HYDRA-NET grid calculations
│   ├── test_order_manager.py        # Order execution mocks
│   ├── test_capital_router.py       # Tier transitions
│   └── conftest.py                  # Shared fixtures
│
├── docs/                            # Architecture docs (v17 upgrade guide)
│   ├── TECHNICAL_ARCHITECTURE_v17.md
│   ├── UPGRADE_GUIDE_v17.md
│   └── SETUP_CHECKLIST.md
│
├── logs/
│   └── bot.log                      # Production structured logs
│
├── .env                             # API keys (DO NOT COMMIT)
├── .env.example                     # Template for new devs
├── hot_symbols.txt                  # Scanner output (live symbol list)
├── daily_report.csv                 # CSV trade report generator
└── triada.tar.gz                    # Deployment artifact
```

---

## 5. CRITICAL CONFIGURATION KEYS

### 5.1 `shared/config.json` (Runtime Toggles)

```json
{
  "trading": {
    "slot_size": 5.0,
    "dry_run": true,
    "max_trades_per_day": 2500,
    "min_confidence_threshold": 85.0,
    "order_execution_timeout_sec": 60
  },
  "websocket": {
    "enabled": true,
    "reconnect_interval_sec": 5,
    "max_reconnect_attempts": 10
  },
  "hydra_net": {
    "enabled": true,
    "grid_distance_pct": 0.4,
    "max_grid_levels": 3,
    "take_profit_pct": 0.8,
    "failing_knife_threshold": -3.0
  },
  "scanner": {
    "enabled": true,
    "cache_ttl": 600,
    "use_priority": true
  },
  "metrics": { "port": 9090 },
  "arbitrage": {
    "enabled": true,
    "min_profit_pct": 0.05,
    "scan_interval_ms": 50,
    "metrics_port": 9091
  }
}
```

### 5.2 Environment Variables (`.env`)

```bash
BYBIT_API_KEY=xxx
BYBIT_API_SECRET=xxx
LOG_LEVEL=INFO
TANK_MODE=false   # Legacy; now controlled via config.json trading.tank_mode
```

---

## 6. OPERATIONAL PLAYBOOK FOR AI AGENTS

### 6.1 Before Any Edit

1. Run `pytest tests/` locally. All 65 must be green.
2. Check `hydra_health_status` at `http://localhost:9090/metrics`.
3. Verify `shared/config.json` schema with `python -c "from config_models import validate_config; validate_config(...)"`.

### 6.2 After Any Edit

1. Re-run `pytest tests/`
2. Start local bot: `python main.py > logs/bot.log 2>&1`
3. Verify WS connection: `grep "@WS_TICK@" logs/bot.log` (should show `11/11 WebSocket tickers`)
4. Verify health: `curl -s http://localhost:9090/metrics | grep health_status`
5. Deploy to AWS: `scp` changed files + `ssh ... docker compose up -d --build hydra-bot`

### 6.3 Forbidden Actions (Invariant Protections)

- **NEVER** re-introduce active network calls into `health.py` checks.
- **NEVER** reduce the tickers freshness threshold below 95% within 10s.
- **NEVER** remove the persistent SQLite connection in `TradeDatabase`.
- **NEVER** disable the watchdog SIGTERM mechanism in `HealthChecker`.
- **NEVER** remove the `restart: unless-stopped` Docker policy.
- **NEVER** commit `.env` files.

---

## 7. VERSION HISTORY

| Version | Date | Key Changes |
|---------|------|-------------|
| v5.4-Absolute Pure | 2026-05-24 | WebSocket real-time ticker mode enabled. HealthChecker fully async. 65/65 tests green. |
| v5.3 | 2026-05-20 | HealthChecker refactoring, Prometheus registry fix, CapitalRouter tier locking. |
| v5.2 | 2026-05-15 | Go Arb Engine integration, Docker Compose orchestration, Grafana dashboard. |
| v5.1 | 2026-05-10 | HYDRA-NET ATR-grid Martingale, Signal Optimizer v2. |
| v5.0 | 2026-05-01 | Initial split-logic topology (Python + Go). |

---

**End of Specification**  
*For questions, refer to `docs/TECHNICAL_ARCHITECTURE_v17.md` and `docs/UPGRADE_GUIDE_v17.md`.*
