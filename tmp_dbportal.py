import sqlite3
conn=sqlite3.connect('/app/shared/state/trades.db')
c=conn.cursor()
c.execute("SELECT side, price, amount, profit, timestamp FROM trades WHERE symbol = 'PORTAL/USDT' ORDER BY timestamp ASC")
for r in c.fetchall():
    print(r)
conn.close()
