# Audit backlog — deferred items

One line per item. Source: `00_full_audit_2026-09-06.md` unless noted. Items here are
**not** part of the critical fix plan (Phases 0–5); they are cleanup/refactor work for Phase 6.

Format: `- [ ] <id> <what> — <where> — <why deferred / decision>`

## Dead / duplicate code (remove in Phase 6 after live logic is stabilised)

- [x] B-01 **Tank mode: remove (decision "б", 2026-09-09).** `tank_mode` flag is read from two
  unsynchronised sources (`main.py:42-52` → `initialize_analyzer` at startup vs
  `trading_config.get('tank_mode')` per tick in `scanning.py:484`), so env/config disagreement yields a
  half-mode. Semantically it contradicts Hydra's knife-catch entry (requires bullish Ichimoku/EMA,
  `matrix.py:567-593`, `:653-664`, `:677-684`). Never enabled in any recorded run. Remove together with
  `_scan_for_entries`; build a "cautious live" preset from existing validated knobs instead
  (`slot_size`, `max_grid_levels=1`, `min_confidence_threshold`, `min_rvol_threshold`).
  Touch points: `main.py:39-52`, `bot.py:72-86`, `scanning.py:86-88,209-259,484-522`,
  `matrix.py:510-548,567-593,653-664,677-684,1002-1016`, `config.json:20`, `README.md §5.2`.
  Done 2026-09-21 (TZ-06): all touch points removed; "cautious live" preset not added.
- [x] B-02 Second definition of `_handle_scanning_state` (`scanning.py:16`) and the whole legacy
  `_scan_for_entries` (`scanning.py:21-357`) are dead — the later definition at `:605` wins.
  Done 2026-09-14: both removed; `@SCAN_NO_QUEUE@` fallback now goes IDLE (queue scan is the only path).
- [x] B-03 `_fix_executed_grid_deal` (`hydra_net.py:308-407`) is never called and has a `NameError`
  (`trading_config` undefined at `:364`). Done 2026-09-14: removed; no imports became unused.
- [x] B-04 `shared/database.py` duplicates `src/database/models.py` with an older schema; kept alive only
  by the `sys.path` ordering hack in `main.py:9-13`. Done 2026-09-21 (TZ-06): file deleted; `trade_logger.py`
  `from database import TradeDatabase` resolves to `src/database` package.
- [x] B-05 `shared/exchange_utils.py` — unreferenced exchange client with live order methods and the same
  synthetic `fetch_order` fallback (`:213-214`). Done 2026-09-21 (TZ-06): file deleted.
- [x] B-06 `shared/utils.py:116-212` — `ProfitManager`, `HealthChecker`, `SoundNotifier` unused;
  `paths.py:12-13` `V17_CONFIG`, `SESSION_PROFIT_FILE` unused. Done 2026-09-21 (TZ-06): classes +
  `get_session_profit` removed with now-unused imports (`json`, `os`, `time`, `logger`, `paths`).
- [x] B-07 `archive/scanner_legacy.py`, root-level `analyze_entry.py`, `compare_metrics.py`,
  `compare_logs.ps1`, `daily_report.csv`, `trades.db` (root copy), `HYDRA_MATH_ANALYSIS.md`,
  `TRADING_TEST_RESULTS.md`, `roadmap.md` — decide keep/move to `docs/` or `scripts/`.
  Done 2026-10-07 (TZ-09): scripts → `scripts/analysis/`, history docs + `archive/` → `docs/history/`,
  `grafana_trade_panels.json` → `monitoring/`, `upload_to_github.ps1` removed; `daily_report.csv`
  untracked (bot still writes it at runtime) and `/trades.db`, `/daily_report.csv` gitignored
  (root `trades.db` was never tracked). Note: `scripts/analysis/compare_logs.ps1` hardcodes a local
  Windows path and `analyze_entry.py` the container DB path (B-13 territory).
- [x] B-08 `breakeven.py:41-42` computes a fee-aware multiplier that is immediately overwritten at `:44`
  (functional bug H8 is in the fix plan; the dead lines go here). Done 2026-09-22 (TZ-05): the `1.001`
  overwrite and the duplicate `price_to_precision` call removed; the fee-aware price is the one placed.
- [x] B-18 The same `buy_price * 1.001` breakeven price survives in `in_position.py:401`
  (`_execute_partial_tp`, re-order of the remaining position) — below the 0.2% round-trip fee, so the
  "breakeven" leg exits at a loss. Found during TZ-05; out of that TZ's scope (п.4 named only
  `breakeven.py`). Fix by reusing the fee-aware multiplier, ideally extracted into one helper.
  Done 2026-10-07: `BreakevenMixin._breakeven_price(symbol, buy_price)` is the single helper used by
  `_set_breakeven` and the partial-TP remainder; test `test_partial_tp_remainder_uses_fee_aware_breakeven`.

- [x] B-19 `scanning.py` `_validate_candidate` referenced `obi_skew_val` in its return dict while
  the variable is only bound inside `if dispatcher_enabled:` — with the dispatcher off every
  validated candidate raised `NameError` outside the surrounding try. Found during TZ-12A;
  worked around by initialising `obi_skew_val = 0.0` / `context = {}` up front, but the real fix is
  to build the `dispatcher_features` dict in one place instead of re-listing the keys.
  Done 2026-10-07: the dict is assembled once after scoring; the scan-time DB row is
  `log_dispatcher_features(trade_id=0, **dispatcher_features)` and the same dict is returned.
  Tests cover dispatcher off (neutral features, no row) and on (row == carried dict).
  Still re-listed: the trade-linked rows in `buying.py`, `hydra_net.py`, `bot.py` (`df.get(...)` per key).
- [x] B-20 `docs/bot_map.md` §4.5/§7.2 still shows the pre-TZ-12A `dispatcher_features` DDL
  (no `spread_pct`, `bid_ask_source`, `entry_ask`, `source`, ...). Out of TZ-12A's file scope.
  Done 2026-10-07: §4.5 DDL synced with `models.py`; §7.1 `trades` DDL corrected too (had a
  non-existent `mode`, lacked `confidence`).

## Structure

- [ ] B-09 Replace per-module `sys.path.append(... 'shared')` with a real package + one `PYTHONPATH`
  (`pyproject.toml`); resolves the `database` name collision (B-04).
- [ ] B-10 `print(..., end='\r')` progress lines in production handlers (`in_position.py:51`,
  `buying.py:205`, `exiting.py:32`, `limits.py:32,49`, `bot.py:810`) — replace with logger/metrics.
- [ ] B-11 `metrics.py:208` trailing `import json`; `bot.py:846` in-function `import numpy`.
- [x] B-12 Pydantic schema for the whole config with `extra='forbid'` (`config_models.py:43,73,89-132`
  currently validate only `trading`/`hydra_net` with `extra='allow'`).
  Done 2026-10-07 (TZ-07): every section has a model, all except `trading`/`hydra_net` are
  `extra='forbid'` (incl. `exchange`); unknown top-level section → `unknown section '<name>'`.
  Non-core sections are re-emitted with `exclude_unset` so `.get(k, default)` call sites see no
  injected keys. Remaining: inventory + forbid for `trading`/`hydra_net`.
  **Operator:** the server `config.json` differs from the repo one — validate it before deploying
  (`python -c "import json,sys; sys.path.insert(0,'shared'); from config_models import validate_config; validate_config(json.load(open('<path>')))"`).
- [ ] B-13 `tools/*.sh` and `tools/*.py` — several hardcode `/app/shared/state/trades.db`; unify on
  `shared/paths.py`.

## Infra / hygiene

- [x] B-14 `Dockerfile` runs as root (no `USER`); add a non-root user once volume permissions are sorted.
  Done 2026-10-07 (TZ-08): `USER hydra` (uid 10001) + `user: "10001:10001"` in compose; `/app/logs`,
  `/app/shared/state` pre-created and chowned. `docker build` not run locally (no Docker daemon).
  **Operator:** an existing root-owned `shared-data` volume must be chowned once (see `DEPLOY.md`).
- [x] B-15 Grafana default password `triada2024` in `docker-compose.yml:91` — move to `.env`
  (`GRAFANA_PASSWORD` is already read; drop the fallback).
  Done 2026-10-07 (TZ-08): `${GRAFANA_PASSWORD:?...}`; `docker compose config` fails without it.
  Note: Grafana only applies the env password on first init of `grafana-data`; an existing volume
  keeps its stored password (`grafana-cli admin reset-admin-password` to rotate).
- [x] B-16 Docker healthcheck + `restart: unless-stopped` + in-process `HealthChecker` SIGTERM + external
  watchdog = four overlapping supervisors; decide on one owner (audit H2/H3, plan Phase 4 §17).
  Done 2026-09-22 (TZ-04): the external watchdog owns restart; `HealthChecker` only reports
  (`@HEALTH_CRITICAL@` + `health_status=0`). Thresholds raised to 60s/20s. Docker `restart:
  unless-stopped` and the compose healthcheck are left as-is — they do not race the watchdog.
- [x] B-17 `tests/test_health.py:83,91` set `bot.state` to strings while production uses `BotState` Enum —
  test must use the Enum once `health.py:134` is fixed. Done 2026-09-22 (TZ-04): `_check_state_stuck`
  compares Enum members; `DummyBot` and all assignments use `BotState.*`.

## Done

- [x] 2026-10-01 **Phase 3 — grid (audit C2, plan §6 items 12–15).** `hydra_net.py` rewritten: level 0 = entry at ask,
  separate `grid_buy_order_id` / `tp_order_id`, TP refresh cancels only the TP, TP fill during the grid is an exit
  (`_on_grid_tp_filled`), knife/max-hold/level-rejected end the grid into IN_POSITION (`_end_grid_to_position`),
  no phantom base quantity, `quantize_order` before every placement, `amend_order(..., side=)`, ATR cached 60 s.
  Dry-run uses a virtual order book through the same `_grid_*` wrappers (the fake 2-second fill is gone).
  Tests: `tests/test_grid_lifecycle.py` (12). Known gap: `_synchronize_grid_network` re-pricing still uses REST ATR every 60 s.

- [x] 2026-09-09 `go-scalper/` → repo `triada-scalper`; `arb-engine/` → repo `triada-arb` (subtree split,
  binaries/dry-run dumps purged from their history).
- [x] 2026-09-09 58 `tmp_*.py`/`tmp_*.sh` removed from repo root; `terraform.tfvars` untracked;
  `deploy.bat` no longer force-pushes; SG ports 3000/9090/9092 restricted to `your_ip_cidr`
  (needs `terraform apply`).
- [x] 2026-09-09 unread `arbitrage` section removed from `shared/config.json`.
- [x] 2026-09-14 **Phase 1 (audit plan §6 items 5–8) + reconciled IDLE (item 9) + exit retry budget (item 10).**
  `fetch_order`/`fetch_balance`/`fetch_ticker` return `None` instead of fabricated values;
  `get_coin_balance`/`get_free_usdt`/`get_non_usdt_holdings` replace 5 copies of UTA parsing;
  `TradingBot._get_fresh_price` gates every stop/target on a fresh price; `TradingBot._transition_to_idle`
  is the only path to IDLE from BUYING/IN_POSITION/EXITING and requires the exchange to confirm the
  coin is gone; `_resolve_buy_by_balance` / `_resolve_exit_by_balance` replace "assume filled/sold";
  `max_exit_attempts` (default 5) halts exit retries. Tests: `tests/test_exchange_truth.py`,
  `tests/test_state_unknown.py` (34 cases). Closes C1, C3 (main paths), C4, C5, C6; H4 partially.
