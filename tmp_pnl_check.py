import sqlite3, time, sys

DB = '/var/lib/docker/volumes/triada_shared-data/_data/trades.db'
CUT = time.mktime((2026, 6, 4, 15, 55, 0, 0, 0, -1))  # restart time (server local)
c = sqlite3.connect(DB)
cur = c.cursor()
fmt = lambda t: time.strftime('%m-%d %H:%M', time.localtime(t)) if t else None

def block(label, where, params):
    print(f'\n===== {label} =====')
    q = f"SELECT COUNT(*), ROUND(SUM(profit),4), ROUND(AVG(profit),4) FROM trades WHERE profit!=0 AND {where}"
    cnt, s, a = cur.execute(q, params).fetchone()
    print(f'realized: count={cnt} total=${s} avg=${a}')
    w = cur.execute(f"SELECT COUNT(*) FROM trades WHERE profit>0 AND {where}", params).fetchone()[0]
    l = cur.execute(f"SELECT COUNT(*) FROM trades WHERE profit<0 AND {where}", params).fetchone()[0]
    if w + l:
        print(f'wins={w} losses={l} win_rate={round(100*w/(w+l),1)}%')
    gp = cur.execute(f"SELECT SUM(profit) FROM trades WHERE profit>0 AND {where}", params).fetchone()[0] or 0
    gl = cur.execute(f"SELECT SUM(profit) FROM trades WHERE profit<0 AND {where}", params).fetchone()[0] or 0
    print(f'gross_profit=${round(gp,4)} gross_loss=${round(gl,4)} PF={round(gp/abs(gl),2) if gl else float("inf")}')
    awin = cur.execute(f"SELECT ROUND(AVG(profit),4) FROM trades WHERE profit>0 AND {where}", params).fetchone()[0]
    print(f'avg_WIN=${awin}')
    print('by side:')
    for r in cur.execute(f"SELECT side, COUNT(*), ROUND(SUM(profit),4), ROUND(AVG(profit),4) FROM trades WHERE side LIKE 'sell%' AND {where} GROUP BY side ORDER BY 3", params):
        print(f'   {r[0]:16s} cnt={r[1]:5d} sum=${r[2]} avg=${r[3]}')

block('BEFORE restart', 'timestamp < ?', (CUT,))
block('AFTER restart (fee-adjusted code)', 'timestamp >= ?', (CUT,))

# partial_tp specific: confirm non-zero now
print('\n===== sell_partial CHECK =====')
pre0 = cur.execute("SELECT COUNT(*) FROM trades WHERE side='sell_partial' AND timestamp<? AND profit=0", (CUT,)).fetchone()[0]
pre = cur.execute("SELECT COUNT(*) FROM trades WHERE side='sell_partial' AND timestamp<?", (CUT,)).fetchone()[0]
postnz = cur.execute("SELECT COUNT(*) FROM trades WHERE side='sell_partial' AND timestamp>=? AND profit!=0", (CUT,)).fetchone()[0]
post = cur.execute("SELECT COUNT(*) FROM trades WHERE side='sell_partial' AND timestamp>=?", (CUT,)).fetchone()[0]
print(f'BEFORE: total={pre} with_zero_profit={pre0}')
print(f'AFTER : total={post} with_nonzero_profit={postnz}')

print('\n===== FIRST 12 EXITS AFTER RESTART =====')
for r in cur.execute("SELECT timestamp, symbol, side, ROUND(profit,4) FROM trades WHERE side LIKE 'sell%' AND timestamp>=? ORDER BY timestamp ASC LIMIT 12", (CUT,)):
    print(f'  {fmt(r[0])}  {r[1]:14s} {r[2]:16s} ${r[3]}')

c.close()
