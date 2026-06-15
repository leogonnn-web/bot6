"""Проверка логики get_session_stats(since_ts): тот же FIFO, но с фильтром времени.
Read-only. Доказывает, что baseline отсекает старую dry-run прибыль.
"""
import sqlite3, glob, time
from collections import defaultdict


def session_profit(db, since_ts):
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute('SELECT symbol, side, amount, price FROM trades '
                'WHERE timestamp >= ? ORDER BY timestamp ASC', (since_ts,))
    rows = cur.fetchall()
    conn.close()
    open_buys = defaultdict(list)
    profit = 0.0
    trades = 0
    for symbol, side, amount, price in rows:
        amount = float(amount); price = float(price)
        if side == 'buy':
            open_buys[symbol].append((amount, price))
        elif side == 'buy_grid_complete':
            continue
        elif side.startswith('sell') and open_buys[symbol]:
            if side == 'sell_partial':
                ba, bp = open_buys[symbol][0]
                m = min(amount, ba); rem = ba - m
                if rem > 0: open_buys[symbol][0] = (rem, bp)
                else: open_buys[symbol].pop(0)
                continue
            ba, bp = open_buys[symbol].pop(0)
            m = min(amount, ba)
            profit += (price - bp) * m
            trades += 1
            rem = ba - m
            if rem > 0: open_buys[symbol].insert(0, (rem, bp))
    return profit, trades


db = sorted(glob.glob('/app/shared/state/*.db'))[0]
now = time.time()
for label, since in [('ALL-TIME (since_ts=0)', 0),
                     ('last 6h', now - 6 * 3600),
                     ('last 1h', now - 3600)]:
    p, t = session_profit(db, since)
    print('%-24s closed=%-5d session_profit=$%.4f' % (label, t, p))

print('\nОжидаемо: ALL-TIME = вся накопленная dry-run прибыль (~$155),')
print('а last 1h/6h = маленькое окно. В реале baseline=go-live отсечёт старое.')
