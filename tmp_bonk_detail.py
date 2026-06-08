import sqlite3
from collections import defaultdict

conn = sqlite3.connect('/app/shared/state/trades.db')
c = conn.cursor()

print("=== BONK СДЕЛКИ (последние 50) ===")
c.execute("""
    SELECT side, amount, price, timestamp, profit
    FROM trades
    WHERE symbol = 'BONK/USDT'
    ORDER BY timestamp DESC
    LIMIT 50
""")

buys = []
sells = []
for side, amount, price, ts, profit in c.fetchall():
    if side == 'buy':
        buys.append((amount, price))
    else:
        sells.append((side, amount, price, profit))

print(f"Всего buy: {len(buys)}, sell: {len(sells)}")

# FIFO PnL для BONK
print("\n=== BONK FIFO PnL ===")
c.execute("""
    SELECT side, amount, price, timestamp, profit
    FROM trades
    WHERE symbol = 'BONK/USDT'
    ORDER BY timestamp ASC
""")

open_buys = []
total_pnl = 0.0
wins = 0
losses = 0

for side, amount, price, ts, profit in c.fetchall():
    amount = float(amount)
    price = float(price)
    if side == 'buy':
        open_buys.append((amount, price))
    elif side.startswith('sell') and open_buys:
        buy_amt, buy_price = open_buys.pop(0)
        matched = min(amount, buy_amt)
        pnl = (price - buy_price) * matched
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        if buy_amt > matched:
            open_buys.insert(0, (buy_amt - matched, buy_price))

print(f"BONK PnL: ${total_pnl:.2f}")
print(f"Wins: {wins}, Losses: {losses}")

# Средние цены
print("\n=== BONK СТАТИСТИКА ===")
c.execute("""
    SELECT side, AVG(price), MIN(price), MAX(price), COUNT(*)
    FROM trades
    WHERE symbol = 'BONK/USDT'
    GROUP BY side
""")
for row in c.fetchall():
    print(f"  {row[0]:<15}: avg={row[1]:.8f} min={row[2]:.8f} max={row[3]:.8f} count={row[4]}")

conn.close()
