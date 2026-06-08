#!/bin/bash
cd ~/triada
LOGS=$(sudo docker compose logs hydra-bot 2>&1)
echo "TP_HITS:$(echo "$LOGS" | grep -c 'Virtual TP hit')"
echo "SL_HITS:$(echo "$LOGS" | grep -c 'Virtual SL hit')"
echo "GRID_COMPLETE:$(echo "$LOGS" | grep -c 'GRID_COMPLETE')"
echo "SIGNAL_APPROVED:$(echo "$LOGS" | grep -c 'SIGNAL_APPROVED')"
echo "PARTIAL_TP:$(echo "$LOGS" | grep -c 'Partial TP hit')"
echo "BREAKEVEN:$(echo "$LOGS" | grep -c 'BREAKEVEN_TIMEOUT')"
