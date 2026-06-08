import sqlite3
from collections import defaultdict

conn=sqlite3.connect('/app/shared/state/trades.db')
c=conn.cursor()

# Simulate actual get_session_stats logic
open_buys = defaultdict(list)
total_trades = 0
winning_trades = 0
session_profit = 0.0
loss_trades = []

c.execute('SELECT symbol, side, amount, price FROM trades ORDER BY timestamp ASC')
rows = c.fetchall()

for symbol, side, amount, price in rows:
    amount = float(amount)
    price = float(price)
    
    if side == 'buy':
        open_buys[symbol].append((amount, price))
    elif side == 'sell' and open_buys[symbol]:
        buy_amount, buy_price = open_buys[symbol].pop(0)
        matched = min(amount, buy_amount)
        profit = (price - buy_price) * matched
        session_profit += profit
        total_trades += 1
        if profit > 0:
            winning_trades += 1
        else:
            loss_trades.append((symbol, price, buy_price, profit))
        remainder = buy_amount - matched
        if remainder > 0:
            open_buys[symbol].insert(0, (remainder, buy_price))

print(f"session_profit: ${session_profit:.2f}")
print(f"total_trades: {total_trades}")
print(f"winning: {winning_trades}")
print(f"losses: {len(loss_trades)}")
print(f"\nBiggest losing sells:")
loss_trades.sort(key=lambda x: x[3])
for t in loss_trades[:10]:
    print(f"  {t[0]}: sell=${t[1]:.6f} buy=${t[2]:.6f} loss=${t[3]:.4f}")

print(f"\nOpen buys remaining: {sum(len(v) for v in open_buys.values())}")
conn.close()
