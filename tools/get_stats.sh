#!/bin/bash
cd ~/triada
LOGS=$(sudo docker compose logs hydra-bot 2>&1)
echo "APPROVED:$(echo "$LOGS" | grep -c 'SIGNAL_APPROVED')"
echo "GRID_COMPLETE:$(echo "$LOGS" | grep -c 'GRID_COMPLETE')"
echo "TP:$(echo "$LOGS" | grep -cE 'DRY_RUN_TP|TP_HIT')"
echo "SL:$(echo "$LOGS" | grep -cE 'SL_HIT|TRAILING_STOP')"
echo "BREAKEVEN:$(echo "$LOGS" | grep -c 'BREAKEVEN')"
echo "BUY:$(echo "$LOGS" | grep -c 'rec=BUY')"
echo "SKIP:$(echo "$LOGS" | grep -c 'rec=SKIP')"
echo "REJECTED:$(echo "$LOGS" | grep -c 'SIGNAL_REJECT')"
