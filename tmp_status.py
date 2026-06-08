import sqlite3

conn = sqlite3.connect('/app/shared/state/trades.db')
cur = conn.cursor()

# Active trades
cur.execute("SELECT symbol, side, status, tp_price, sl_price FROM trades WHERE status='OPEN' OR status='GRID_ACTIVE' ORDER BY timestamp DESC LIMIT 5")
active = cur.fetchall()

# Last 5 closed trades
cur.execute("SELECT symbol, status, realized_pnl FROM trades WHERE status IN ('TP_HIT','SL_HIT','CLOSED') ORDER BY timestamp DESC LIMIT 5")
closed = cur.fetchall()

# Session stats
cur.execute("SELECT COUNT(*), SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END), SUM(realized_pnl) FROM trades WHERE realized_pnl IS NOT NULL")
stats = cur.fetchone()

conn.close()

print('=== ACTIVE/GRID ===')
for row in active:
    print(f'  {row[0]} | {row[1]} | {row[2]} | TP={row[3]} SL={row[4]}')
if not active:
    print('  No active trades')

print('\n=== LAST CLOSED ===')
for row in closed:
    print(f'  {row[0]} | {row[1]} | PnL=${row[2]:.3f}')
if not closed:
    print('  No closed trades')

print(f'\n=== SESSION ===')
print(f'  Total closed: {stats[0] or 0}, Wins: {stats[1] or 0}, Losses: {stats[2] or 0}, Net PnL: ${stats[3] or 0:.3f}')
