import sqlite3
conn=sqlite3.connect('/app/shared/state/trades.db')
c=conn.cursor()

# Losses
c.execute("SELECT COUNT(*), SUM(profit) FROM trades WHERE side LIKE 'sell%' AND profit < 0")
loss=c.fetchone()

# Zero profit
c.execute("SELECT COUNT(*), SUM(profit) FROM trades WHERE side LIKE 'sell%' AND profit = 0")
zero=c.fetchone()

# Small wins
c.execute("SELECT COUNT(*), SUM(profit) FROM trades WHERE side LIKE 'sell%' AND profit > 0 AND profit < 0.1")
small=c.fetchone()

# Medium wins
c.execute("SELECT COUNT(*), SUM(profit) FROM trades WHERE side LIKE 'sell%' AND profit >= 0.1 AND profit < 0.5")
med=c.fetchone()

# Big wins
c.execute("SELECT COUNT(*), SUM(profit) FROM trades WHERE side LIKE 'sell%' AND profit >= 0.5")
big=c.fetchone()

print("Loss:", loss[0], "sum:", round(loss[1] or 0, 2))
print("Zero:", zero[0], "sum:", round(zero[1] or 0, 2))
print("Small (0-0.1):", small[0], "sum:", round(small[1] or 0, 2))
print("Medium (0.1-0.5):", med[0], "sum:", round(med[1] or 0, 2))
print("Big (0.5+):", big[0], "sum:", round(big[1] or 0, 2))

# Check breakeven / timeout / panic breakdown
c.execute("SELECT side, COUNT(*), SUM(profit) FROM trades WHERE side LIKE 'sell%' GROUP BY side")
print("\nBy exit type:")
for row in c.fetchall():
    print(f"  {row[0]}: count={row[1]}, sum={round(row[2] or 0, 2)}")

conn.close()
