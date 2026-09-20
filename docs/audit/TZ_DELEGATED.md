# ТЗ для делегируемых задач (после Фазы 1)

Состояние на 2026-09-21: HEAD `af8e4a1`, 168 тестов зелёные. Фаза 1 (правда о бирже) и B-01…B-03
частично закрыты. Ниже — самостоятельные задачи, которые можно отдавать более дешёвой модели.
**Фаза 3 (сетка, C2) в этот список не входит — её делает сильная модель в режиме high.**

## Общий протокол исполнителя

1. Одна задача = одна ветка/один коммит. Не трогать файлы вне раздела «Файлы».
2. Перед началом: `git status` чистый, `python -m pytest -q` → все зелёные. Если нет — стоп, доложить.
3. Ничего не «улучшать» сверх написанного. Нашёл проблему рядом — одна строка в `docs/audit/BACKLOG.md`, не чинить.
4. Никаких заглушек «чтобы не падало»: любое `except: return <удобное значение>` в торговом коде — запрещено. Это первопричина критических багов аудита.
5. Не менять `shared/config.json` значения (это dry-run baseline; серверный конфиг другой), только структуру, если ТЗ прямо требует.
6. Приёмка = все пункты «Критерии» + команды «Проверка» отработали. Отчёт: что изменено (файл:строки), вывод pytest, вывод grep-проверок.
7. Коммит-сообщение: `<type>(<scope>): <что>` + ссылка на номер ТЗ и пункт BACKLOG.

---

## TZ-04 — Надзор без ложных рестартов (аудит H2, H3; BACKLOG B-16, B-17)

**Модель / режим:** средняя, medium.
**Файлы:** `src/core/health.py`, `src/monitoring/watchdog.py`, `monitoring/Dockerfile.watchdog`, `docker-compose.yml`,
`src/core/states/idle.py`, `tests/test_health.py`, `tests/test_watchdog.py`.

**Проблема.** Два надзирателя стреляют по здоровому боту:
- `watchdog`: `STALL_THRESHOLD_SEC=15` при `scrape_interval: 10s` (`monitoring/prometheus.yml:2`) и блокировках главного цикла до 6–16 с → ложный `heartbeat_stalled`, затем `restart(timeout=2)` = SIGKILL через 2 с.
- `health.py:134`: `state in ['IN_POSITION', ...]` сравнивает `BotState` (Enum) со строками → всегда False; тест проходит, потому что `tests/test_health.py:83,91` подставляют строки.
- `health.py:66-71`: после 3 провалов (90 с) бот сам себе шлёт SIGTERM — конфликтует с внешним watchdog.

**Шаги.**
1. `health.py:134`: сравнивать с Enum: `if state in (BotState.IN_POSITION, BotState.BUYING, BotState.EXITING)`; словарь `max_time` — тоже ключи Enum. Импорт `from core.state_enum import BotState`.
2. `tests/test_health.py`: `DummyBot.state` и присваивания в тестах → `BotState.*`. Добавить тест: `state=BotState.BUYING`, `state_entry_time = now-301` → `_check_state_stuck() is False`; `state=BotState.IDLE`, любое время → True.
3. `health.py`: убрать вызов `self._trigger_watchdog_restart()` из `check()` (метод оставить, но не вызывать; в docstring — «restart is owned by the external watchdog»). Вместо этого при `consecutive_fails >= max_consecutive_fails` — `logger.critical("@HEALTH_CRITICAL@ ...")` и `METRICS.health_status.set(0.0)`. Правило гистерезиса на строке 81 сохранить.
4. `watchdog.py:56`: default `STALL_THRESHOLD_SEC` → `"60"`; `:58` `RESTART_TIMEOUT_SEC` → `"20"`. Docstring (`:28,:30`) синхронизировать.
5. `monitoring/Dockerfile.watchdog:25` → `STALL_THRESHOLD_SEC=60`; в `docker-compose.yml` в сервисе `watchdog` → `STALL_THRESHOLD_SEC=60`, добавить `RESTART_TIMEOUT_SEC=20`.
6. `watchdog.py:277-283`: при исключении из `controller.restart(...)` выставлять `self.state.last_restart_ts = now` (чтобы не было плотного цикла попыток каждые 5 с). Тест: fake controller, `restart` бросает → второй `tick()` через 5 с не вызывает `restart` повторно.
7. `src/core/states/idle.py:26`: заменить `time.sleep(5)` на неблокирующий гейт: `self._idle_next_check_ts` (атрибут инициализировать через `getattr(self, ..., 0.0)`); если `now < _idle_next_check_ts` → `return` без проверок; при провале проверок ставить `_idle_next_check_ts = now + 5`. Heartbeat главного цикла (`bot.py` `METRICS.heartbeat_timestamp.set`) должен обновляться каждую секунду в IDLE.
8. `tests/test_watchdog.py`: если есть тест с именем `test_target_down_triggers_restart`, утверждающий отсутствие рестарта, — переименовать в `test_target_down_does_not_trigger_restart` (HANDOFF §4).

**Критерии.**
- `grep -n "'IN_POSITION'" src/core/health.py` → 0 совпадений.
- `grep -n "_trigger_watchdog_restart()" src/core/health.py` → 0 вызовов (определение остаётся).
- `grep -n "time.sleep" src/core/states/idle.py` → 0.
- Все дефолты порогов в трёх местах (watchdog.py, Dockerfile.watchdog, docker-compose.yml) одинаковые.
- pytest зелёный; новые тесты из п.2 и п.6 присутствуют.

**Проверка.** `python -m pytest tests/test_health.py tests/test_watchdog.py -q`; `docker compose config --quiet`.

**Не трогать.** `bot.py` кроме, при необходимости, инициализации `_idle_next_check_ts` в `__init__`; логику `_check_tickers_cache` (порог 95% — отдельное решение).

---

## TZ-05 — Один источник PnL (аудит H7, H8; BACKLOG B-08)

**Модель / режим:** средняя, medium.
**Файлы:** `src/database/models.py`, `src/core/states/in_position.py` (только `_execute_partial_tp`), `src/core/risk/breakeven.py`,
`tests/test_pnl.py` (расширить) или новый `tests/test_session_stats.py`.

**Проблема.** PnL считается двумя способами: в памяти через `_calc_pnl` с комиссиями, в БД `get_session_stats`
пересчитывает брутто `(price - buy_price) * matched` (`models.py:226`), игнорируя колонку `profit`, которую сам же пишет.
Риск-лок капитала (`limits.py`, `get_session_stats(since_ts)['session_profit']`) кормится брутто → убытки занижены.
Частичный TP записывает прибыль до исполнения ордера. Breakeven-цена `buy_price * 1.001` меньше 0.2% комиссий.

**Шаги.**
1. `models.py` `get_session_stats(since_ts)`: считать по колонке `profit`:
   `SELECT side, profit FROM trades WHERE timestamp >= ? AND side LIKE 'sell%'`;
   `session_profit = sum(profit)`, `total_trades = count(side in ('sell','sell_panic'))` (партиалы `sell_partial` в сумму входят, в счётчик сделок — нет),
   `winning_trades = count(profit > 0 among sell/sell_panic)`. FIFO-матчинг удалить. Ключи возвращаемого словаря сохранить.
2. `models.py` `get_daily_trades_count(since_ts)`: считать только `side IN ('sell','sell_panic')` — лимит `max_trades_per_day` ограничивает сделки, а не ноги. В docstring отразить.
3. `in_position.py` `_execute_partial_tp` (live-ветка): **не** логировать `sell_partial` и не менять `session_profit` при постановке лимитного ордера. Вместо этого сохранять в `state_data['partial_tp_order_id']` и `state_data['partial_tp_amount']`; в `_handle_in_position_state` перед проверкой основного TP: если `partial_tp_order_id` есть → `fetch_order`; `None` → ждать; `closed/filled` → тогда `log_trade('sell_partial', filled, average, profit=_calc_pnl(...))`, `session_profit +=`, очистить ключ; `canceled` → очистить ключ, лог warning. Dry-run ветку не менять.
4. `breakeven.py:41-46`: удалить строки 44–46 (перезапись `1.001`); использовать `breakeven_price` из `:41-42` (`buy_price * breakeven_multiplier`, где multiplier = `1 + taker + maker + 0.0002`). Убедиться, что `amount_to_precision` для `amount` остаётся.
5. Тесты (sqlite в `tmp_path`): (a) buy 10@1.0, sell 10@1.1 profit=0.8 → `session_profit == 0.8`, `total_trades == 1`; (b) + `sell_partial` profit=0.3 → `session_profit == 1.1`, `total_trades == 1`; (c) `get_daily_trades_count` не считает `buy`; (d) breakeven: `market()` → taker/maker 0.001 → цена = `buy * 1.0022` с точностью до `price_to_precision`.

**Критерии.** `grep -n "open_buys" src/database/models.py` → 0; `grep -n "1.001" src/core/risk/breakeven.py` → 0; pytest зелёный.

**Не трогать.** `shared/database.py` (удаляется в TZ-06), `_calc_pnl`, `exiting.py`.

---

## TZ-06 — Мёртвые модули и режим «танк» (BACKLOG B-01, B-04, B-05, B-06)

**Модель / режим:** дешёвая, low/medium. Механическое удаление по списку + grep.
**Файлы:** `shared/database.py`, `shared/exchange_utils.py`, `shared/utils.py`, `shared/paths.py`, `main.py`, `src/core/bot.py`,
`src/core/states/scanning.py`, `src/indicators/matrix.py`, `shared/config.json`, `shared/config_models.py`, `README.md`, `docs/bot_map.md`.

**Шаги.**
1. **B-04** удалить `shared/database.py`. Перед этим: `grep -rn "from database import\|import database" src shared main.py tests tools` — единственный допустимый импорт `from database.models import TradeDatabase` (пакет `src/database/`). Комментарий в `main.py:9-13` про порядок `sys.path` оставить (порядок всё ещё нужен для других имён).
2. **B-05** удалить `shared/exchange_utils.py`. Проверка: `grep -rn "exchange_utils\|ExchangeManager" --include=*.py .` → 0.
3. **B-06** в `shared/utils.py` удалить классы `ProfitManager`, `HealthChecker`, `SoundNotifier` и функцию `get_session_profit`; убрать ставшие лишними импорты (`json`, `os`, `SESSION_PROFIT_FILE`). В `shared/paths.py` удалить `V17_CONFIG`, `SESSION_PROFIT_FILE`. Проверка: `grep -rn "ProfitManager\|SoundNotifier\|SESSION_PROFIT_FILE\|V17_CONFIG" --include=*.py .` → 0 (в `tests/test_core.py` может использоваться `HealthChecker` из utils — если да, удалить этот тест, он проверяет `return True`).
4. **B-01 танк-режим** (решение «б» от 2026-09-09) — удалить целиком:
   - `main.py:39-52`: удалить резолв `TANK_MODE`; `TradingBot()` без аргумента.
   - `bot.py:72-86`: убрать параметр `tank_mode`, `self.tank_mode`, передавать `initialize_analyzer(self.config)`.
   - `scanning.py`: строки с `tank_mode` в `_validate_candidate` (`:141-143`, `:170-172`, `:175-179`) — удалить ветки, оставив обычную логику (`base_threshold` из конфига, без `85.0`).
   - `matrix.py`: параметр `tank_mode` из `SignalOptimizer.__init__` (`:533,:548`), `from_config` (`:510,:523`), `initialize_analyzer` (`:1002-1016`); блоки `if self.tank_mode:` (`:568-593`, `:654-664`, `:678-684`) удалить, оставив ветки `else`.
   - `shared/config.json:20` удалить `"tank_mode": false`; в `shared/config_models.py` удалить поле `tank_mode`, если оно объявлено.
   - `README.md` §5.2 — убрать строку `TANK_MODE`; `docs/bot_map.md` — упоминания танка.
   Проверка: `grep -rni "tank" --include=*.py --include=*.json --include=*.md . | grep -v docs/audit` → 0.
5. Прогнать pytest. Если что-то падает из-за удалённого `HealthChecker`/`tank_mode` в тестах — поправить тест, не код.

**Критерии.** Все grep-проверки → 0; pytest зелёный; `python main.py --help` не требуется, но `python -c "import sys; sys.path[:0]=['src','shared']; import core.bot"` импортируется без ошибок.

**Не трогать.** `src/database/models.py`, любую торговую логику кроме перечисленных удалений веток.

---

## TZ-07 — Полная pydantic-схема конфига (BACKLOG B-12)

**Модель / режим:** дешёвая, low. По образцу существующих моделей.
**Файлы:** `shared/config_models.py`, `tests/test_config_models.py`.

**Шаги.**
1. Добавить модели для секций `websocket`, `metrics`, `cache`, `api_retry`, `scanner`, `indicators`, `stochastic`, `signal_optimizer`,
   `market_conditions`, `toxic_flow`, `dispatcher` (c вложенным `dynamic_min_score`), `scanner_protection`, `symbols: list[str]`, `exchange`.
   Типы и дефолты брать из `shared/config.json` и из `shared/config.py:58-111` (в-код дефолты).
2. Для новых моделей `model_config = ConfigDict(extra='forbid')`. Для `TradingConfig`/`HydraNetConfig` `extra='allow'` **оставить** (там реально ~25 незадокументированных полей; переводить на forbid — отдельная задача после инвентаризации).
3. `validate_config`: валидировать все секции; неизвестная секция верхнего уровня → ошибка `ConfigValidationError` с именем секции.
4. Тесты: (a) `shared/config.json` проходит; (b) `toxic_flow.sweep_sell_pct_min = "abc"` → ошибка с путём к полю; (c) неизвестный ключ в `dispatcher` → ошибка; (d) секция `arbitrage` (удалена 2026-09-09) → ошибка «unknown section».

**Критерии.** `python -c "import json,sys; sys.path.insert(0,'shared'); from config_models import validate_config; validate_config(json.load(open('shared/config.json')))"` → без ошибок; pytest зелёный.

**Не трогать.** Значения в `config.json`; `config.py` кроме, при необходимости, передачи всего словаря в `validate_config` (уже так).

---

## TZ-08 — Инфра-гигиена (BACKLOG B-14, B-15)

**Модель / режим:** дешёвая, low. Применение на сервере — только оператор.
**Файлы:** `Dockerfile`, `docker-compose.yml`, `.env.example`, `DEPLOY.md`.

**Шаги.**
1. `Dockerfile`: после копирования исходников добавить `RUN useradd -r -u 10001 hydra && chown -R hydra:hydra /app` и `USER hydra`. Учесть, что `shared/state` — volume: в `docker-compose.yml` для `hydra-bot` добавить `user: "10001:10001"` **или** оставить chown на первый запуск — выбрать первое. Проверить, что `logs/` создаётся (`logger_setup.py` делает `os.makedirs(LOGS_DIR)` в `/app/logs` — нужен `RUN mkdir -p /app/logs /app/shared/state && chown ...`).
2. `docker-compose.yml` Grafana: `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD:-triada2024}` → `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD:?set GRAFANA_PASSWORD in .env}` (обязательная переменная, без дефолта).
3. `.env.example`: добавить `GRAFANA_PASSWORD=` с комментарием.
4. `DEPLOY.md`: в чек-лист — «`GRAFANA_PASSWORD` задан», «после обновления образа: `docker compose up -d --build --remove-orphans`».

**Критерии.** `docker compose config --quiet` проходит при заданном `GRAFANA_PASSWORD` и падает с понятной ошибкой без него; `docker build .` проходит локально (если Docker доступен; иначе — отметить в отчёте).

---

## TZ-09 — Разбор корня репозитория (BACKLOG B-07)

**Модель / режим:** дешёвая, low.
**Файлы:** только перемещения/удаления в корне: `analyze_entry.py`, `compare_metrics.py`, `compare_logs.ps1`, `daily_report.csv`,
`trades.db` (корневая копия), `HYDRA_MATH_ANALYSIS.md`, `TRADING_TEST_RESULTS.md`, `roadmap.md`, `grafana_trade_panels.json`,
`upload_to_github.ps1`, `setup_windows.bat`, `run_scanner.bat`, `run_bot.bat`, `archive/`.

**Правило:** `analyze_entry.py`, `compare_metrics.py`, `compare_logs.ps1` → `scripts/analysis/`; `HYDRA_MATH_ANALYSIS.md`,
`TRADING_TEST_RESULTS.md`, `roadmap.md` → `docs/history/`; `grafana_trade_panels.json` → `monitoring/`; `archive/` → `docs/history/archive/`;
`upload_to_github.ps1` — удалить (дублирует `deploy.bat`); `trades.db` в корне и `daily_report.csv` — удалить из git (`git rm --cached`) и добавить в `.gitignore` (`/trades.db`, `/daily_report.csv`; путь боевой БД — `shared/state/trades.db`, он уже под `*.db`).
`run_*.bat`, `setup_windows.bat` — оставить. Перемещать через `git mv`.
**Проверка:** `grep -rn "analyze_entry\|compare_metrics\|grafana_trade_panels" --include=*.py --include=*.md --include=*.yml . | grep -v docs/history` — поправить найденные ссылки (ожидается 0–2 в README).

---

## TZ-10 — Разбор dry-run логов после Фаз 1–3 (ЗАДАЧА C из HANDOFF)

**Модель / режим:** средняя, medium. Только чтение, без правок кода.
**Вход:** `logs/bot.log*` после ≥ 24 ч dry-run с новым кодом.
**Задача:** посчитать и интерпретировать маркеры: `@PRICE_STALE@`, `@IDLE_DEFERRED@`, `@IDLE_REFUSED@`, `@BUY_STATUS_UNKNOWN@`,
`@TP_STATUS_UNKNOWN@`, `@EXIT_STATUS_UNKNOWN@`, `@EXIT_RETRY@`, `@EXIT_HALTED@`, `@BALANCE_UNKNOWN@`, `@CAPITAL_EVAL_SKIP@`,
`@HEALTH_FAIL@`, `@WS_RECONNECT@`, `@WS_FATAL@`, `@LOOP_ERROR@`, `Traceback`.
Ожидания: `@EXIT_HALTED@`, `@WS_FATAL@`, `@LOOP_ERROR@`, `Traceback` — 0; `@PRICE_STALE@` — редко и только на неликвидных символах;
`@*_UNKNOWN@` — единичные всплески, совпадающие по времени с `@WS_RECONNECT@`/сетевыми ошибками, не постоянный фон.
**Выход:** таблица маркер → количество → интерпретация → «норма / требует внимания», в `docs/audit/02_dryrun_review_<дата>.md`. Без рекомендаций по изменению кода — только факты и вопросы.

---

## Порядок

TZ-06 (мёртвый код, чтобы Фаза 3 правила чистые файлы) → TZ-04 (надзор) → TZ-05 (учёт) → TZ-07 → TZ-08 → TZ-09.
Фаза 3 (сетка) — сильная модель, после TZ-06. TZ-10 — после Фазы 3 и суток dry-run.
