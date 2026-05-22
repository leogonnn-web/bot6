import sqlite3

conn = sqlite3.connect('trades.db')
cursor = conn.cursor()

# Trade breakdown
cursor.execute('SELECT side, COUNT(*) FROM trades GROUP BY side')
results = cursor.fetchall()
print('Trade breakdown:')
for side, count in results:
    print(f'  {side}: {count}')

# Symbols by buy volume
cursor.execute('SELECT symbol, COUNT(*) FROM trades WHERE side="buy" GROUP BY symbol ORDER BY COUNT(*) DESC')
print('\nSymbols by buy volume:')
for symbol, count in cursor.fetchall():
    print(f'  {symbol}: {count}')

# Profit by symbol
cursor.execute('SELECT symbol, side, SUM(amount * price) as total FROM trades GROUP BY symbol, side ORDER BY symbol')
print('\nProfit by symbol:')
for symbol, side, total in cursor.fetchall():
    print(f'  {symbol} {side}: ${total:.2f}')

conn.close()
