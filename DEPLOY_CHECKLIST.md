# Deploy Checklist — Hydra Bot

## Before EVERY deploy to production:

- [ ] **dry_run = true** in `shared/config.json` (unless explicitly switching to live)
- [ ] **Balance check**: minimum $3 USDT free on Bybit
- [ ] **State persistence test**: run `python -m pytest tests/ -x` → all green
- [ ] **Git commit**: `git diff` reviewed, no accidental changes to config

## Critical config fields to verify:

```bash
# Run this locally before deploy:
python -c "import json; c=json.load(open('shared/config.json')); print('dry_run:', c['trading']['dry_run']); print('min_exchange_limit:', c['trading']['min_exchange_limit']); print('max_trades_per_day:', c['trading']['max_trades_per_day'])"
```

## Deploy commands:

```bash
# 1. Copy config
scp shared/config.json ubuntu@54.179.1.197:~/triada/shared/config.json

# 2. Restart bot
ssh ubuntu@54.179.1.197 "cd ~/triada && sudo docker compose stop hydra-bot && sudo docker compose rm -f hydra-bot && sudo docker compose up -d hydra-bot"

# 3. Verify dry_run in logs (first 30 seconds)
ssh ubuntu@54.179.1.197 "sudo docker logs --tail 20 hydra-bot 2>&1 | grep -i dry"
```

## Post-deploy verification (within 2 minutes):

- [ ] `docker logs hydra-bot` shows `@DRY_RUN@` or `@REST_POLL@ ... dry_run`
- [ ] `hydra_health_status` = 1.0 in Prometheus
- [ ] No `@BALANCE_LOW` spam (if balance OK)
- [ ] Watchdog not restarting bot every 15 seconds

## If switching to LIVE mode (explicit approval only):

- [ ] **User confirmed** in writing
- [ ] **Balance >= $50** (not $3)
- [ ] **Take screenshot** of current balance on Bybit
- [ ] **Set max_trades_per_day = 1** for first test
- [ ] **Set hard_exit_timeout** active
- [ ] **Monitor first trade manually** via logs + Bybit app

## Emergency rollback:

```bash
# Stop bot immediately
ssh ubuntu@54.179.1.197 "sudo docker compose stop hydra-bot"

# Check last real orders on Bybit
ssh ubuntu@54.179.1.197 "sudo docker exec hydra-bot python3 -c 'import ccxt; ...'"
```
