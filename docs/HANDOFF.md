# HANDOFF — Hydra Spot Bot (real-money pilot)

Дата последней правки: 2026-09-05
Последняя реальная работа на сервере: 2026-06-17
Автор передачи: сессия Cascade (Claude → SWE), пилот на реальных деньгах

---

## 1. Пути и доступы

| Что | Где |
|---|---|
| Локальный код (git) | `C:\Users\leogo\Desktop\bot4-main\bot4-main` |
| Git remote | GitHub `leogonnn-web/bot6` |
| Сервер | `ubuntu@<SERVER_IP>` (AWS EC2; актуальный IP — в личных заметках, не в git) |
| SSH-ключ | `<PATH_TO_SSH_KEY>.pem` (хранить вне репозитория и вне Desktop) |
| Каталог на сервере | `~/triada` — **это НЕ git-репозиторий** |
| Ключи API | `~/triada/.env` (`BYBIT_API_KEY`, `BYBIT_API_SECRET`) — в git не коммитить |
| Конфиг бота | `~/triada/shared/config.json` (bind-mount, `:ro` внутрь контейнера) |
| Futures-проект | `C:\Users\leogo\Desktop\triada-futures` — **отдельный проект, к Hydra не относится** |

### Контейнеры и порты

| Контейнер | Сервис в compose | Порт |
|---|---|---|
| `hydra-bot` | `hydra-bot` | `9090` (метрики Prometheus) |
| `triada-prometheus` | `prometheus` | `9092` → UI |
| `triada-grafana` | `grafana` | `3000` |
| `triada-watchdog` | `watchdog` | — |

**2026-09-09:** `hydra-arb` (arb-engine) и `go-scalper` вынесены в отдельные репозитории
`triada-arb` и `triada-scalper`. На сервере контейнер `hydra-arb` мог остаться от старого compose —
при следующем деплое выполнить `sudo docker compose up -d --remove-orphans`.

**Важно:** в `docker compose stop/up` используется **имя сервиса**, а не имя контейнера.
`docker compose stop triada-watchdog` → ошибка. Правильно: `docker compose stop watchdog`.

### Named volumes

- `triada_shared-data` — `capital_state.json`, SQLite-БД, файл-флаг паузы watchdog. **Выживает при пересборке.**
- `triada_prometheus-data`, `triada_grafana-data`

---

## 2. Команды запуска и эксплуатации

Деплой на сервер: `scp` файлов + пересборка. Git на сервере не используется.

```powershell
# Залить изменённые файлы (пример)
scp -i "<PATH_TO_SSH_KEY>.pem" `
  "C:\Users\leogo\Desktop\bot4-main\bot4-main\src\core\states\buying.py" `
  ubuntu@<SERVER_IP>:/home/ubuntu/triada/src/core/states/
```

```bash
# На сервере, всегда из ~/triada
sudo docker compose up -d --build hydra-bot     # пересобрать + поднять бота
sudo docker compose up -d --build watchdog      # пересобрать + поднять watchdog
sudo docker compose stop hydra-bot              # остановить бота
sudo docker compose stop watchdog hydra-bot     # остановить всё торговое
sudo docker ps -a --format "{{.Names}} | {{.Status}}"
sudo docker logs --tail 50 hydra-bot
sudo docker logs --tail 50 triada-watchdog
```

### Правка config.json на сервере (ловушка с inode)

`shared/config.json` смонтирован bind-mount'ом. Docker пиннит **inode**. Любая правка,
которая пересоздаёт файл (`os.replace`, `sed -i`, редактор с atomic save), меняет inode —
и работающий контейнер **продолжит видеть старый конфиг**. Единственный надёжный способ:

```bash
sudo docker compose up -d --force-recreate hydra-bot
```

### Пауза watchdog (не даёт поднимать бота, сам watchdog остаётся жив)

Флаг — файл `/data/watchdog.pause` на volume `triada_shared-data`.

```bash
# Поставить паузу
sudo docker run --rm -v triada_shared-data:/data alpine touch /data/watchdog.pause
# Снять паузу
sudo docker run --rm -v triada_shared-data:/data alpine rm -f /data/watchdog.pause
# Проверить
sudo docker run --rm -v triada_shared-data:/data alpine ls -la /data
```
Пока файл есть, в логах watchdog идёт `@WATCHDOG_PAUSED@` и рестарты не выдаются.

### Тесты

```powershell
python -m pytest tests/test_watchdog.py -q    # 14 passed (проверено 2026-09-05)
```

---

## 3. Что настроено

### Состояние кода: правки НЕ закоммичены

`git log` HEAD = `aa6853b`. Все фиксы пилота лежат в рабочем дереве **незакоммиченными**:

```
 M docker-compose.yml
 M src/core/states/buying.py
 M src/core/states/in_position.py
 M src/monitoring/watchdog.py
 M tests/test_watchdog.py
?? tools/session_trades.py
```

На сервер эти файлы **залиты и собраны**, в git — нет. Расхождение git ↔ сервер.

### Watchdog (`src/monitoring/watchdog.py`) — «Вариант 3»

- `ContainerController` / `DockerContainerController` получили метод `status(name) -> str`
  (`running` / `exited` / `unknown` при ошибке).
- В `Watchdog.tick()` перед рестартом проверяется статус целевого контейнера:
  если он **не** `running` → рестарт не выдаётся, лог `@WATCHDOG_SKIP@`.
- `target_down` (пропажа метрик из Prometheus) больше **не является триггером** рестарта —
  только трекается для диагностики.
- Pause-флаг: в начале `tick()` проверяется `PAUSE_FILE_PATH` (по умолчанию `/data/watchdog.pause`).
- `docker-compose.yml`: сервису `watchdog` добавлены `shared-data:/data` и `PAUSE_FILE_PATH`.

### Логика торговли

`src/core/states/buying.py`:
- `_on_buy_filled` считает объём продажи от **реального баланса монеты** (Bybit UTA:
  `info.result.list[0].coin[].equity`), fallback — `amount * (1 - 0.0015)`, затем `amount_to_precision`.
- При провале выставления TP-ордера состояние **не** сбрасывается в `IDLE`:
  бот остаётся в `IN_POSITION` с `order_id=None`.
- `_enter_trade`: **Single Position Invariant** — если на балансе есть любая монета кроме USDT
  выше dust (`0.0001`), новая покупка блокируется (`@SINGLE_POSITION_BLOCK@`).

`src/core/states/in_position.py`:
- `_panic_sell` продаёт **реальный баланс**, а не сохранённый `amount`.
- В `except` у `_panic_sell` сброса в `IDLE` больше нет — позиция остаётся под управлением.

### Риск-логика (закоммичено ранее, `aa6853b`)

- `src/core/risk/limits.py`: fail-closed при ошибках в реальном режиме; realized PnL и дневной
  счётчик сделок скоупятся по `session_start_ts`, чтобы dry-run история не маскировала реальные убытки.
- `src/database/models.py`: `get_session_stats(since_ts)`, `get_daily_trades_count(since_ts)`.

### Конфиг: локальный ≠ серверный

Локальный `shared/config.json` — это **dry-run baseline**:
`dry_run: true`, `slot_size: 3.0`, `base_order_size_usdt: 3.0`, `session_start_ts: 0`,
`max_trades_per_day: 2000`, `panic_stop: 1.2`, `stop_loss_total: 12.0`.

На сервере во время пилота было: `dry_run: false`, `slot_size: 6`,
`session_start_ts: 1781598549`, `max_trades_per_day: 17` (снижен с 30 для купирования покупок).

**Перед любым запуском сверить оба конфига явно.** Локальный конфиг не является источником истины для сервера.

### Capital Router

`shared/capital_router.py` сам ограничивает режим по балансу:
`$15–25` → `single_shot`, grid выключен; `$25` → grid1; `$50` → grid2; `$100` → grid3.
Ниже порога — `frozen`. На последнем запуске при балансе `$4.75` было `mode=frozen slot=$0.00`.

---

## 4. Что сломано / открыто

### Блокеры

1. **Сервер недоступен.** На 2026-09-05 `ssh <SERVER_IP>:22` → connection timed out.
   EC2 либо остановлен, либо сменился публичный IP. Состояние контейнеров, конфига и
   баланса **не подтверждено**. Первый шаг для следующего исполнителя — поднять/найти инстанс
   и снять фактическое состояние, ничего не запуская.
2. **Фиксы не в git.** См. список изменённых файлов выше. Нужен коммит, иначе правки
   существуют только в рабочем дереве и в образах на сервере.
3. **ЗАДАЧА C не выполнена** — валидация (dry-run прогон) перед возвратом на реальные деньги.
   Пользователь явно вывел её из скоупа предыдущей сессии. Реальную торговлю **не включать**
   без отдельной команды пользователя.

### Технический долг

- `tests/test_watchdog.py::test_target_down_triggers_restart` — имя врёт: тест теперь
  утверждает, что рестарта **не** происходит. Переименовать / удалить как дубль
  `test_exited_container_no_restart`.
- `hydra-bot` в `docker-compose.yml` остаётся с `restart: unless-stopped`. Решение сознательное
  (см. §5), но означает, что автоподъём после ребута хоста возможен помимо watchdog.
- Проверка баланса в `_enter_trade` и в `_on_buy_filled` дублирует парсинг UTA-структуры
  (`info.result.list[0].coin[]`) в трёх местах. Просится один хелпер.
- Единый dust-порог `0.0001` захардкожен и не учитывает разницу decimals между монетами.
- API-ключи не залочены по IP; статический IP не назначен. Пользователь просил удалить
  plaintext-файл с ключами на Desktop — статус не подтверждён.
- `dispatcher.feedback_loop` остаётся `false`: калибровка на dry-run данных бессмысленна.

### Наблюдения по последнему реальному прогону

- Баланс просел с ~$22.27 до ~$4.61–4.75 USDT из-за серии непроданных покупок.
- Сессия по логам: 4 сделки из 17, realized PnL −$0.01. Часть монет пользователь продавал вручную.
- Grafana показывала `IDLE` для остановленного бота. Рабочая гипотеза (инструментально
  не подтверждена): панель отдаёт последнее заскрейпленное значение метрики, а не живое
  состояние. В любом случае — не считать эту панель индикатором «бот жив»; сверять с `docker ps`.

---

## 5. Принятые решения и почему

### Watchdog: «Вариант 3» вместо запрета/полномочий

Рассматривались: (1) команда «не поднимать бота», (2) отдать watchdog право включать/выключать бота,
(3) проверка статуса контейнера + флаг паузы. Выбран (3).

**Почему.** Корень проблемы: при `docker stop hydra-bot` метрики исчезали из Prometheus,
ветка `target_down` трактовала это как заморозку и поднимала намеренно остановленный бот.
Отличить «намеренно остановлен» от «завис» по метрикам невозможно — но можно по статусу Docker.
Watchdog теперь лечит только то, для чего он нужен: контейнер `running`, но не отвечает.
Флаг-файл добавлен как ручной оверрайд без остановки самого watchdog.

Ветка `target_down` убрана из триггеров, потому что она напрямую конфликтует с проверкой статуса.
Настоящая заморозка ловится через `heartbeat_stalled` — heartbeat обновляется каждой итерацией
основного цикла в любом состоянии, включая `IN_POSITION`.

### Продажа по реальному балансу

Bybit spot берёт комиссию 0.1% **в базовой валюте**. Купив `N` монет, на балансе оказывается
чуть меньше `N`. Попытка продать ровно `N` → `retCode 170131 "Insufficient balance"`.
Отсюда каскад: TP-ордер не встаёт → `order_id=None` → `panic_sell` тоже падает → сброс в `IDLE`
→ бот покупает следующую монету. Поэтому объём продажи считается от факта, а не от записанного `amount`.

### Никакого сброса в IDLE при ошибке продажи

`IDLE` означает «позиции нет». Если продажа не удалась, монета на балансе **есть**.
Сброс в `IDLE` был первопричиной «покупает несколько монет и не продаёт». Теперь бот остаётся
в `IN_POSITION` и ретраит; выход в `IDLE` — только когда баланс монеты реально ниже dust.

### Single Position Invariant

Последний рубеж от каскада покупок: даже если состояние машины разъехалось с реальностью,
проверка баланса перед покупкой не даст открыть вторую позицию. Ставка на факт с биржи,
а не на внутреннее состояние бота.

### `restart: unless-stopped` оставлен

`docker compose stop` переводит контейнер в состояние `stopped`, которое эта политика уважает.
Проблема была не в политике, а в watchdog. Политика полезна при перезагрузке хоста, поэтому её не трогали.

### `max_trades_per_day: 17`

Оперативное купирование во время инцидента: значение ниже уже совершённого числа сделок,
чтобы риск-лимит отсёк новые входы, но бот остался жив для управления открытой позицией.
Это временная мера, не постоянная настройка.

---

## 6. Рекомендуемый порядок для следующего исполнителя

1. Поднять/найти EC2, снять фактическое состояние: `docker ps -a`, логи, `shared/config.json`, баланс.
   Ничего не запускать в реале.
2. Закоммитить фиксы пилота в git с внятным сообщением (без секретов).
3. Почистить долг по тестам watchdog (переименовать/удалить врущий тест).
4. Проверить, что на сервере `dry_run: true`, и прогнать ЗАДАЧУ C — dry-run валидацию:
   единственная позиция, продажа по реальному балансу, отсутствие сброса в `IDLE` на ошибках,
   управляемость watchdog (stop бота → рестарта нет; заморозка → рестарт есть; pause-файл → рестарта нет).
5. Возврат на реальные деньги — **только по явной команде пользователя**, с пересчётом
   `session_start_ts` и sizing под фактический баланс.
