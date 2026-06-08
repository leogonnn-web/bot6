import sqlite3
conn=sqlite3.connect('/app/shared/state/trades.db')
c=conn.cursor()
c.execute("SELECT symbol, profit, side, timestamp FROM trades WHERE side LIKE 'sell%' AND profit < 0 ORDER BY timestamp DESC LIMIT 10")
for r in c.fetchall():
    print(r)
conn.close()
