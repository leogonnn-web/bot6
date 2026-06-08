import sqlite3
from collections import defaultdict

conn=sqlite3.connect('/app/shared/state/trades.db')
c=conn.cursor()

# Check duplicate buy entries for PORTAL
c.execute("SELECT side, COUNT(*), AVG(price), MIN(timestamp), MAX(timestamp) FROM trades WHERE symbol = 'PORTAL/USDT' GROUP BY side")
print("PORTAL/USDT trades:")
for r in c.fetchall():
    print(f"  {r[0]}: count={r[1]}, avg_price={r[2]:.6f}, first={r[3]}, last={r[4]}")

# Check if buy_grid_complete always has a matching buy
c.execute("SELECT symbol, side, price, amount, timestamp FROM trades WHERE symbol = 'PORTAL/USDT' ORDER BY timestamp ASC LIMIT 20")
print("\nFirst 20 PORTAL trades:")
for r in c.fetchall():
    print(f"  {r[1]:18s} price={r[2]:.6f} amt={r[3]:.2f} ts={r[4]}")

conn.close()
