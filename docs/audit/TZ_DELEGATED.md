# ТЗ для делегируемых задач (после Фазы 1)

Состояние на 2026-10-26: HEAD `4d4c1b2`, 269 тестов зелёные. Фазы 1–3 и TZ-04…TZ-09, TZ-11, TZ-12A закрыты;
демо-этап 2 идёт на `4d4c1b2` (B-24, B-27, B-28 исправлены). TZ-04…TZ-12 ниже оставлены как история и образец формата.
**Актуальная очередь — TZ-13…TZ-20 и раздел «План внедрения» в конце файла.**
Сильная модель (high): TZ-17 (решения по выходу), TZ-18, TZ-19, TZ-20. Остальное делегируемо.

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

## TZ-11 — Demo Trading + предохранитель live (подготовка к тест-плану)

**Модель / режим:** дешёвая, low.
**Файлы:** `src/api/bybit_client.py` (только `BybitClient.__init__`), `shared/config.json`, `shared/config_models.py`, `main.py`,
`.env.example`, `docs/HANDOFF.md`, `tests/test_exchange_truth.py` (добавить), новый `tests/test_live_guard.py`.

**Зачем.** Dry-run — это `if is_dry_run:` в десятках мест и виртуальные ордера; он не исполняет live-код (fetch_order,
cancel, баланс, partial fill). Bybit Demo Trading (`api-demo.bybit.com`) даёт боевое API с реальными ценами и виртуальным
балансом — единственный способ прогнать Фазу 1 и сетку без денег.

**Шаги.**
1. `shared/config.json` → секция `exchange`: добавить `"demo_trading": false`. `config_models.py`: поле `demo_trading: bool = False`
   в модели `exchange` (если модели нет — см. TZ-07; минимально — добавить в существующую валидацию).
2. `BybitClient.__init__`: ключи брать из `BYBIT_DEMO_API_KEY`/`BYBIT_DEMO_API_SECRET`, если `demo_trading`, иначе из
   `BYBIT_API_KEY`/`BYBIT_API_SECRET`. При `demo_trading` после создания `ccxt.bybit(...)` вызвать
   `self.exchange.enable_demo_trading(True)`; лог `@EXCHANGE_INIT@ ... mode=DEMO`. Сигнатура `__init__(api_key=None, secret=None, demo=None)`;
   `demo=None` → читать из `config` (импорт `from config import config` уже доступен через `shared`).
3. **Live guard** в `main.py` перед `TradingBot(...)`: если `trading.dry_run is False` **и** `exchange.demo_trading is False`
   **и** `os.getenv('HYDRA_LIVE_CONFIRM') != 'yes'` → `print("[FATAL] @LIVE_GUARD@ real trading requires HYDRA_LIVE_CONFIRM=yes")`,
   `return 2`. Проверка выполняется до импорта `core.bot`.
4. `.env.example`: `BYBIT_DEMO_API_KEY=`, `BYBIT_DEMO_API_SECRET=`, `HYDRA_LIVE_CONFIRM=` с комментариями.
5. `docs/HANDOFF.md` §3 «Конфиг»: таблица трёх режимов — `dry_run=true` (виртуальные ордера) / `dry_run=false, demo_trading=true`
   (живой код, демо-биржа) / `dry_run=false, demo_trading=false, HYDRA_LIVE_CONFIRM=yes` (деньги).
6. Тесты: (a) `BybitClient(demo=True)` с фейковым ccxt-объектом → `enable_demo_trading(True)` вызван, ключи взяты из DEMO-переменных;
   (b) `main()` при `dry_run=false, demo_trading=false` без `HYDRA_LIVE_CONFIRM` → код возврата 2, `core.bot` не импортирован
   (проверять через `sys.modules`); (c) с `HYDRA_LIVE_CONFIRM=yes` guard пропускает (дальше замокать `TradingBot`).

**Критерии.** `grep -n "enable_demo_trading" src/api/bybit_client.py` → 1; `grep -n "HYDRA_LIVE_CONFIRM" main.py` → ≥1; pytest зелёный.
**Не трогать.** Логику ордеров, WS.

---

## TZ-12 — Офлайн-калибровка весов диспетчера: данные, разметка, анализ

Онлайн-обучение (`dispatcher.feedback_loop`, `learning_rate`) **не включать**: градиентный шаг по PnL одной сделки при
N < тысяч — случайное блуждание весов, меняющее поведение бота, который генерирует следующие данные.
Веса подбираются офлайн по размеченным кандидатам; исходом считается **ценовой путь после сигнала**, а не исполненная сделка —
это даёт данные по всем кандидатам, включая невыбранных, и не зависит от того, dry-run это, демо или реал.

Критика, учтённая в ТЗ (2026-09-30): (1) у существующих строк `dispatcher_features` нет реальных данных о ликвидности —
спред в сканере считается по **синтетическим** bid/ask (`bybit_client.py:86-91`), поэтому сначала инструментирование (12A);
(2) исход должен считаться той же `utils.realized_pnl`, что и бот, с проскальзыванием для маркет-выходов;
(3) `calculate_score` уже нелинейный (сигмоида дампа с центром 4.5%, `dispatcher.py:83`; клампы) — проверять надо формы
преобразований, а не только веса.

### TZ-12A — Инструментирование (в бот)

**Модель / режим:** средняя, medium (трогает WS-клиент и БД).
**Файлы:** `src/api/bybit_client.py:257-272`, `src/database/models.py` (схема + `log_dispatcher_features`),
`src/core/states/scanning.py` (`_validate_candidate`, вызовы `log_dispatcher_features`), `src/core/grid/hydra_net.py` и
`src/core/states/buying.py` (только сигнатура вызова), `tests/test_exchange_truth.py` или новый тест WS-парсера.

1. `bybit_client.py` обработчик `orderbook.1`: кроме размеров сохранять **цены** `bids[0][0]` → `existing['bid']`,
   `asks[0][0]` → `existing['ask']`, и `existing['book_ts'] = now`. В `_update_price` (тикер) **не перетирать** bid/ask синтетикой,
   если `book_ts` свежее 5 с; синтетика — только когда стакана нет вовсе. Пометить `existing['bid_ask_source'] = 'book' | 'synthetic'`.
   Это закрывает аудит H6 (спред-фильтр и лимит-чейз по выдуманным ценам).
2. `models.py`: миграция (по образцу `:98-104`) — колонки `spread_pct REAL`, `bid_vol REAL`, `ask_vol REAL`, `turnover24h REAL`,
   `bid_ask_source TEXT`, `tp_pct REAL`, `sl_pct REAL`, `hold_sec REAL`, `entry_ask REAL`, `source TEXT` (`dry_run|demo|live`).
   `log_dispatcher_features(...)` — новые параметры со значениями по умолчанию `None`, чтобы старые вызовы не ломались.
3. `scanning.py` `_validate_candidate`: передавать `spread_pct` (из реальных bid/ask, `None` если `bid_ask_source != 'book'`),
   `bid_vol`, `ask_vol`, `turnover24h`, `entry_ask`, `tp_pct` (из `dispatcher.get_grid_params(mode).take_profit_pct` либо конфиг),
   `sl_pct` (`trading.panic_stop`), `hold_sec` (`trading.hard_exit_timeout_sec`), `source`.
4. Тест: фрейм `orderbook.1` с `[[0.99,100]]/[[1.01,80]]` → в кеше `bid=0.99, ask=1.01, source='book'`; последующий тикер без bid/ask
   не перетирает их.

**Критерии.** `PRAGMA table_info(dispatcher_features)` содержит новые колонки; в dry-run логе `@DISPATCHER_LOG@` строки пишутся
с `bid_ask_source='book'` для ликвидных пар; pytest зелёный.

### TZ-12B — Разметка (`scripts/analysis/label_candidates.py`)

**Модель / режим:** дешёвая, low. Читает `trades.db` и OHLCV через ccxt (публичные эндпоинты, ключи не нужны). Ничего не пишет в код бота.

Вход: строки `dispatcher_features` с `entry_ask IS NOT NULL` (т.е. после 12A). Для каждой:
1. **Фильтр ликвидности** (критика №1): пропустить, если `bid_ask_source != 'book'`, или `spread_pct > trading.spread_max`,
   или `turnover24h < trading.min_volume_usdt`, или `min(bid_vol, ask_vol) * entry_ask < 3 * slot_size`. Причину отсева записать в колонку `skip_reason`.
2. Загрузить 1m OHLCV от `timestamp` на `hold_sec + 60` секунд вперёд.
3. **Консервативное исполнение**: вход по `entry_ask` (не `last`). TP = `entry_ask * (1 + tp_pct/100)` засчитывается только если
   `high >= TP + tick` (прошли сквозь уровень, а не коснулись). SL = `entry_ask * (1 - sl_pct/100)` — по касанию (`low <= SL`).
   Если в одной свече и TP, и SL — считать **SL** (пессимизм). Ни то, ни другое за `hold_sec` → `timeout`, выход по `close` последней свечи.
4. **Исход в USDT** — только через `shared.utils.realized_pnl(buy, sell, qty, fee_pct, slippage_pct)`: `tp` → `slippage=0`;
   `sl`, `timeout` → `slippage = trading.panic_slippage_pct` (критика №2). `qty = slot_size / entry_ask`. Никакой собственной формулы комиссий.
5. Выход: таблица `candidate_labels` в той же БД (или CSV в `data/`): `feature_id, label ∈ {tp, sl, timeout, skipped}, skip_reason,
   bars_to_outcome, net_usdt, net_pct, labelled_at`. Идемпотентно: уже размеченные строки не пересчитывать.

**Критерии.** Прогон на dry-run БД без ошибок; `SELECT label, COUNT(*) FROM candidate_labels GROUP BY label` даёт все четыре класса;
юнит-тест на синтетических свечах: (a) TP и SL в одной свече → `sl`; (b) касание TP без прохода → не `tp`; (c) `timeout` использует slippage.

### TZ-12C — Анализ (`scripts/analysis/score_vs_outcome.py` + отчёт)

**Модель / режим:** средняя, medium; выводы — сильная модель/оператор. Только чтение данных.
Требование к данным: ≥ 300 размеченных (не `skipped`) кандидатов, ≥ 7 календарных дней.

1. **Формы признаков до весов** (критика №3): для каждого сырого признака (`confidence, rvol_spike, dump_depth, obi_skew, btc_1h`)
   — децили → доля `tp`, средний `net_pct`, N в бине. График/таблица. Проверить монотонность; для `dump_depth` — явно сравнить с
   текущей сигмоидой (центр 4.5, наклон 0.7). Если зависимость U-образная/горб — предложить замену преобразования, **не** веса.
2. **Скор vs исход**: децили текущего `score` → доля `tp`, `net_pct`. Это ответ на вопрос «диспетчер вообще что-то предсказывает?».
3. **Подбор весов**: логистическая регрессия `P(tp)` на *преобразованных* признаках (те же `c_norm, r_norm, d_norm, o_norm, btc_ok`,
   что в `calculate_score`), обучение на первых 70 % **по времени**, проверка на последних 30 %. Никаких деревьев/бустинга.
4. **Значимость**: средний `net_pct` топ-квартиля по новому скору vs по дефолтному — **блочный бутстрап по дням** (кандидаты одного часа
   коррелированы), 95 % интервал. Если интервал накрывает 0 — вывод «неразличимо», веса **не менять**. Это нормальный результат.
5. Разбивка по `source` (`dry_run|demo|live`) — исходы не должны систематически расходиться; если расходятся, разбираться с 12B, а не с весами.
6. Отчёт `docs/audit/03_dispatcher_calibration_<дата>.md`: таблицы из п.1–5, вывод, предложенные веса **или** решение оставить дефолт.
   Применение весов — только вручную: `dispatcher_weights.json` → демо 2–3 дня → live.

**Не делать.** Не включать `feedback_loop`; не менять `dispatcher.py` в рамках 12C (изменение преобразований — отдельное ТЗ по результатам отчёта).

---

# Блок «Эффективность» (2026-10-26)

Исходные цифры (демо-этап 2, 33 закрытых сделки, slot $6): средний плюс +$0.020 (0.34%), средний минус −$0.089 (1.48%),
матожидание ≈ 0; для нуля при таком R:R нужен win-rate 82%, факт 76%. Половина «побед» — breakeven/таймаут около нуля, а не TP.
Пыль от комиссии-в-монете на грубых лотах (QNT: $0.19 = 3.2% слота за сделку) не попадает в `trades.db` и превышает TP.

**Принцип блока:** ни одно изменение порогов, TP, стопа или признаков входа не деплоится без цифр из харнесса TZ-13.
Сначала измерение (TZ-13), параллельно — механические потери (TZ-14) и надёжность (B-23); затем вселенная (TZ-15) и новые
признаки *только в лог* (TZ-16); решения по выходу (TZ-17) — по отчёту; архитектура (TZ-18…TZ-20) — отдельной веткой.

Уже есть и переиспользуется, не делать заново: реальный bid/ask из `orderbook.1` (TZ-12A, `bid_ask_source='book'`),
`publicTrade`-агрегатор (`bybit_client.py:158`, кормит `ToxicFlowFilter`), ATR-кэш (`_grid_atr`), `shared.utils.realized_pnl`.

---

## TZ-13 — Харнесс исходов: разметка кандидатов и отчёт (расширяет TZ-12B/12C)

Цель: превратить ~13 сделок/день в сотни размеченных сэмплов/день — все кандидаты, дошедшие до тяжёлой валидации,
с их ценовым путём после сигнала. Это единственный способ ответить на «какой TP», «где стоп», «что режут фильтры».

### TZ-13A — Логировать отказы, а не только прошедших (в бот)

**Модель / режим:** дешёвая, low.
**Файлы:** `src/core/states/scanning.py` (`_validate_candidate`, `_handle_scanning_state`), `src/database/models.py`,
`tests/test_dispatcher_features.py`.

**Проблема.** `log_dispatcher_features(trade_id=0, ...)` пишется только для кандидатов, прошедших *все* фильтры
(`scanning.py:217+`). Отвергнутые (`rvol_low`, `shallow_drop`, analyzer, `rec`, `conf_low`, `btc_trend`, `corr_low`,
`dyn_min_score`) не попадают в БД — значит, нельзя оценить, что именно режут фильтры и не режут ли они лучшие входы.

**Шаги.**
1. `models.py`: миграция `dispatcher_features` (по образцу `:98-111`): `reject_reason TEXT` (NULL = прошёл), `picked INTEGER DEFAULT 0`,
   `sigma_1m REAL`, `dump_z REAL`, `vol_decay REAL`, `higher_low INTEGER`, `sell_ratio REAL` (последние пять — для TZ-16, сейчас NULL).
   `log_dispatcher_features(..., reject_reason=None, picked=0, ...)`.
2. `_validate_candidate`: каждый `return None` после получения OHLCV → перед ним одна запись с `reject_reason` и теми признаками,
   что уже известны (`rvol_spike=real_rvol`, `dump_depth=drop`, `confidence` если analyzer отработал, иначе NULL; контекст
   ликвидности из `_candidate_context` — вызвать его до фильтров, он не делает REST). Отказ до OHLCV (`fetch failed`) не логировать.
3. `_handle_scanning_state`: при `@SCAN_PICK@` обновить строку победителя `picked=1` (нужен `id` строки — `log_dispatcher_features`
   должен возвращать `lastrowid`; проверить, возвращает ли; если нет — добавить). При `@SCAN_REJECT_DYNAMIC@` — `reject_reason='dyn_min_score'`
   для `best`.
4. Тесты: (a) кандидат с `rvol < min_rvol` → одна строка с `reject_reason='rvol_low'`; (b) прошедший → `reject_reason IS NULL`;
   (c) `picked=1` ровно у одного после `@SCAN_PICK@`.

**Критерии.** После суток демо: `SELECT reject_reason, COUNT(*) FROM dispatcher_features WHERE timestamp > ? GROUP BY 1` даёт
≥ 5 классов; число строк/сутки ≥ 10× числа `@GRID_LAUNCH@`. pytest зелёный.
**Не трогать.** Логику фильтров (порядок, пороги). Это чистое наблюдение.

### TZ-13B — Разметчик `scripts/analysis/label_candidates.py`

**Модель / режим:** дешёвая, low. Не трогает код бота. Публичные OHLCV через ccxt (без ключей).
Основа — TZ-12B целиком (фильтр ликвидности, консервативное исполнение, `realized_pnl`, идемпотентность). Дополнения:

1. **Ценовой путь**, а не один исход: для каждой строки (включая отвергнутые) по 1m OHLCV от `timestamp`: `mfe_5, mae_5, mfe_15, mae_15,
   mfe_30, mae_30, mfe_60, mae_60` (в % от `entry_ask`; MFE по `high`, MAE по `low`), `close_5/15/30/60`, `bars_to_mfe_30`.
2. **Сетка исходов**: для TP ∈ {0.5, 0.8, 1.0, 1.5, 2.0, retrace30, retrace50} × SL ∈ {0.8, 1.2, 1.6, 2.0, none} × hold ∈ {900, 1800, 3600}
   — `label` и `net_pct` по правилам 12B (`retraceX` = `drop × X/100`, ограничено [0.5%, 2.5%]). Хранить в длинной таблице
   `candidate_outcomes(feature_id, tp_rule, sl_pct, hold_sec, label, bars, net_pct)`. Базовый вариант (текущий конфиг) — отдельно в `candidate_labels` как в 12B.
3. **Сетка с усреднением** (для решения по этапу 3): вариант `grid1` — если `low <= entry×(1−grid_distance)` до TP/SL, вход ×1.5 по этой цене,
   TP от средней; `net_pct` от суммарного вложения. Пессимизм тот же (TP и SL в одной свече → SL).
4. Таблица `candidate_paths` отдельно от меток — пути не зависят от правил и считаются один раз.
5. CLI: `--db`, `--since`, `--limit`, `--dry-run`; прогресс в stderr; повторный запуск досчитывает только новые `feature_id`.

**Критерии.** Юнит-тесты 12B + (d) путь: синтетические свечи → известные MFE/MAE; (e) `retrace50` при `drop=4` даёт TP 2.0%, при `drop=1` — 0.5% (клип);
(f) `grid1` при касании уровня усредняет корректно. Прогон на серверной `trades.db` (скачать `scp`) без ошибок.

### TZ-13C — Отчёт `scripts/analysis/outcome_report.py` → `docs/audit/04_outcomes_<дата>.md`

**Модель / режим:** средняя, medium; **выводы — сильная модель/оператор**. Только чтение. Требование: ≥ 300 строк с путями, ≥ 7 дней.

1. Матрица TP × SL × hold: `N`, доля `tp`, средний `net_pct`, матожидание в $ при slot 6 — для прошедших фильтры (`reject_reason IS NULL`).
   Отдельно та же матрица для каждого `reject_reason`: если у отвергнутых класс `conf_low` матожидание выше, чем у прошедших — фильтр вреден.
2. Распределения: время до MFE_30 (гистограмма) — ответ на «нужен ли hold 1800 с»; MAE победителей по текущему правилу (перцентили 50/80/90/95)
   — ответ на «режет ли стоп 1.2% своих».
3. Децили `dump_depth`, `rvol_spike`, `confidence`, `spread_pct`, `turnover24h` → доля `tp`, `net_pct` (п.1 из 12C).
4. `grid1` vs без усреднения — матожидание и хвост (5-й перцентиль `net_pct`).
5. Разбивка по `source` и по часу суток UTC.
6. Выводы формулируются как **предложения конфига** для TZ-17 с интервалами (блочный бутстрап по дням, п.4 из 12C). «Неразличимо» — нормальный результат.

---

## TZ-14 — Механические потери: грубые лоты, пыль, мёртвый partial-TP (BACKLOG B-25)

**Модель / режим:** дешёвая, low/medium.
**Файлы:** `src/core/states/scanning.py` (или `src/core/scanner.py` `DynamicSymbolManager`), `src/core/bot.py` (`_single_position_ok` уже
считает holdings), `shared/metrics.py`, `shared/config.json`, `shared/config_models.py`, тесты.

**Факт.** Комиссия берётся в монете, остаток до шага лота непродаваем; размер пыли ограничен одним шагом лота **независимо** от того,
как округлять покупку (покупка тоже обязана лежать на шаге). Поэтому «докупать `amount/(1−fee)`» не решает — решает фильтр по
отношению `шаг × цена / slot`, плюс видимость накопленного.

**Шаги.**
1. Фильтр на этапе лёгкого скана (до REST): `lot_step × ask / slot_size > trading.max_lot_step_pct` (новый ключ, default `0.5`) →
   кандидат отбрасывается, лог `@LOT_TOO_COARSE@ {symbol} step=${...} = {pct}% of slot` (одна строка в 10 мин на символ через `log_every`).
   `lot_step` из `markets[symbol]['precision']['amount']` (ccxt TICK_SIZE). Для TZ-13A это `reject_reason='lot_coarse'`.
2. Метрика `hydra_dust_usdt` (gauge): сумма `holdings × approx_price` по всем не-USDT монетам ниже `_dust_threshold`; обновлять в
   `_evaluate_capital` (раз в 30 с, баланс там уже запрашивается — переиспользовать один `fetch_balance`, не добавлять второй).
   Панель в Grafana — рядом с `hydra_session_profit_usdt`, чтобы «прибыль» читалась вместе с утечкой.
3. **Partial TP мёртв по конфигу**: `partial_tp_activation_pct: 1.0` > `hydra_net.take_profit_pct: 0.8` — TP исполняется раньше активации.
   Решение: в `config_models.py` валидатор — если `partial_tp_activation_pct > 0` и `>= hydra_net.take_profit_pct` → `@CONFIG_WARN@` в лог
   (не ошибка). В `shared/config.json` и на сервере поставить `partial_tp_activation_pct: 0.0` (выключить) до решения по TZ-17.
   Код партиала не удалять.
4. Тесты: (a) символ с `step×price = 3% slot` отфильтрован, с `0.1%` — нет; (b) gauge считает только монеты ниже порога; (c) валидатор конфига.

**Критерии.** В демо-логе за сутки `@LOT_TOO_COARSE@` присутствует для QNT-подобных пар; `hydra_dust_usdt` в Grafana; pytest зелёный.
**Не трогать.** `_dust_threshold`, `_entry_sellable` (сделаны в `4d4c1b2`).

---

## TZ-15 — Вселенная символов по обороту вместо статического списка

**Модель / режим:** средняя, medium (трогает WS-подписки).
**Файлы:** `src/core/scanner.py` (`DynamicSymbolManager`), `src/api/bybit_client.py` (`WebSocketListener`: добавить `update_symbols()`
или документировать перезапуск), `src/core/bot.py` (`_apply_market_universe`, периодический refresh), `shared/config.json`,
`shared/config_models.py`, тесты.

**Проблема.** 127 пар в `config.json` подобраны руками; среди них пары с оборотом, где «дамп» — один чужой маркет-ордер.
Ловля ножей — это mean reversion ликвидного инструмента; на тонких парах это ловля манипуляций.

**Шаги.**
1. Секция конфига `universe`: `{"mode": "static" | "turnover", "top_n": 80, "min_turnover_usdt": 5000000, "refresh_sec": 86400,
   "exclude_patterns": ["3L","3S","2L","2S","USDC","USDE","DAI","FDUSD"]}`. `static` = текущее поведение (список `symbols`).
2. `turnover`: раз в `refresh_sec` (и при старте) `fetch_tickers()` по всем активным spot `*/USDT`; отсев по `exclude_patterns`, по
   `quoteVolume < min_turnover_usdt`, по лот-фильтру TZ-14; взять `top_n` по `quoteVolume`. Открытая позиция и символы в `symbol_cooldown`
   из вселенной не выкидываются до закрытия.
3. Смена состава → `ws_listener.update_symbols(new)`: отписка от ушедших (`unsubscribe`), подписка на новые батчами ≤ 10 (как в `start`).
   Если реализация отписки окажется нетривиальной — допустимо `stop()`+`start(new)` с логом `@WS_RESUBSCRIBE@`, но **не** в момент
   `BUYING/IN_POSITION/EXITING` (отложить до IDLE).
4. Лог `@UNIVERSE@ n={N} added=[...] removed=[...] min_turnover=...`. `hot_symbols.txt` (сканер) не трогать — он поверх вселенной.
5. Тесты: (a) отбор/исключения на синтетических тикерах; (b) refresh не выкидывает символ открытой позиции; (c) `update_symbols` батчит ≤ 10.

**Критерии.** На демо после включения `mode=turnover`: `@MARKET_UNIVERSE@`/`@UNIVERSE@` в логе, `@WS_TICK@ Using N/N`, здоровье 1.0;
`@PRICE_STALE@` не растёт. pytest зелёный.
**Не трогать.** Логику сканера (`scanner.py` кроме `DynamicSymbolManager`), пороги.

---

## TZ-16 — Признаки входа в единицах волатильности и истощения (сначала только лог)

**Модель / режим:** средняя, medium. **Гейт: TZ-13A задеплоен** (есть колонки). Признаки *только пишутся* в `dispatcher_features`;
в фильтры и скор не входят до отчёта TZ-13C по ним.
**Файлы:** `src/core/states/scanning.py` (`_validate_candidate` — OHLCV 60×1m там уже есть), `src/core/toxic_flow.py` (прочитать
агрегат sell/buy), тесты.

**Признаки** (все из уже имеющихся данных, ноль новых REST-вызовов):
- `sigma_1m` — std log-доходностей за 60 баров (%); `dump_z = drop / (sigma_1m × sqrt(k))`, где `k` — число баров окна дампа
  (как считается `drop` в лёгком скане — взять оттуда же, не изобретать второе окно).
- `vol_decay = volume[-1] / max(volume за окно дампа)` — истощение объёма (1.0 = пик сейчас, < 0.5 = затухает).
- `higher_low` — `low[-1] > low[-2] > min(low за окно дампа)` (0/1).
- `sell_ratio` — доля агрессивных продаж за последние 60 с из `publicTrade`-буфера (`ToxicFlowFilter` уже считает для sweep; вынести
  чистую функцию `aggressor_ratio(trades, window_sec)` без побочных эффектов).

**Шаги.** 1. Чистые функции в `src/indicators/` (новый `src/indicators/dump_features.py`), без состояния, с тестами на синтетике.
2. Вызов в `_validate_candidate` сразу после OHLCV; значения в `dispatcher_features` (и в строки отказов из TZ-13A).
3. Логировать в `@SCAN_VALID@`/`@SCAN_PICK@`: `z={dump_z:.1f} vd={vol_decay:.2f} hl={higher_low} sr={sell_ratio:.2f}`.
**Критерии.** Колонки заполнены для ≥ 95% новых строк; pytest зелёный; поведение входа **не изменилось** (сравнить счётчики
`@SCAN_PICK@`/сутки до и после — в пределах шума).

**Следующий шаг (отдельное ТЗ после 13C):** если децили `dump_z`/`vol_decay` показывают монотонную связь с `net_pct` —
заменить фиксированный `dump_threshold −3.5%` на `dump_z < −k` и добавить гейт истощения. Ichimoku/MACD/Stochastic в `confidence`
на 1m — проверить тем же отчётом; при отсутствии связи обнулить их веса в `signal_optimizer.signal_weights` (конфиг, не код).

---

## TZ-17 — Правила выхода по данным: TP от глубины, стоп, breakeven, hold

**Модель / режим:** сильная, high (решения), исполнение — средняя. **Гейт: отчёт TZ-13C.**
**Файлы:** `src/core/grid/hydra_net.py` (`_grid_tp_pct`, `_launch_grid_network`), `src/core/states/in_position.py` (breakeven/hold —
только чтение конфига), `shared/config.json`, `shared/config_models.py`, тесты.

**Механика (готовится заранее, включается по отчёту):**
1. `hydra_net.take_profit_mode: "fixed" | "retrace"`, `retrace_pct: 40`, `tp_min_pct: 0.5`, `tp_max_pct: 2.5`. В `retrace` при запуске
   сетки `state_data['take_profit_pct'] = clip(drop × retrace_pct/100, tp_min, tp_max)`. **Приоритет:** явный `take_profit_pct` диспетчера
   по режиму (`get_grid_params`) → retrace → фиксированный; приоритет задокументировать в `_grid_tp_pct`.
2. `trading.breakeven_mode: "price" | "trailing" | "off"`: `trailing` — после `breakeven_timeout_sec` вместо переноса TP на вход
   включается trailing от локального максимума с `trailing_callback_pct` (код trailing уже есть для партиала — переиспользовать).
3. `hard_exit_timeout_sec`, `panic_stop`, `breakeven_timeout_sec` — значения из отчёта (перцентили времени до MFE и MAE победителей),
   меняются **только в конфиге**, с записью «почему» в `docs/audit/04_outcomes_<дата>.md`.
4. Тесты: (a) retrace при drop 4% → TP 1.6%, при drop 1% → 0.5% (клип), при `fixed` — конфиг; (b) приоритет диспетчера; (c) trailing-режим
   не ставит breakeven-ордер.

**Критерии.** Включение любого режима — по одному изменению за деплой, с контрольной точкой через 2–3 дня и сравнением
среднего плюса/минуса с таблицей в начале блока. Партиал (`partial_tp_*`) — решение в том же отчёте: починить активацию ниже TP или удалить код.

---

## TZ-18 — Приватный WebSocket: `order`, `execution`, `wallet` вместо REST-поллинга

**Модель / режим:** сильная, high. Отдельная ветка, демо не менее 3 дней перед слиянием в основную линию.
**Файлы:** `src/api/bybit_client.py` (новый класс `PrivateWSListener`; `fetch_order`, `get_coin_balance`, `get_free_usdt`,
`get_non_usdt_holdings` — чтение из кеша с REST-fallback), `src/core/bot.py` (старт/стоп), `shared/metrics.py`, `tests/`.

**Зачем.** Сейчас в позиции 2 REST-запроса/с (`fetch_order`, `fetch_balance`), и каждая гонка «баланс уже 0, статус ещё open»
— баг класса B-28. С событиями бот *получает* факты «исполнено / отменено / баланс изменился», а не опрашивает их.

**Шаги.**
1. `PrivateWSListener`: `wss://stream.bybit.com/v5/private` (демо: `wss://stream-demo.bybit.com/v5/private`); аутентификация
   `{"op":"auth","args":[api_key, expires, hmac_sha256(secret, f"GET/realtime{expires}")]}`; топики `order`, `execution`, `wallet`;
   ping каждые 20 с; реконнект как у публичного. Кеш: `orders[order_id] -> ccxt-подобный dict` (маппинг статусов: `New/PartiallyFilled→open`,
   `Filled→closed`, `Cancelled/Deactivated→canceled`, `Rejected→rejected`; `filled=cumExecQty`, `average=avgPrice`, `side` lower),
   `balances[coin] -> {equity, free, ts}`; каждый элемент с `ts` события.
2. `BybitClient.fetch_order(id)`: если в кеше и `ts` свежее `PRIVATE_WS_FRESH_SEC` (default 5 с для `open`, без ограничения для
   терминальных статусов) → вернуть из кеша; иначе REST как сейчас. `get_coin_balance` и `get_free_usdt` — аналогично из `wallet`.
   Если приватный WS не активен — поведение полностью текущее.
3. **Сверка**: раз в 60 с (и обязательно при переходах состояний) принудительный REST и сравнение с кешем; расхождение статуса или баланса
   > `max(step, 0.1%)` → `@PRIVATE_WS_DRIFT@` + кеш перезаписывается REST-данными. Это защита от пропущенных событий.
4. Метрики: `hydra_rest_calls_total{endpoint}` (счётчик в обёртках `BybitClient`), `hydra_private_ws_events_total{topic}`,
   `hydra_private_ws_connected` (gauge).
5. Тесты: скриптованные кадры `order`/`wallet` → кеш; маппинг статусов; устаревший кеш → REST; сверка перезаписывает кеш при расхождении.
   Авторизацию — тест подписи по известному вектору.

**Критерии.** На демо: `rate(hydra_rest_calls_total[5m])` в IN_POSITION ≤ 0.1/с (было ~2/с); `@*_STATUS_UNKNOWN@` при живой сети = 0;
`@PRIVATE_WS_DRIFT@` — единицы в сутки и только при реконнектах. Тест обрыва сети (этап 4 runbook) повторить с приватным WS.
**Не трогать.** Логику состояний: она продолжает звать те же `fetch_order`/`get_coin_balance`.

---

## TZ-19 — Типизированное состояние позиции вместо `state_data: dict`

**Модель / режим:** сильная, high. Инкрементально, после TZ-18. Исходный долг: дрейф ключей (`amount`/`total_qty`, `order_id`/`tp_order_id`,
`buy_time` с двумя смыслами) — половина истории аудита.

1. Шаг 1 (безопасный): pydantic-модель `PositionState` со всеми ключами, которые сегодня встречаются в `state_data` (инвентаризация
   `grep -o "state_data\[['\"][a-z_]*" -r src | sort -u` + `.get('...')`), `extra='forbid'`. Используется **только** для валидации в
   `_save_state`/`_load_state`: неизвестный ключ → `@STATE_SCHEMA_WARN@` в лог, не исключение. Так модель стабилизируется на живом демо.
2. Шаг 2: `state_data` остаётся dict-совместимым (`PositionState.model_dump()`), но конструируется через модель в `_launch_grid_network`,
   `_enter_trade`, `_end_grid_to_position`, `_transition_to_idle`; методы-мутаторы для переходов (`to_in_position(...)`, `to_exiting(...)`).
3. Шаг 3: типизированные обращения в `in_position.py`/`exiting.py`/`hydra_net.py`, удаление `.get(..., default)` с молчаливыми дефолтами.
**Критерии.** Шаг 1 — 0 `@STATE_SCHEMA_WARN@` за 3 дня демо; каждый шаг — pytest зелёный, без изменения поведения (маркеры за сутки те же).

---

## TZ-20 — Мультипозиция (2–3 слота)

**Гейт:** TZ-18 и TZ-19 в основной линии ≥ 2 недели демо без `@PRIVATE_WS_DRIFT@`-инцидентов и `@STATE_SCHEMA_WARN@`; отчёт TZ-13C
показывает, что пропущенные во время hold сигналы имеют положительное матожидание (иначе смысла нет).
Суть: `Position` становится элементом `positions: dict[symbol, PositionState]`, state machine — per-position; single-position
инвариант заменяется на `len(positions) < max_positions` + per-symbol сверка баланса; капитал — `CapitalRouter` делит `available` на слоты.
Детальное ТЗ пишется после TZ-19; здесь — только место в очереди и гейт.

---

## План внедрения (2026-10-26)

Правила: торговая логика деплоится только вместе с контрольной точкой демо (сутки минимум) и новым `session_start_ts`;
измерительные и инфраструктурные вещи (TZ-13A, TZ-14, B-23) — можно между точками. Одно поведенческое изменение на деплой.

| Шаг | Что | Гейт входа | Критерий выхода | Исполнитель |
|---|---|---|---|---|
| 1 | Контрольная точка этапа 2 на `4d4c1b2` (B-27/B-28) | идёт | `@IDLE_REFUSED@`=0, `170140`=0, sell-строк в БД = `@PROFIT_TAKEN@+@LOSS_TAKEN@+@EXIT_DONE@+@PANIC_SELL_DONE@` | оператор + ИИ (TZ-10-подобный разбор) |
| 2 | **TZ-13A** лог отказов + колонки TZ-16 | шаг 1 без стоп-маркеров | деплой; ≥ 5 классов `reject_reason` за сутки | дешёвая |
| 3 | **TZ-14** лот-фильтр, `hydra_dust_usdt`, partial-TP off | — | деплой вместе с шагом 2 | дешёвая |
| 4 | **B-23** алерты: `hydra_state_stuck`, `BalanceLow`, новый `DustHigh` → Telegram через Grafana Alerting; мёртвые правила удалить | — | тестовый алерт доставлен | дешёвая |
| 5 | **TZ-13B** разметчик; прогон по серверной БД | ≥ 3 дня строк после шага 2 | таблицы `candidate_paths`/`candidate_outcomes` заполнены | дешёвая |
| 6 | **TZ-15** вселенная по обороту | шаг 1 закрыт | деплой; сутки без роста `@PRICE_STALE@`/`@WS_RECONNECT@` | средняя |
| 7 | **TZ-16** признаки в лог | шаг 2 задеплоен | колонки заполнены, входы не изменились | средняя |
| 8 | **TZ-13C** первый отчёт | ≥ 300 строк с путями, ≥ 7 дней | `04_outcomes_<дата>.md` с предложениями конфига и интервалами | средняя + сильная |
| 9 | **TZ-17** одно изменение выхода (ожидаемо: TP-режим или стоп) | отчёт шага 8 | 2–3 дня демо, средний плюс/минус vs таблица блока | сильная |
| 10 | Этап 3 runbook (`max_grid_levels: 3`) | п.4 отчёта (`grid1`) не ухудшает хвост | по runbook | оператор |
| 11 | **TZ-18** приватный WS — отдельная ветка параллельно шагам 5–9 | — | критерии TZ-18 на демо 3 дня, затем merge | сильная |
| 12 | Повтор этапа 4 runbook (обрывы сети) на коде с TZ-18 | шаг 11 | без `@IDLE_ENTER@`/`@PANIC_*` во время блокировки | оператор |
| 13 | **TZ-19** шаг 1 (валидация схемы) | шаг 11 в main | 0 `@STATE_SCHEMA_WARN@` за 3 дня | сильная |
| 14 | Этап 5 runbook (реал, минимальный слот) | шаги 1–13, B-23 закрыт, TZ-17 стабилен ≥ 1 неделю на демо | по runbook | **только оператор** |
| 15 | TZ-19 шаги 2–3, затем TZ-20 | после этапа 5 на малом слоте ≥ 2 недели | по ТЗ | сильная |

Ориентиры по времени (не обещания): шаги 2–4 — первая неделя; 5–7 — вторая; 8–9 — третья (упирается в накопление данных,
а не в код); 11 — параллельно, 1–2 недели; 14 — не раньше, чем через месяц от сегодня.

**Чего не делать в этом блоке:** не включать `dispatcher.feedback_loop`; не добавлять индикаторы без отчёта TZ-13C;
не поднимать `max_grid_levels` и slot до шага 10; не менять два параметра выхода в одном деплое.

---

## Порядок (история до 2026-10-26)

TZ-06 ✓ → TZ-04 ✓ → TZ-05 ✓ → **TZ-11** ✓ (нужен для тест-плана) → **TZ-12A** ✓ (данные начинают копиться сразу) → TZ-07 ✓ → TZ-08 ✓ → TZ-09 ✓ → B-18 ✓ (+ B-19, B-20 ✓, 2026-10-07).
Фаза 3 (сетка) ✓ 2026-10-01. TZ-10 — выполняется как контрольные точки runbook. TZ-12B/12C поглощены TZ-13B/13C.

Тест-план после рихтовки (2026-09-30): dry-run 12–24 ч ✓ → демо `max_grid_levels=1` 2–3 дня (**идёт**, перезапущен 2026-10-25 на `4d4c1b2`) → демо
`max_grid_levels=3` 2–3 дня → демо с искусственными обрывами сети в BUYING/IN_POSITION → реал минимальным слотом,
`HYDRA_LIVE_CONFIRM=yes` руками. Актуальная версия — `docs/RUNBOOK_DEMO.md` и таблица «План внедрения» выше.
