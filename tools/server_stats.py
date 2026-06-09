"""Снятие статистики эффективности Hydra с боевой БД (dispatcher_features + trades).

Запуск на сервере внутри контейнера:
    scp -i triada-key2.pem tools/server_stats.py ubuntu@<IP>:/tmp/server_stats.py
    ssh -i triada-key2.pem ubuntu@<IP> \
        "sudo docker cp /tmp/server_stats.py hydra-bot:/tmp/server_stats.py && \
         sudo docker exec hydra-bot python /tmp/server_stats.py"

Граница отсчёта = самая ранняя строка dispatcher_features (таблица обнуляется при сбросе),
поэтому статистика автоматически считается «с момента последнего обнуления».
"""
import sqlite3, glob, datetime

db = sorted(glob.glob('/app/shared/state/*.db'))[0]
conn = sqlite3.connect(db)
cur = conn.cursor()

def ts(u):
    return datetime.datetime.utcfromtimestamp(u).strftime('%Y-%m-%d %H:%M:%S UTC')

now_u = cur.execute('SELECT MAX(timestamp) FROM trades').fetchone()[0]
cut = cur.execute('SELECT MIN(timestamp) FROM dispatcher_features').fetchone()[0]
hours = (now_u - cut) / 3600.0

print('================ ОБЩЕЕ ================')
print('now (last trade):', ts(now_u))
print('reset / first feature:', ts(cut))
print('прошло часов с обнуления: %.2f (%.2f дней)' % (hours, hours / 24))

print('\n================ DISPATCHER_FEATURES (с обнуления) ================')
total = cur.execute('SELECT COUNT(*) FROM dispatcher_features').fetchone()[0]
exec_pos = cur.execute('SELECT COUNT(DISTINCT trade_id) FROM dispatcher_features WHERE trade_id>0').fetchone()[0]
zero_tid = cur.execute('SELECT COUNT(*) FROM dispatcher_features WHERE trade_id=0').fetchone()[0]
print('строк всего: %d   (%.1f/час)' % (total, total / hours))
print('уникальных trade_id>0 (исполнено позиций): %d   (%.1f/час, ~%.0f/день)' % (
    exec_pos, exec_pos / hours, exec_pos / hours * 24))
print('строк с trade_id=0 (лог до исполнения): %d' % zero_tid)
print('\nпо режимам:')
for mode, c in cur.execute('SELECT mode, COUNT(*) FROM dispatcher_features GROUP BY mode ORDER BY 2 DESC'):
    print('  %-14s %5d  (%.1f%%)' % (mode, c, 100.0 * c / total))

lab = cur.execute('SELECT COUNT(*) FROM dispatcher_features WHERE profit IS NOT NULL').fetchone()[0]
print('\nразмечено (profit not null): %d из %d' % (lab, total))
if lab:
    wins = cur.execute('SELECT COUNT(*) FROM dispatcher_features WHERE profit IS NOT NULL AND profit>0').fetchone()[0]
    psum = cur.execute('SELECT SUM(profit) FROM dispatcher_features WHERE profit IS NOT NULL').fetchone()[0]
    print('  win-rate: %.1f%% (%d/%d)' % (100.0 * wins / lab, wins, lab))
    print('  сумма profit(labeled): $%.4f' % psum)
    # размечено по режимам (важно для порога 500 conservative)
    print('  по режимам (labeled):')
    for mode, c in cur.execute('SELECT mode, COUNT(*) FROM dispatcher_features WHERE profit IS NOT NULL GROUP BY mode'):
        print('    %-14s %d' % (mode, c))

print('\nscore (все): min/avg/max = %.2f / %.2f / %.2f' %
      tuple(cur.execute('SELECT MIN(score),AVG(score),MAX(score) FROM dispatcher_features').fetchone()))
print('score по режимам (avg):')
for mode, a, n in cur.execute('SELECT mode, AVG(score), COUNT(*) FROM dispatcher_features GROUP BY mode'):
    print('  %-14s avg=%.2f  n=%d' % (mode, a, n))

print('\nсредние фичи:')
row = cur.execute('SELECT AVG(confidence),AVG(rvol_spike),AVG(dump_depth),AVG(obi_skew),AVG(btc_1h),AVG(take_profit_pct) FROM dispatcher_features').fetchone()
for l, v in zip(['confidence', 'rvol_spike', 'dump_depth', 'obi_skew', 'btc_1h', 'take_profit_pct'], row):
    print('  %-16s %s' % (l, ('%.4f' % v) if v is not None else 'None'))

print('\nраспределение исполненных позиций по часам (UTC):')
for h, c in cur.execute("SELECT strftime('%Y-%m-%d %H', datetime(timestamp,'unixepoch')) hh, COUNT(DISTINCT trade_id) FROM dispatcher_features WHERE trade_id>0 GROUP BY hh ORDER BY hh"):
    print('  %s  %s %d' % (h, '#' * c, c))

print('\n================ TRADES (с обнуления) ================')
tot_tr = cur.execute('SELECT COUNT(*) FROM trades WHERE timestamp>=?', (cut,)).fetchone()[0]
print('записей trades: %d   (%.1f/час)' % (tot_tr, tot_tr / hours))
print('по side (count, sum profit):')
for side, c, p in cur.execute('SELECT side, COUNT(*), COALESCE(SUM(profit),0) FROM trades WHERE timestamp>=? GROUP BY side ORDER BY 2 DESC', (cut,)):
    print('  %-22s n=%-5d profit=$%.4f' % (side, c, p))

closes = cur.execute("SELECT COUNT(*), COALESCE(SUM(profit),0), SUM(CASE WHEN profit>0 THEN 1 ELSE 0 END) FROM trades WHERE timestamp>=? AND (side LIKE '%sell%' OR side LIKE '%exit%' OR side LIKE '%tp%' OR side LIKE '%close%')", (cut,)).fetchone()
if closes[0]:
    print('\nзакрытий: %d   realized PnL: $%.4f   win-rate: %.1f%%' % (
        closes[0], closes[1], 100.0 * closes[2] / closes[0]))
entries = cur.execute("SELECT COUNT(*) FROM trades WHERE timestamp>=? AND side='buy'", (cut,)).fetchone()[0]
print('входов (side=buy): %d   (%.1f/час, ~%.0f/день)' % (entries, entries / hours, entries / hours * 24))

print('\nтоп символов по входам:')
for sym, c in cur.execute("SELECT symbol, COUNT(*) FROM trades WHERE timestamp>=? AND side='buy' GROUP BY symbol ORDER BY 2 DESC LIMIT 10", (cut,)):
    print('  %-14s %d' % (sym, c))

print('\nвсего записей trades за всё время (вкл. до обнуления): %d' %
      cur.execute('SELECT COUNT(*) FROM trades').fetchone()[0])
conn.close()
