import sqlite3
conn = sqlite3.connect('/app/shared/state/trades.db')
c = conn.cursor()

# Все записи trades
print("=== ВСЕ ТРЕЙДЫ (первые 30) ===")
c.execute("SELECT symbol, side, amount, price, timestamp FROM trades ORDER BY timestamp DESC LIMIT 30")
for row in c.fetchall():
    print(f"  {row[0]:<10} {row[1]:<4} amt={row[2]:<12.4f} price={row[3]:<12.6f}")

# Статистика по сделкам
print("\n=== СТАТИСТИКА ===")
c.execute("SELECT COUNT(*) FROM trades")
print(f"Всего записей: {c.fetchone()[0]}")

c.execute("SELECT side, COUNT(*) FROM trades GROUP BY side")
for side, count in c.fetchall():
    print(f"  {side}: {count}")

# Считаем PnL FIFO
print("\n=== FIFO PnL ===")
c.execute("SELECT symbol, side, amount, price FROM trades ORDER BY timestamp ASC")
rows = c.fetchall()

from collections import defaultdict
open_buys = defaultdict(list)
session_profit = 0.0
tp_count = 0
sl_count = 0

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
        if profit > 0:
            tp_count += 1
        else:
            sl_count += 1
        remainder = buy_amount - matched
        if remainder > 0:
            open_buys[symbol].insert(0, (remainder, buy_price))

print(f"Total PnL: ${session_profit:.2f}")
print(f"TP trades: {tp_count}")
print(f"SL/loss trades: {sl_count}")

# Топ убыточных монет
print("\n=== PnL ПО МОНЕТАМ ===")
c.execute("SELECT symbol, side, amount, price FROM trades ORDER BY timestamp ASC")
rows = c.fetchall()
open_buys = defaultdict(list)
pnl_by_symbol = defaultdict(float)

for symbol, side, amount, price in rows:
    amount = float(amount)
    price = float(price)
    if side == 'buy':
        open_buys[symbol].append((amount, price))
    elif side == 'sell' and open_buys[symbol]:
        buy_amount, buy_price = open_buys[symbol].pop(0)
        matched = min(amount, buy_amount)
        profit = (price - buy_price) * matched
        pnl_by_symbol[symbol] += profit
        remainder = buy_amount - matched
        if remainder > 0:
            open_buys[symbol].insert(0, (remainder, buy_price))

for sym, pnl in sorted(pnl_by_symbol.items(), key=lambda x: x[1]):
    print(f"  {sym:<10}: ${pnl:>+8.2f}")

conn.close()
