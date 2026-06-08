#!/bin/bash
# Auto-stop Hydra bot after first trade closes (for real-money test with max_trades=1)
# Usage: ./auto_stop_after_first_trade.sh &

DB_PATH="${DB_PATH:-/app/trades.db}"
MAINTENANCE_URL="${MAINTENANCE_URL:-http://localhost:9090/maintenance}"
POLL_INTERVAL=5

echo "[@AUTO_STOP@] Watching for first sell trade in $DB_PATH..."

while true; do
    # Check if there's any sell/sell_panic/sell_partial today in trades DB
    # SQLite date() converts unix timestamp to YYYY-MM-DD
    result=$(sqlite3 "$DB_PATH" \
        "SELECT side FROM trades WHERE date(timestamp, 'unixepoch') = date('now') AND side LIKE 'sell%' LIMIT 1;" \
        2>/dev/null)
    
    if [ -n "$result" ]; then
        echo "[@AUTO_STOP@] First sell detected: side=$result — triggering maintenance mode"
        
        # Call maintenance endpoint to stop bot from opening new positions
        response=$(curl -s -X POST "$MAINTENANCE_URL" 2>/dev/null)
        echo "[@AUTO_STOP@] Maintenance response: $response"
        
        # Also log to file for audit
        echo "$(date -Iseconds) — auto_stop triggered after first sell ($result)" >> /tmp/hydra_auto_stop.log
        
        echo "[@AUTO_STOP@] Done. Bot will close position and exit."
        exit 0
    fi
    
    sleep $POLL_INTERVAL
done
