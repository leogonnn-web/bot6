#!/bin/bash
cd ~/triada
LOGS=$(sudo docker compose logs hydra-bot 2>&1)
echo "GRID_CANCEL:$(echo "$LOGS" | grep -c 'GRID_CANCEL')"
echo "GRID_EXPIRED:$(echo "$LOGS" | grep -c 'GRID_EXPIRED')"
echo "BREAKEVEN:$(echo "$LOGS" | grep -c 'BREAKEVEN')"
echo "PANIC:$(echo "$LOGS" | grep -c 'PANIC')"
echo "DRY_RUN_SL:$(echo "$LOGS" | grep -c 'DRY_RUN_SL')"
echo "TRAILING_STOP:$(echo "$LOGS" | grep -c 'TRAILING_STOP')"
echo "PROFIT_TAKEN:$(echo "$LOGS" | grep -c 'PROFIT_TAKEN')"
echo "CLOSE_PROFIT:$(echo "$LOGS" | grep -c 'CLOSE_PROFIT')"
echo "CLOSE_LOSS:$(echo "$LOGS" | grep -c 'CLOSE_LOSS')"
echo "RESET:$(echo "$LOGS" | grep -c '@RESET@')"
