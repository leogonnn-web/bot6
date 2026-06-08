import sqlite3, time, sys

DB = sys.argv[1] if len(sys.argv) > 1 else '/var/lib/docker/volumes/triada_shared-data/_data/trades.db'
c = sqlite3.connect(DB)
cur = c.cursor()

cur.execute('SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM trades')
n, mn, mx = cur.fetchone()
fmt = lambda t: time.strftime('%Y-%m-%d %H:%M', time.localtime(t)) if t else None
print('TOTAL_ROWS', n)
print('SPAN', fmt(mn), '->', fmt(mx))
if mn and mx:
    print('SPAN_HOURS', round((mx - mn) / 3600, 1))

print('\n--- BY SIDE ---')
for r in cur.execute('SELECT side, COUNT(*), ROUND(SUM(profit),2) FROM trades GROUP BY side ORDER BY 2 DESC'):
    print(f'{r[0]:24s} cnt={r[1]:5d} sum_profit=${r[2]}')

print('\n--- REALIZED PnL (rows with profit != 0) ---')
cur.execute('SELECT COUNT(*), ROUND(SUM(profit),4), ROUND(AVG(profit),4), ROUND(MIN(profit),4), ROUND(MAX(profit),4) FROM trades WHERE profit != 0')
cnt, s, a, lo, hi = cur.fetchone()
print(f'count={cnt} total=${s} avg=${a} min=${lo} max=${hi}')

cur.execute('SELECT SUM(profit>0), SUM(profit<0) FROM trades WHERE profit != 0')
w, l = cur.fetchone()
w = w or 0; l = l or 0
if (w + l) > 0:
    print(f'wins={w} losses={l} win_rate={round(100*w/(w+l),1)}%')

cur.execute('SELECT ROUND(SUM(profit),4) FROM trades WHERE profit>0')
gp = cur.fetchone()[0] or 0
cur.execute('SELECT ROUND(SUM(profit),4) FROM trades WHERE profit<0')
gl = cur.fetchone()[0] or 0
print(f'gross_profit=${gp} gross_loss=${gl} profit_factor={round(gp/abs(gl),2) if gl else float("inf")}')

print('\n--- EXIT TYPE BREAKDOWN ---')
for r in cur.execute("SELECT side, COUNT(*), ROUND(SUM(profit),4), ROUND(AVG(profit),4) FROM trades WHERE side LIKE 'sell%' GROUP BY side ORDER BY 3"):
    print(f'{r[0]:24s} cnt={r[1]:5d} sum=${r[2]} avg=${r[3]}')

print('\n--- PnL BY SYMBOL (top/bottom 10 by realized) ---')
rows = list(cur.execute("SELECT symbol, COUNT(*), ROUND(SUM(profit),4) FROM trades WHERE profit!=0 GROUP BY symbol ORDER BY 3 DESC"))
for r in rows[:10]:
    print(f'  +  {r[0]:14s} trades={r[1]:4d} pnl=${r[2]}')
print('  ...')
for r in rows[-10:]:
    print(f'  -  {r[0]:14s} trades={r[1]:4d} pnl=${r[2]}')

print('\n--- LAST 15 REALIZED EXITS ---')
for r in cur.execute("SELECT timestamp, symbol, side, ROUND(profit,4) FROM trades WHERE side LIKE 'sell%' ORDER BY timestamp DESC LIMIT 15"):
    print(f'  {fmt(r[0])}  {r[1]:14s} {r[2]:18s} ${r[3]}')

print('\n--- DAILY PnL (last 10 days) ---')
for r in cur.execute("SELECT date(timestamp,'unixepoch','localtime') d, COUNT(*), ROUND(SUM(profit),4) FROM trades WHERE profit!=0 GROUP BY d ORDER BY d DESC LIMIT 10"):
    print(f'  {r[0]}  exits={r[1]:4d}  pnl=${r[2]}')

c.close()
