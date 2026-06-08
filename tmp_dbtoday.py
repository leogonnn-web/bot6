import sqlite3, time
conn=sqlite3.connect('/app/shared/state/trades.db')
c=conn.cursor()
today_start=int(time.time()) - (int(time.time()) % 86400)

c.execute("SELECT COUNT(*), SUM(profit) FROM trades WHERE side LIKE 'sell%' AND timestamp >= ?", (today_start,))
row=c.fetchone()
print("Today sells:", row[0], "Today profit:", round(row[1] or 0, 2))

c.execute("SELECT side, COUNT(*), SUM(profit) FROM trades WHERE side LIKE 'sell%' AND timestamp >= ? GROUP BY side", (today_start,))
for r in c.fetchall():
    print("  ", r[0], "count:", r[1], "sum:", round(r[2] or 0, 2))

conn.close()
