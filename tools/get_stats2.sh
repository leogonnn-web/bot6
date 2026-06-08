#!/bin/bash
cd ~/triada
LOGS=$(sudo docker compose logs hydra-bot 2>&1)
echo "CLOSE_PROFIT:$(echo "$LOGS" | grep -c 'CLOSE_PROFIT')"
echo "CLOSE_LOSS:$(echo "$LOGS" | grep -c 'CLOSE_LOSS')"
echo "DRY_RUN_CLOSE:$(echo "$LOGS" | grep -c 'DRY_RUN_CLOSE')"
echo "DRY_RUN_BUY_FILL:$(echo "$LOGS" | grep -c 'DRY_RUN_BUY_FILL')"
echo "---DB---"
sudo sqlite3 data/trades.db "SELECT COUNT(*) FROM trades" 2>/dev/null || echo "DB_ERROR"
sudo sqlite3 data/trades.db "SELECT side,COUNT(*),ROUND(AVG(profit),4),ROUND(SUM(profit),4) FROM trades GROUP BY side" 2>/dev/null || echo "DB_EMPTY"
