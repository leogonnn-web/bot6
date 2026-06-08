# Hydra Dispatcher — Context-AI Risk Manager

## Цель
Динамический диспетчер для Hydra grid-bot: выбирает 1 лучший символ из 11 и настраивает агрессивность сетки под текущую микроструктуру рынка.

---

## 1. Архитектура (Score-Based Selection)

### Формула Score (нормализованная)

```
score = w1·c_norm + w2·r_norm + w3·d_norm + w4·o_norm + w5·btc_ok

где:
  c_norm = confidence / 100                          (0..1)
  r_norm = min(rvol_spike, 5.0) / 5.0               (0..1), отсечка 5x
  d_norm = min(dump_depth, 3.0) / 3.0               (0..1), отсечка 3%
  o_norm = clamp((obi_skew + 1.0) / 2.0, 0, 1)      (0..1), bid-ask imbalance
  btc_ok = 0  if BTC_1h < -2%
         = 0.5 if BTC_1h < -1%
         = 1.0 otherwise
```

### Параметры

| Параметр | Описание | Источник |
|----------|----------|----------|
| confidence | % анализатора | indicators.matrix |
| rvol_spike | RVOL_current / RVOL_24h | OHLCV |
| rvol_local | RVOL_30min / RVOL_4h | OHLCV |
| dump_depth | % пролива от локального high | OHLCV |
| obi_skew | (bid_vol - ask_vol) / total | OrderBook |
| BTC_1h | % изменения BTC за 1ч | WS тикер |

---

## 2. Режимы работы (Finite State Machine)

```python
if btc_1h < -2.0:
    mode = "red_light"      # стоп, не входим
elif rvol_local > 2.5 and dump > 0.8 and conf > 50:
    mode = "aggressive"     # жаришка
elif rvol_local > 1.5:
    mode = "normal"         # стандарт
else:
    mode = "conservative"   # штиль
```

### Параметры режимов

| Режим | grid_dist | TP % | levels | min_conf | slot_mult |
|-------|-----------|------|--------|----------|-----------|
| red_light | — | — | 0 | — | 0 |
| aggressive | 0.25-0.30% | 1.5% | 3 | 10% | 1.2x |
| normal | 0.45-0.50% | 0.8% | 2 | 15% | 1.0x |
| conservative | 0.60-0.80% | 0.5% | 2 | 25% | 0.8x |

---

## 3. Feedback Loop (Widrow-Hoff Delta)

После каждой сделки корректируем веса:

```
# TP → ошибка = +1 (хотели максимум)
# SL → ошибка = -1 (хотели избежать)

error = +1 if profit > 0 else -1
delta = learning_rate * error * feature_value

w_i = w_i + delta  # ограничить [0.1, 5.0]
```

### Защита от "ножа"

- Если SL по безоткатному падению: `error = -2` (двойной штраф)
- Если TP на первом колене: `error = +1.5` (бонус за точный вход)
- Веса ограничены: min=0.1, max=5.0
- "Забывание" — экспоненциальное сглаживание старых коррекций

---

## 4. Требования к БД (фичи для обучения)

При каждом входе сохранять:

```sql
CREATE TABLE dispatcher_features (
    id INTEGER PRIMARY KEY,
    trade_id INTEGER REFERENCES trades(id),
    timestamp REAL,
    symbol TEXT,
    -- Факторы при входе
    confidence REAL,
    rvol_spike REAL,
    rvol_local REAL,
    dump_depth REAL,
    obi_skew REAL,
    btc_1h REAL,
    btc_4h REAL,
    -- Расчётные
    score REAL,
    mode TEXT,
    -- Результат
    outcome TEXT,  -- 'tp', 'sl', 'breakeven', 'partial_tp'
    profit_pct REAL,
    hold_time_min REAL,
    max_dd_pct REAL,  -- максимальная просадка
    -- Веса диспетчера на момент входа
    w_confidence REAL,
    w_rvol REAL,
    w_dump REAL,
    w_obi REAL
);
```

---

## 5. План реализации

### Этап 1: Статический диспетчер (1 день)
- Считает score для 11 монет
- Выбирает max(score)
- Назначает mode → параметры сетки
- **Метрики:** сравнить WinRate "со диспетчером" vs "без"

### Этап 2: Адаптивные веса (2-3 дня)
- Feedback loop после каждой сделки
- Авто-коррекция w1-w5
- **Метрики:** стабильность WinRate при смене рыночной фазы

### Этап 3: ML-буст (опционально, 1 неделя)
- Logistic regression на фичах из БД
- Предсказание: TP/SL вероятность
- Fallback на score, если ML не уверен

---

## 6. Граничные случаи

| Ситуация | Действие |
|----------|----------|
| 2 монеты с одинаковым score | Берём ту, что дольше не торговалась |
| Все score < 0.3 | red_light, ждём |
| BTC crash (-5%) | red_light на 30 минут |
| Open position | Диспетчер спит, не переключает |
| API timeout при чтении OB | Fallback на OHLCV-only score |

---

## 7. Ключевые принципы

1. **No Black Box:** Все веса и score логируются. Трейдер видит "NOT score=0.87 (rvol=0.9, dump=0.6, conf=0.7)"
2. **Fail-Safe:** Если диспетчер сломался — Hydra работает в conservative mode по умолчанию
3. **Zero Latency:** Расчёт < 0.1ms на Go, все данные в памяти
4. **No Parallel Grids:** Один символ, одна сетка. Ротация только после закрытия

---

*Статус: Концепт зафиксирован. Реализация после 24ч+ сбора базовой статистики.*
