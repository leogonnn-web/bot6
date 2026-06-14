# Карта бота HYDRA / Triada

**Актуальность:** 2026-06-08 (16:03 UTC — редеплой + сброс данных)  
**Версия:** v17.0 + dispatcher (P.2 + P.6 **реально в образе** с 2026-06-08)  
**Назначение:** одностраничная карта, чтобы через неделю вспомнить, где что лежит.

> ⚠️ **Важно (2026-06-08):** до этой даты образ на сервере был собран 2026-06-05 и
> **НЕ содержал** кода P.2/P.6 — фичи числились «deployed» только в доках. Образ
> пересобран (`triada-hydra-bot:latest`), P.2 (`dispatcher.get_min_score`) и
> P.6 (`rejected_cache`) теперь действительно работают. Подробнее — раздел 13.

---

## 1. Общая архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│  Python Ingress (hydra-bot)                                     │
│  ├─ bot.py          — State Machine (IDLE → SCANNING → ...)     │
│  ├─ scanning.py     — сканер + dispatcher + rejected_cache      │
│  ├─ dispatcher.py   — scoring, weights, dynamic_min_score         │
│  ├─ capital_router.py — tiers ($15→$50→$100→$250→$1000)        │
│  ├─ order_manager.py — фасад для всех вызовов биржи           │
│  ├─ health.py       — passive health checks + SIGTERM watchdog   │
│  └─ bybit_client.py — REST + raw WebSocket (V5 spot)            │
├─────────────────────────────────────────────────────────────────┤
│  Go Arb Engine (hydra-arb) — пока OFF в live-конфиге           │
├─────────────────────────────────────────────────────────────────┤
│  Shared State (Docker volume triada_shared-data)                │
│  ├─ trades.db       — SQLite (FIFO PnL, dispatcher_features)    │
│  ├─ config.json     — runtime параметры                         │
│  ├─ capital_state.json — текущий tier + max_grid_levels         │
│  └─ hot_symbols.txt — выход scanner_v3                          │
├─────────────────────────────────────────────────────────────────┤
│  Инфраструктура                                                 │
│  AWS ap-southeast-1  →  Ubuntu + Docker Compose                 │
│  SSH:  ubuntu@54.179.1.197   key: triada-key2.pem               │
│  Logs:  docker logs hydra-bot --since 60m                       │
│  DB:    /var/lib/docker/volumes/triada_shared-data/_data/...    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. State Machine (главный цикл)

```
IDLE ──→ SCANNING ──→ BUYING ──→ IN_POSITION ──→ EXITING ──→ IDLE
  ↑            ↑                                        │
  │            │ (rejected_cache retry)                  │
  └────────────┘←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←┘
```

| State | Что происходит | Ключевой @TAG@ |
|-------|----------------|----------------|
| `IDLE` | Ждёт, пока нет позы | `@IDLE@` |
| `SCANNING` | Сканер ищет кандидатов, dispatcher считает score | `@BG_SCAN@`, `@SCAN_VALID@`, `@SCAN_PICK@` |
| `BUYING` | Ставит лимитку/маркет на вход | `@BUY_ORDER_SEND@`, `@DRY_RUN_BUY@` |
| `IN_POSITION` | Мониторит TP/SL/timeout, HYDRA-NET grid | `@IN_POSITION_LOOP@`, `@GRID_ACTIVE@`, `@GRID_RECALC@` |
| `EXITING` | Limit-chase или market panic sell | `@CHASE_START@`, `@CHASE_BACKSTOP@`, `@PANIC_SELL_DONE@`, `@EXIT_DONE@` |

**Ограничение:** `max_active_slots = 1` (default). Пока `state != IDLE`, новые входы блокируются.

---

## 3. Поток данных (от рынка до БД)

```
WebSocket tickers (Bybit V5 spot)
    ↓
[_update_websocket_stream]  →  tickers_cache[symbol] = {bid, ask, last, volume, ts}
    ↓
[scanning.py _background_scan_loop]  каждые ~5с
    ├─ composite_score = drop% + rvol + obi + ...
    ├─ top-3 → REST-валидация (RVOL, confidence, correlation, BTC trend)
    └─ dispatcher.score() → mode (conservative/normal/red_light)
    ↓
[_handle_scanning_state]
    ├─ heavy validation (RVOL≥1.2, conf≥35%, corr≥0.5, BTC ok)
    ├─ dynamic_min_score(BTC 1h) → порог 0.7/1.0/1.5
    ├─ rejected_cache (мягкие отказы: rvol_low, conf_low, corr_low)
    └─ best → _launch_grid_network()
    ↓
[hydra_net.py]  HYDRA-NET grid
    ├─ Колено 1: slot_size ($3)
    ├─ Колено 2: slot_size × 1.5 ($4.5)
    ├─ Колено 3: slot_size × 1.5² ($6.75)
    └─ entry_price пересчитывается (средневзвешенный)
    ↓
[in_position.py]  мониторинг каждые 5с
    ├─ TP ≥ take_profit_pct (0.8%)? → limit chase → EXITING
    ├─ SL ≥ panic_stop (2%)? → market panic sell → EXITING
    ├─ Timeout ≥ timeout_breakeven (1200с)? → breakeven chase
    └─ Grid fill (DCA)? → `@GRID_RECALC@`, обновление средней цены
    ↓
[exiting.py]  фиксация прибыли/убытка
    ├─ trade_profit = exit_price - entry_price (с комиссией)
    ├─ session_profit += trade_profit
    └─ trades.db: INSERT INTO trades (..., profit, ...)
    ↓
[bot.py _apply_dispatcher_feedback]
    ├─ trades.db: UPDATE dispatcher_features SET trade_id=?, profit=?
    ├─ (если dispatcher_feedback=true) Widrow-Hoff update_weights
    └─ _save_dispatcher_weights() → shared/state/dispatcher_weights.json
```

---

## 4. Dispatcher — детали

### 4.1 Что считает score

| Фича | Откуда | Пример значения |
|------|--------|-----------------|
| `drop_pct` | % падения от high 15m | 1.37 |
| `rvol_spike` | relative volume (proxy или REST) | 2.2 |
| `obi_skew` | order book imbalance | 0.15 |
| `dump_depth` | % от high до low за 15m | 2.5 |
| `btc_1h` | BTC 1h change% (via REST при сканировании) | -0.3 |

### 4.2 Score → Mode

```python
if score >= 3.0:  mode = 'red_light'      # редкий, агрессивный
elif score >= 2.0: mode = 'normal'        # средний
else:              mode = 'conservative'    # чаще всего
```

### 4.3 Dynamic min_score (P.2 deployed)

| BTC 1h зона | min_score | Лог-тег |
|-------------|-----------|---------|
| Crash (≤ -2%) | 1.5 | `@DYNAMIC_MIN_SCORE@ BTC=crash dynamic=1.5` |
| Bearish (≤ -0.8%) | 1.0 | `@DYNAMIC_MIN_SCORE@ BTC=bearish dynamic=1.0` |
| Flet (≤ +0.8%) | 0.7 | `@DYNAMIC_MIN_SCORE@ BTC=flet dynamic=0.7` |
| Bullish (> +0.8%) | 1.0 | `@DYNAMIC_MIN_SCORE@ BTC=bullish dynamic=1.0` |

### 4.4 Rejected Cache (P.6 deployed)

**Мягкие отказы (кэшируются на 45с):**
- `rvol_low` — RVOL < 1.2x
- `shallow_drop` — drop < 1% и RVOL < 2x
- `conf_low` — confidence < 35%
- `corr_low` — корреляция < 0.5

**Жёсткие отказы (не кэшируются):**
- OHLCV fetch failed, analyzer error, BTC trend failed, tank_block

**Retry:** через 45с тот же символ проверяется снова. Если score вырос — `@SECOND_CHANCE@`.

### 4.5 Где хранятся данные

```sql
-- Таблица dispatcher_features (в trades.db на AWS)
CREATE TABLE dispatcher_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER,        -- 0 = лог валидации до исполнения; >0 = исполненная позиция
    timestamp REAL,
    symbol TEXT,
    confidence REAL,
    rvol_spike REAL,
    rvol_local REAL,
    dump_depth REAL,
    obi_skew REAL,
    btc_1h REAL,
    score REAL,
    mode TEXT,
    profit REAL,             -- заполняется при закрытии сделки (NULL = ещё открыта)
    take_profit_pct REAL
);
-- trades: id, symbol, side, amount, price, timestamp, confidence, profit
-- side: buy, buy_grid_complete, sell, sell_partial, sell_panic
```

**Снятие полной статистики эффективности — `tools/server_stats.py`**

Скрипт сам считает срез «с момента последнего обнуления» (граница = самая ранняя
строка `dispatcher_features`). Выдаёт: объём данных, разбивку по режимам, win-rate,
realized PnL по типам выхода, частоту сделок/час, распределение по часам, средние фичи,
топ символов.

```bash
# с Windows (PowerShell), ключ на рабочем столе:
scp -i C:\Users\leogo\Desktop\triada-key2.pem tools\server_stats.py ubuntu@54.179.1.197:/tmp/server_stats.py
ssh -i C:\Users\leogo\Desktop\triada-key2.pem ubuntu@54.179.1.197 "sudo docker cp /tmp/server_stats.py hydra-bot:/tmp/server_stats.py && sudo docker exec hydra-bot python /tmp/server_stats.py"
```

> 💡 Если SSH не подключается, а инстанс `Running` — проверь **Security Group → Inbound
> порт 22**: твой внешний IP (динамический) мог смениться. Обнови правило на «My IP».
> Пинг не показатель — AWS блокирует ICMP по умолчанию.

> 🔄 **2026-06-08 16:03 UTC:** `dispatcher_features` обнулён (было 2133 → 0) для чистого
> сбора под P.2+P.6. Старый снимок — в бэкапе БД (раздел 13). Данные считать
> **только после 2026-06-08 16:03 UTC**.

**Срез на 2026-06-14 22:34 UTC (≈150.5 ч / 6.27 дня сбора):**
| Метрика | Значение |
|---------|----------|
| Размечено сэмплов (profit ≠ null) | **1112** (из 3529 строк; 1113 исполненных позиций) |
| conservative labeled | **912 / 500 ✅** (порог пройден) |
| По режимам | conservative 82.5% · normal 17.2% · red_light **0.3% (10, не растёт)** |
| Win-rate | **92.7%** (по закрытиям) / 91.6% (labeled) |
| Realized PnL (dry-run) | **+$127.13** (~+2%/день, верхняя граница) |
| Частота | **~7.4 позиций/час ≈ 177/день** |
| Avg score | 1.70 (cons 1.62 / normal 2.09 / red_light 0.90) |
| Паник-выходы | 78 / 1280 (6.1%), −$15.91 |
| `rvol_spike` avg | 9.31 |

**⚠️ Калибровка П.4 (preview 2026-06-15): на dry-run бессмысленна.** Win-rate 92% →
нет отрицательных примеров → веса лишь растут до потолка 4.0, важности фич не учат.
`dispatcher_feedback` оставить `false`, веса не трогать. Подробности и предлагаемые
числа — в `docs/dispatcher_backlog.md`. Нужны реальные убытки/слиппедж либо смена
целевой функции (→ П.5).

**Контейнер:** 2026-06-14 ~20:16 UTC чисто перезапустился (рестарт docker-сервиса,
не крах: RestartCount=0, без OOM; хост uptime 13д). Данные непрерывны, потерь нет.

**Прошлые срезы:** 06-09 (18.5ч): 143 labeled, WR 91.9%, +$15.80 · 06-11 (2.9д): 486 labeled, WR 90.8%, +$49.43.

---

## 5. Capital Router

### 5.1 Режимы (tiers)

| Депозит | Tier | `max_grid_levels` | `slot_size` | Комментарий |
|---------|------|-------------------|-------------|-------------|
| $15–$50 | Bootstrap | 1 | $3 | Без Мартингейла |
| $50–$100 | Growth | 2 | $6 | Простой grid |
| $100–$250 | Maturity | 3 | $9 | Полный grid |
| $250–$1000 | Power | 3 | $12 | — |
| $1000+ | Apex | 3 | $15+ | + авто-реинвест (TODO при переходе на реал) |

### 5.2 Как вычисляется tier

```python
# capital_router.py
if equity >= 250:  mode = 'power'
elif equity >= 100: mode = 'maturity'
elif equity >= 50:  mode = 'growth'
else:               mode = 'bootstrap'
```

### 5.3 Session profit

- `session_profit` — просто счётчик, **не реинвестируется** автоматически.
- `slot_size` фиксирован из конфига.
- **При переходе на реал:** добавить `slot_size = base_slot * (1 + session_profit / capital)`.

---

## 6. HYDRA-NET Grid (DCA)

| Колено | Множитель | Сумма (slot=3$) | Цель усреднения |
|--------|-----------|-----------------|-----------------|
| 1 | 1.0 | $3.00 | Первый вход |
| 2 | 1.5 | $4.50 | Усреднение при просадке |
| 3 | 2.25 | $6.75 | Финальное усреднение |

**Take Profit:** 0.8% от средней цены (в conservative).  
**Panic Stop:** 2% от entry.  
**Timeout Breakeven:** 1200с (20 мин).

---

## 7. Базы данных (trades.db)

### 7.1 Таблица `trades`

```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    symbol TEXT,
    side TEXT,          -- 'buy', 'sell', 'buy_grid_complete'
    amount REAL,
    price REAL,
    profit REAL,        -- PnL (для sell-записей)
    timestamp REAL,
    mode TEXT           -- 'conservative' и т.д.
);
```

### 7.2 Таблица `dispatcher_features`

(см. раздел 4.5)

### 7.3 Как подключиться к живой БД

```bash
# На сервере
ssh -i triada-key2.pem ubuntu@54.179.1.197
sudo sqlite3 /var/lib/docker/volumes/triada_shared-data/_data/trades.db
```

---

## 8. Ключевые @TAGS@ в логах

| Тег | Когда | Пример |
|-----|-------|--------|
| `@START_SUCCESS@` | Бот стартовал | `HYDRA v17.0 STARTED` |
| `@CONFIG_OK@` | Конфиг прошёл Pydantic | `validation passed` |
| `@BG_SCAN@` | Фоновый скан нашёл кандидатов | `3 candidates. Top: APT/USDT` |
| `@SCAN_VALID@` | Кандидат прошёл heavy validation | `BILL/USDT score=1.43 mode=conservative` |
| `@SCAN_REJECT_DETAIL@` | Отказ по причине | `RVOL=0.07x < 1.2x` |
| `@SCAN_PICK@` | Dispatcher выбрал лучшего | `score=1.43 mode=conservative` |
| `@DYNAMIC_MIN_SCORE@` | Порог адаптировался | `BTC=flet dynamic=0.7` |
| `@SECOND_CHANCE@` | Повторный вход из rejected_cache | `passed retry` |
| `@GRID_ACTIVE@` | Grid запущен | `HYDRA-NET launched` |
| `@GRID_RECALC@` | Колено заполнено | `Колено 1 учтено. Новая средняя...` |
| `@CHASE_START@` | Limit-chase на выход | `limit-chase @ 0.075 window=12s` |
| `@CHASE_BACKSTOP@` | Дедлайн, выход маркетом | `-> market (deadline)` |
| `@PANIC_SELL_DONE@` | Panic sell завершён | `PnL: $-0.37` |
| `@EXIT_DONE@` | Нормальный выход | `profit=0.13` |
| `@CAPITAL@` | Текущий tier | `balance=$95 mode=power` |
| `@DISPATCHER_FEEDBACK@` | Веса обновлены | `profit=0.13 weights updated` |

---

## 9. Конфигурация (config.json)

| Секция | Ключ | Значение | Зачем |
|--------|------|----------|-------|
| `trading` | `slot_size` | 3.0 | Размер позы |
| `trading` | `dry_run` | true | Фантики |
| `trading` | `max_trades_per_day` | 2500 | Лимит |
| `trading` | `min_confidence_threshold` | 85.0 | Порог confidence |
| `trading` | `min_score_for_entry` | 1.0 | Базовый score (переопределяется dynamic) |
| `hydra_net` | `max_grid_levels` | 3 | Колен DCA |
| `hydra_net` | `take_profit_pct` | 0.8 | TP в % |
| `hydra_net` | `failing_knife_threshold` | -3.0 | Предел просадки |
| `websocket` | `enabled` | true | WS вместо REST |
| `websocket` | `reconnect_interval_sec` | 5 | Бэкофф |
| `scanner` | `enabled` | true | Сканер |
| `scanner` | `cache_ttl` | 600 | TTL hot_symbols |
| `arbitrage` | `enabled` | true | Go arb (в конфиге, но может быть выключен) |

---

## 10. Инфраструктура

| Компонент | Где | Проверить |
|-----------|-----|-----------|
| hydra-bot | Docker container | `docker compose ps` |
| Логи | docker logs | `docker logs hydra-bot --since 60m` |
| БД | Docker volume | `/var/lib/docker/volumes/triada_shared-data/_data/trades.db` |
| Конфиг | Docker volume | `/home/ubuntu/triada/shared/config.json` |
| Метрики | Prometheus :9090 | `curl localhost:9090/metrics` |
| Дашборд | Grafana :3000 | http://54.179.1.197:3000 |

---

## 11. Чеклист «через неделю»

- [ ] Сколько сэмплов `dispatcher_features`? (`tmp_dstats.py`)
- [ ] Conservative ≥ 500? → можно калибровать XGBoost
- [ ] Normal ≥ 50? → можно калибровать режимные веса (П.1)
- [ ] Win-rate стабилен? (должен быть ~90%)
- [ ] Panic sell / chase ratio? (`@CHASE_BACKSTOP@` / `@CHASE_START@`)
- [ ] Новых `@EXCHANGE_ERROR@ does not have market symbol`? → проверить делисты
- [ ] Session profit растёт? → при переходе на реал: авто-реинвест
- [ ] Feedback loop (`dispatcher_feedback`) включён? Сейчас false, включаем когда веса стабильны.

---

## 12. Ссылки

- `roadmap.md` — roadmap Hydra (спот). Фьючерс вынесен в отдельный проект `triada-futures`
- `docs/dispatcher_backlog.md` — 6 идей (П.1–П.6), статус
- `tools/calibrate_dispatcher.py` — offline batch calibration (Widrow-Hoff)
- `tools/server_stats.py` — снятие статистики эффективности с боевой БД (раздел 4.5)
- `src/core/dispatcher.py` — scoring, weights, dynamic threshold
- `src/core/states/scanning.py` — scanning + rejected_cache
- `src/database/models.py` — DB schema + migration

---

## 13. Журнал развёртывания

### 2026-06-08 — синхронизация local → server + сброс данных
- **Что было:** образ сервера собран 2026-06-05 без P.2/P.6; локальный код опережал.
  Из 41 файла кода реально отличались 4 (+1 косметический).
- **Залито на сервер** (`~/triada/`, проверено md5): `src/core/bot.py`,
  `src/core/dispatcher.py`, `src/core/scanner.py`, `src/core/states/scanning.py`,
  `src/database/models.py`. Весь `src/` + `shared/` + `main.py` + `config.json`
  теперь побайтно равны локальным.
- **Образ:** пересобран `triada-hydra-bot:latest`, контейнер `hydra-bot` пересоздан.
  Хэши кода внутри контейнера совпадают с локальными → P.2 + P.6 активны.
- **Данные:** `dispatcher_features` обнулён (2133 → 0), `VACUUM`. Таблица `trades`
  (ledger PnL) сохранена.
- **Бэкап БД:** `/var/lib/docker/volumes/triada_shared-data/_data/trades.db.bak.20260608_160203`.
- **Состояние:** `hydra-bot` Up (healthy), `dry_run=true`, новая выборка пошла с нуля.

### Как деплоить дальше (актуальная процедура)
```bash
KEY=~/Desktop/triada-key2.pem   # на Windows: C:\Users\leogo\Desktop\triada-key2.pem
# 1. Залить изменённые файлы (пример)
scp -i $KEY src/core/bot.py ubuntu@54.179.1.197:~/triada/src/core/bot.py
# 2. Пересобрать и перезапустить ТОЛЬКО hydra-bot (НИКОГДА не используй down -v!)
ssh -i $KEY ubuntu@54.179.1.197 "cd ~/triada && sudo docker compose build hydra-bot && sudo docker compose up -d hydra-bot"
# 3. Проверить
ssh -i $KEY ubuntu@54.179.1.197 "sudo docker ps --filter name=hydra-bot; sudo docker logs --tail 30 hydra-bot"
```
- **Реальный volume с БД:** `triada_shared-data` (на сервере есть пустой мусорный
  `shared-data` — не путать).
- **dispatcher_feedback** всё ещё `false` — онлайн-обновление весов не идёт, данные
  только копятся для будущей batch-калибровки.

---

*Карта создана для быстрого ориентирования. Если что-то изменилось — обнови этот файл.*
