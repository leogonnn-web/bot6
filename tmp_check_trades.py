import sqlite3
conn = sqlite3.connect('/app/shared/state/trades.db')
cur = conn.cursor()

# Last 10 trades with timestamps
cur.execute('SELECT symbol, side, timestamp, profit FROM trades ORDER BY timestamp DESC LIMIT 10')
for row in cur.fetchall():
    print(row)

# Count trades in last 30 minutes
cur.execute('SELECT COUNT(*) FROM trades WHERE timestamp > 0')
print('Total trades:', cur.fetchone()[0])

conn.close()
