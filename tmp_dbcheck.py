import sqlite3
conn=sqlite3.connect('/app/shared/state/trades.db')
c=conn.cursor()

c.execute("SELECT side, COUNT(*), SUM(profit) FROM trades GROUP BY side")
print("All trades:")
for r in c.fetchall():
    print(f"  {r[0]}: count={r[1]}, sum={round(r[2] or 0, 2)}")

c.execute("SELECT COUNT(*) FROM trades WHERE side = 'buy'")
buys = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM trades WHERE side LIKE 'sell%'")
sells = c.fetchone()[0]
print(f"\nBuy/Sell ratio: {buys} buys / {sells} sells")
print(f"Unmatched: {abs(buys - sells)}")

# Check session_stats calculation
from collections import defaultdict
open_buys = defaultdict(list)
total_trades = 0
winning_trades = 0
session_profit = 0.0

c.execute('SELECT symbol, side, amount, price FROM trades ORDER BY timestamp ASC')
rows = c.fetchall()
for symbol, side, amount, price in rows:
    amount = float(amount)
    price = float(price)
    if side == 'buy':
        open_buys[symbol].append((amount, price))
    elif side.startswith('sell') and open_buys[symbol]:
        buy_amount, buy_price = open_buys[symbol].pop(0)
        matched = min(amount, buy_amount)
        profit = (price - buy_price) * matched
        session_profit += profit
        total_trades += 1
        if profit > 0:
            winning_trades += 1
        remainder = buy_amount - matched
        if remainder > 0:
            open_buys[symbol].insert(0, (remainder, buy_price))

win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
print(f"\nFIFO session_profit: ${session_profit:.2f}")
print(f"FIFO total_trades: {total_trades}")
print(f"FIFO winning: {winning_trades}")
print(f"FIFO win_rate: {win_rate:.1f}%")
print(f"\nOpen buys remaining:")
for sym, buys in open_buys.items():
    if buys:
        total = sum(b[0] for b in buys)
        print(f"  {sym}: {len(buys)} orders, total={total:.4f}")

conn.close()
