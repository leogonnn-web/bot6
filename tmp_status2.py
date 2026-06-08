import sqlite3

conn = sqlite3.connect('/app/shared/state/trades.db')
cur = conn.cursor()

# All trades ordered by timestamp
cur.execute('SELECT symbol, side, amount, price, timestamp, profit FROM trades ORDER BY timestamp DESC LIMIT 10')
trades = cur.fetchall()

# Stats
cur.execute('SELECT COUNT(*), SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END), SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END), SUM(profit) FROM trades')
stats = cur.fetchone()

conn.close()

print('=== LAST 10 TRADES ===')
for row in trades:
    sym, side, amt, price, ts, profit = row
    pnl = f'${profit:.3f}' if profit is not None else 'N/A'
    print(f'  {sym} | {side} | amt={amt:.4f} | price={price:.6f} | PnL={pnl}')

print(f'\n=== SESSION ===')
print(f'  Total: {stats[0] or 0}, Wins: {stats[1] or 0}, Losses: {stats[2] or 0}, Net PnL: ${stats[3] or 0:.3f}')
