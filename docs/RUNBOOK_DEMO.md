# RUNBOOK — от текущего кода до возврата на реальные деньги

Состояние: `main` = `d797c65`, все критические пункты аудита (C1–C6) закрыты в коде, 234 теста.
Код **ни разу не исполнялся против биржи** после правок. Этот runbook — порядок проверки.
Каждый этап имеет критерий «идём дальше» и «стоп». Не перепрыгивать.

Плейсхолдеры: `<SERVER_IP>`, `<KEY>.pem` — из личных заметок, не из git.

---

## Этап 0 — Инфраструктура (один раз, руками, ~1 час)

### 0.1 Сервер
```bash
ssh -i <KEY>.pem ubuntu@<SERVER_IP>
cd ~/triada
sudo docker ps -a --format "{{.Names}} | {{.Status}}"
sudo docker compose stop            # остановить всё, что крутится на старом коде
```
Если `ssh` не отвечает — инстанс остановлен или сменился IP: AWS Console → EC2 → Instances → проверить
состояние и Public IPv4. Поднять, записать новый IP.

### 0.2 Security Group — закрыть порты (аудит H1)
Локально, в `terraform/`:
```powershell
# terraform.tfvars больше не в git — создать из примера, вписать свой текущий IP (whatismyip)
Copy-Item terraform.tfvars.example terraform.tfvars   # если файла нет
# your_ip_cidr = "<твой IP>/32"
.\terraform.exe init
.\terraform.exe plan      # должно показать изменение ТОЛЬКО security group (ingress 3000/9090/9092 → your_ip_cidr, 9091 удалён)
.\terraform.exe apply
```
**Стоп**, если `plan` хочет пересоздать инстанс (`must be replaced`) — не применять, разбираться.
Проверка: `curl -m 5 http://<SERVER_IP>:9090/metrics` с чужой сети → таймаут; со своего IP → метрики.

### 0.3 Ключи Bybit Demo
Bybit → Demo Trading (переключатель в шапке) → API → создать ключ с правами **только Spot Trade** (без Withdraw).
Пополнить демо-баланс (кнопка в демо-кабинете) до реалистичной суммы — **$50–100**, не $100 000:
CapitalRouter режимы завязаны на баланс (`$25` → grid1, `$50` → grid2, `$100` → grid3), на миллионе
демо-баланса тест ничего не скажет о поведении при твоих деньгах.

### 0.4 Доставить код на сервер
Сервер — не git-репо (HANDOFF §1). Проще сделать его репо:
```bash
# на сервере
cd ~ && mv triada triada_old_$(date +%F)
git clone https://github.com/leogonnn-web/bot6.git triada
cd triada
cp ../triada_old_*/.env .                 # ключи, не в git
cp ../triada_old_*/shared/config.json shared/config.server.json   # старый серверный конфиг — для сверки
```
Дальше любой деплой = `git pull && sudo docker compose up -d --build --remove-orphans`.

### 0.5 `.env` на сервере
```
BYBIT_API_KEY=            # боевой — оставить пустым до этапа 5
BYBIT_API_SECRET=
BYBIT_DEMO_API_KEY=<из 0.3>
BYBIT_DEMO_API_SECRET=<из 0.3>
GRAFANA_PASSWORD=<новый, не triada2024>
# HYDRA_LIVE_CONFIRM — НЕ задавать до этапа 5
```

### 0.6 `shared/config.json` на сервере
Взять репозиторный `shared/config.json` как основу (он валиден по новой строгой схеме) и перенести из
`config.server.json` **только** значения `trading.*`, которые ты осознанно менял (slot_size, panic_stop…).
Не копировать старый файл целиком — он не пройдёт валидацию (секция `arbitrage`, неизвестные ключи).

Для этапа 1 (dry-run):
```json
"trading": { "dry_run": true,  ... "session_start_ts": <текущий unix epoch> },
"exchange": { "name": "bybit", "demo_trading": false }
```
Проверить **до** запуска:
```bash
python3 -c "import json,sys; sys.path.insert(0,'shared'); from config_models import validate_config; validate_config(json.load(open('shared/config.json'))); print('CONFIG OK')"
```

### 0.7 Том состояния под non-root
```bash
sudo docker run --rm -v triada_shared-data:/d alpine chown -R 10001:10001 /d
sudo docker run --rm -v triada_shared-data:/d alpine ls -la /d
# если там лежит hydra_state.json от старого пилота — удалить: бот не должен "восстановить" позицию 4-месячной давности
sudo docker run --rm -v triada_shared-data:/d alpine rm -f /d/hydra_state.json /d/watchdog.pause
```
`trades.db` на томе — оставить (история), новая сессия отсекается через `session_start_ts`.

### 0.8 Сборка и запуск
```bash
sudo docker compose build
sudo docker compose up -d --remove-orphans
sudo docker ps --format "{{.Names}} | {{.Status}}"      # hydra-bot, prometheus, grafana, watchdog; hydra-arb НЕТ
sudo docker logs --tail 50 hydra-bot
```
Ожидаемые строки старта: `@CONFIG_OK@`, `@EXCHANGE_INIT@ ... mode=LIVE` (dry-run использует боевой REST только на чтение;
ключи пустые → `@EXCHANGE_WARN@ No API keys, virtual mode` — это нормально для dry-run), `@WS_CONN@`, `@METRICS@`, `@START_SUCCESS@`.
**Стоп**, если `@CONFIG_VALIDATION_ERROR@`, `@LIVE_GUARD@`, `Permission denied` на `/app/shared/state` или `/app/logs`.

Grafana: `http://<SERVER_IP>:3000`, если том старый — `sudo docker exec triada-grafana grafana-cli admin reset-admin-password <пароль>`.

---

## Этап 1 — Dry-run, 12–24 ч (стабильность после TZ-04)

Цель: убедиться, что надзор не стреляет по здоровому боту и цикл не блокируется.

Через 1 час и через 12–24 часа:
```bash
cd ~/triada
L=$(sudo docker inspect --format='{{.LogPath}}' hydra-bot)
for m in @HEALTH_CRITICAL@ @WATCHDOG_RESTART@ @LOOP_ERROR@ Traceback @WS_FATAL@ @WS_RECONNECT@ @PRICE_STALE@ @BG_SCAN@ @SCAN_PICK@ @GRID_LAUNCH@ @IDLE_ENTER@; do
  echo "$m: $(sudo grep -c "$m" $L)"; done
sudo docker logs --tail 20 triada-watchdog
```
| Маркер | Идём дальше | Стоп |
|---|---|---|
| `@WATCHDOG_RESTART@`, `@HEALTH_CRITICAL@` | 0 | ≥1 — смотреть, что было за 60 с до него |
| `@LOOP_ERROR@`, `Traceback`, `@WS_FATAL@` | 0 | ≥1 |
| `@WS_RECONNECT@` | единицы в сутки | десятки — проблема сети/подписок |
| `@PRICE_STALE@` | редко, неликвидные пары | постоянно на ликвидных |
| `@SCAN_PICK@` / `@GRID_LAUNCH@` | есть входы (сколько в сутки — записать, это калибровка частоты) | 0 за сутки — сканер ничего не находит, проверить пороги |

Также: Grafana `hydra_heartbeat_timestamp` — ровная пила без провалов > 10 с; `hydra_health_status` = 1.

---

## Этап 2 — Демо, одиночные входы (`max_grid_levels: 1`), 2–3 дня

Первый прогон live-кода против настоящих ордеров. Сетка минимальная, чтобы отделить проблемы входа/выхода от проблем сетки.

Конфиг:
```json
"trading":   { "dry_run": false, "session_start_ts": <новый epoch>, "slot_size": <как планируешь в реале, напр. 6> },
"exchange":  { "demo_trading": true },
"hydra_net": { "max_grid_levels": 1 }
```
`HYDRA_LIVE_CONFIRM` **не задан**. Запуск: `sudo docker compose up -d --force-recreate hydra-bot` (bind-mount конфига пиннит inode — HANDOFF §2).
В логе обязательно `@EXCHANGE_INIT@ ... mode=DEMO`. Если `mode=LIVE` — **немедленно `docker compose stop hydra-bot`** и проверить `.env`/конфиг.

Что должно появиться за прогон (хотя бы по несколько раз):
```
@GRID_LAUNCH@ → @GRID_RECALC@ (level 0 filled) → @GRID_TP_SET@ → @GRID_END@ (grid_complete) → IN_POSITION
→ @PROFIT_TAKEN@ / @LOSS_TAKEN@ / @STOP_LOSS_HIT@ → @CHASE_START@ → @CHASE_FILL@ или @CHASE_BACKSTOP@ → @IDLE_ENTER@
```
Проверки (каждые сутки):
```bash
for m in @GRID_LAUNCH@ @GRID_RECALC@ @GRID_TP_SET@ @GRID_END@ @PROFIT_TAKEN@ @LOSS_TAKEN@ @IDLE_ENTER@ @IDLE_REFUSED@ @IDLE_DEFERRED@ @EXIT_HALTED@ @EXIT_RETRY@ @BUY_STATUS_UNKNOWN@ @TP_STATUS_UNKNOWN@ @SINGLE_POSITION_BLOCK@ @QUANTIZE_REJECT@ "Insufficient balance" 170131; do
  echo "$m: $(sudo grep -c "$m" $L)"; done
```
| Маркер | Идём дальше | Стоп |
|---|---|---|
| `@EXIT_HALTED@` | 0 | ≥1 — позиция, которую бот не смог продать за 5 попыток; разобрать причину |
| `@IDLE_REFUSED@` | 0–1 с объяснимой причиной | повторяется — бот теряет монеты из учёта |
| `Insufficient balance`/`170131` | 0 | ≥1 — размер TP не совпадает с балансом |
| `@QUANTIZE_REJECT@` | 0 на входе | есть — slot_size ниже минимального лота пары |
| `@SINGLE_POSITION_BLOCK@` | 0 после закрытия позиции | есть при пустой позиции — что-то осталось на демо-балансе |
| `@*_UNKNOWN@` | единичные, совпадают с сетевыми ошибками | постоянный фон |

**Сверка двух источников (главный тест Фазы 1):**
```bash
sudo docker run --rm -v triada_shared-data:/d -w /d python:3.11-slim python - <<'EOF'
import sqlite3; c=sqlite3.connect('trades.db')
print(c.execute("select side,count(*),round(sum(profit),4) from trades where timestamp>=<session_start_ts> group by side").fetchall())
EOF
```
Сумма `profit` по `sell*` должна совпадать с `hydra_session_profit_usdt` в Grafana и с изменением демо-баланса USDT
в кабинете Bybit (± комиссии, которые демо может считать иначе). Расхождение > 5% — **стоп**, это H7 в новом виде.

Также руками в кабинете Bybit Demo → Orders: после каждого `@IDLE_ENTER@` открытых ордеров по символу **нет**, баланс монеты 0.

---

## Этап 3 — Демо, сетка (`max_grid_levels: 3`), 2–3 дня

Первый в истории проекта прогон сетки против настоящих ордеров.
Конфиг: `"hydra_net": { "max_grid_levels": 3 }`, остальное как в этапе 2. Демо-баланс ≥ $100, иначе CapitalRouter сам срежет уровни.

Что проверять — **в кабинете Bybit Demo → Active Orders**, в момент после `@GRID_RECALC@ level 0`:
- открыт **ровно один BUY** (уровень 1, ниже входа) и **ровно один SELL** (TP выше средней) — это C2 вживую;
- после `@GRID_RECALC@ level 1`: старый SELL отменён, новый SELL на бо́льшее количество, BUY уровня 2 открыт;
- `@GRID_REPRICE@` (если есть) меняет цену только BUY-ордера;
- `@PROFIT_TAKEN@ ... grid TP` → в Active Orders пусто, баланс монеты 0;
- `@GRID_END@ (falling_knife|grid_max_hold|level_rejected)` → BUY отменён, SELL остался, состояние IN_POSITION.
Маркеры стопа те же, что в этапе 2, плюс `@GRID_TP_IMPOSSIBLE@` (TP ниже лимитов биржи) и `@GRID_ERROR@`.

---

## Этап 4 — Обрывы сети (1 сессия, ~1 час)

Проверяет обещание Фазы 1: «не знаю» ≠ действие. Делать на демо, пока бот в BUYING с открытой сеткой и затем в IN_POSITION.
```bash
# на сервере: заблокировать демо-API на 45 с, потом снять
sudo iptables -I OUTPUT -d api-demo.bybit.com -j DROP; sleep 45; sudo iptables -D OUTPUT -d api-demo.bybit.com -j DROP
```
(если `iptables` ругается на имя — взять IP через `getent hosts api-demo.bybit.com`.)
Ожидание в логе во время блокировки: `@ORDER_STATUS_UNKNOWN@`, `@GRID_STATUS_UNKNOWN@`/`@TP_STATUS_UNKNOWN@`, `@BALANCE_UNKNOWN@`,
возможно `@PRICE_STALE@` (WS идёт отдельно и может жить). Состояние **не меняется**, новых ордеров **нет**, `@PANIC_SELL_SEND@` **нет**.
После снятия блокировки — обычный ход. **Стоп**, если за время блокировки появились `@IDLE_ENTER@`, `@PANIC_*`, `@NO_TP_ORDER@`.
Повторить второй раз, когда бот в IN_POSITION.

---

## Этап 5 — Реальные деньги (только после 1–4, только твоё решение)

Перед включением:
1. Боевой ключ: Spot Trade only, **IP whitelist = Elastic IP сервера** (сначала назначить Elastic IP в AWS, иначе после рестарта инстанса ключ перестанет работать).
2. Баланс на споте — только та сумма, которую ты готов считать тестовой. CapitalRouter: < $15 — не торгует вовсе.
3. Конфиг: `"dry_run": false, "demo_trading": false`, `slot_size` минимальный разумный (≥ min-notional пар, обычно 5–6 USDT), `max_grid_levels: 1` первые дни,
   `session_start_ts` новый, `max_trades_per_day` — **выставить заново**: после TZ-05 он считает закрытые сделки, а не ноги (старое `17` означало другое).
4. `.env`: `BYBIT_API_KEY/SECRET` боевые, `HYDRA_LIVE_CONFIRM=yes`.
5. `sudo docker run --rm -v triada_shared-data:/d alpine rm -f /d/hydra_state.json` — чистое состояние.
6. `sudo docker compose up -d --force-recreate hydra-bot` → в логе `mode=LIVE`, `@GO LIVE`/`@START_SUCCESS@`.
7. Первые сутки — те же проверки, что в этапе 2, плюс **сверка с реальным балансом в кабинете** после каждой сделки.

Аварийная остановка в любой момент:
```bash
sudo docker run --rm -v triada_shared-data:/data alpine touch /data/watchdog.pause   # watchdog не поднимет
sudo docker compose stop hydra-bot
# позиция, если есть, — продать руками в кабинете; затем rm hydra_state.json перед следующим стартом
```

---

## Что отдавать ИИ по ходу

- **TZ-10** (разбор логов этапов 1–3) — средняя модель, по готовому списку маркеров. Вход: `docker logs hydra-bot > logs_stageN.txt`.
- **TZ-12B** (разметка кандидатов) — когда в `dispatcher_features` ≥ 3 дней строк с `bid_ask_source='book'`.
- Любая найденная проблема → строка в `docs/audit/BACKLOG.md`, затем решение: кому и в каком режиме чинить.
- Правки торговой логики по результатам этапов 2–4 — сильная модель, high; остальное — по матрице из `TZ_DELEGATED.md`.

## Чего не делать

- Не включать `dispatcher.feedback_loop`.
- Не менять `config.json` на сервере через `sed -i`/редактор с atomic save без `--force-recreate` (inode-ловушка).
- Не копировать старый серверный `config.json` целиком — не пройдёт схему.
- Не запускать скальпер/арбитраж на тех же ключах и балансе.
- Не переходить к следующему этапу при любом «стоп»-маркере без разбора причины.
