import sqlite3, time
c = sqlite3.connect("/var/lib/docker/volumes/triada_shared-data/_data/trades.db")
cur = c.cursor()
today = time.time() - 24*3600

rows = cur.execute("SELECT side,symbol,ROUND(profit,4),timestamp FROM trades WHERE timestamp>? ORDER BY timestamp DESC", (today,)).fetchall()
print("Trades 24h:", len(rows))
for r in rows[:10]:
    t = time.strftime("%H:%M", time.localtime(r[3]))
    print("  {} {:10s} {:12s} PnL={}".format(t, r[0], r[1], r[2]))

profits = [p[0] for p in cur.execute("SELECT profit FROM trades WHERE side LIKE ? AND timestamp>?", ("sell%", today)).fetchall()]
wins = sum(1 for p in profits if p and p > 0)
losses = sum(1 for p in profits if p and p <= 0)
total = sum(p for p in profits if p)
avg = total/len(profits) if profits else 0
print("Closed: {}  Wins: {}  Losses: {}  Total PnL: ${:.2f}  Avg: ${:.2f}".format(len(profits), wins, losses, total, avg))

linked = cur.execute("SELECT COUNT(*) FROM dispatcher_features WHERE trade_id > 0").fetchone()[0]
with_profit = cur.execute("SELECT COUNT(*) FROM dispatcher_features WHERE profit IS NOT NULL").fetchone()[0]
print("Dispatcher linked: {}  With profit: {}".format(linked, with_profit))
c.close()
