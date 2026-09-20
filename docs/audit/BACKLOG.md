# Audit backlog — deferred items

One line per item. Source: `00_full_audit_2026-09-06.md` unless noted. Items here are
**not** part of the critical fix plan (Phases 0–5); they are cleanup/refactor work for Phase 6.

Format: `- [ ] <id> <what> — <where> — <why deferred / decision>`

## Dead / duplicate code (remove in Phase 6 after live logic is stabilised)

- [ ] B-01 **Tank mode: remove (decision "б", 2026-09-09).** `tank_mode` flag is read from two
  unsynchronised sources (`main.py:42-52` → `initialize_analyzer` at startup vs
  `trading_config.get('tank_mode')` per tick in `scanning.py:484`), so env/config disagreement yields a
  half-mode. Semantically it contradicts Hydra's knife-catch entry (requires bullish Ichimoku/EMA,
  `matrix.py:567-593`, `:653-664`, `:677-684`). Never enabled in any recorded run. Remove together with
  `_scan_for_entries`; build a "cautious live" preset from existing validated knobs instead
  (`slot_size`, `max_grid_levels=1`, `min_confidence_threshold`, `min_rvol_threshold`).
  Touch points: `main.py:39-52`, `bot.py:72-86`, `scanning.py:86-88,209-259,484-522`,
  `matrix.py:510-548,567-593,653-664,677-684,1002-1016`, `config.json:20`, `README.md §5.2`.
- [x] B-02 Second definition of `_handle_scanning_state` (`scanning.py:16`) and the whole legacy
  `_scan_for_entries` (`scanning.py:21-357`) are dead — the later definition at `:605` wins.
  Done 2026-09-14: both removed; `@SCAN_NO_QUEUE@` fallback now goes IDLE (queue scan is the only path).
- [x] B-03 `_fix_executed_grid_deal` (`hydra_net.py:308-407`) is never called and has a `NameError`
  (`trading_config` undefined at `:364`). Done 2026-09-14: removed; no imports became unused.
- [ ] B-04 `shared/database.py` duplicates `src/database/models.py` with an older schema; kept alive only
  by the `sys.path` ordering hack in `main.py:9-13`.
- [ ] B-05 `shared/exchange_utils.py` — unreferenced exchange client with live order methods and the same
  synthetic `fetch_order` fallback (`:213-214`).
- [ ] B-06 `shared/utils.py:116-212` — `ProfitManager`, `HealthChecker`, `SoundNotifier` unused;
  `paths.py:12-13` `V17_CONFIG`, `SESSION_PROFIT_FILE` unused.
- [ ] B-07 `archive/scanner_legacy.py`, root-level `analyze_entry.py`, `compare_metrics.py`,
  `compare_logs.ps1`, `daily_report.csv`, `trades.db` (root copy), `HYDRA_MATH_ANALYSIS.md`,
  `TRADING_TEST_RESULTS.md`, `roadmap.md` — decide keep/move to `docs/` or `scripts/`.
- [ ] B-08 `breakeven.py:41-42` computes a fee-aware multiplier that is immediately overwritten at `:44`
  (functional bug H8 is in the fix plan; the dead lines go here).

## Structure

- [ ] B-09 Replace per-module `sys.path.append(... 'shared')` with a real package + one `PYTHONPATH`
  (`pyproject.toml`); resolves the `database` name collision (B-04).
- [ ] B-10 `print(..., end='\r')` progress lines in production handlers (`in_position.py:51`,
  `buying.py:205`, `exiting.py:32`, `limits.py:32,49`, `bot.py:810`) — replace with logger/metrics.
- [ ] B-11 `metrics.py:208` trailing `import json`; `bot.py:846` in-function `import numpy`.
- [ ] B-12 Pydantic schema for the whole config with `extra='forbid'` (`config_models.py:43,73,89-132`
  currently validate only `trading`/`hydra_net` with `extra='allow'`).
- [ ] B-13 `tools/*.sh` and `tools/*.py` — several hardcode `/app/shared/state/trades.db`; unify on
  `shared/paths.py`.

## Infra / hygiene

- [ ] B-14 `Dockerfile` runs as root (no `USER`); add a non-root user once volume permissions are sorted.
- [ ] B-15 Grafana default password `triada2024` in `docker-compose.yml:91` — move to `.env`
  (`GRAFANA_PASSWORD` is already read; drop the fallback).
- [ ] B-16 Docker healthcheck + `restart: unless-stopped` + in-process `HealthChecker` SIGTERM + external
  watchdog = four overlapping supervisors; decide on one owner (audit H2/H3, plan Phase 4 §17).
- [ ] B-17 `tests/test_health.py:83,91` set `bot.state` to strings while production uses `BotState` Enum —
  test must use the Enum once `health.py:134` is fixed.

## Done

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
