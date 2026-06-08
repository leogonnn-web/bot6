#!/bin/bash
cd ~/triada
LOGS=$(sudo docker compose logs hydra-bot 2>&1)
echo "IN_POSITION_ERROR:$(echo "$LOGS" | grep -c 'IN_POSITION_ERROR')"
echo "IN_POSITION:$(echo "$LOGS" | grep -c 'IN_POSITION')"
echo "BUYING:$(echo "$LOGS" | grep -c 'BUYING')"
echo "IDLE:$(echo "$LOGS" | grep -c 'IDLE')"
echo "SCANNING:$(echo "$LOGS" | grep -c 'SCANNING')"
echo "EXITING:$(echo "$LOGS" | grep -c 'EXITING')"
echo "GRID_ACTIVE:$(echo "$LOGS" | grep -c 'GRID_ACTIVE')"
echo "GRID_COMPLETE:$(echo "$LOGS" | grep -c 'GRID_COMPLETE')"
