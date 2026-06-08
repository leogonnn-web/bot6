import sqlite3
c = sqlite3.connect('/var/lib/docker/volumes/triada_shared-data/_data/trades.db')
cur = c.cursor()
print('dispatcher_features rows:', cur.execute('SELECT COUNT(*) FROM dispatcher_features').fetchone()[0])
print('  linked to real trades (trade_id>0):', cur.execute('SELECT COUNT(*) FROM dispatcher_features WHERE trade_id>0').fetchone()[0])
print('  by mode:', cur.execute('SELECT mode, COUNT(*) FROM dispatcher_features GROUP BY mode ORDER BY 2 DESC').fetchall())
print('closed exits (sell%):', cur.execute("SELECT COUNT(*) FROM trades WHERE side LIKE 'sell%'").fetchone()[0])
print('buys:', cur.execute("SELECT COUNT(*) FROM trades WHERE side='buy'").fetchone()[0])
# trades/day rate over last 24h
import time
day = time.time() - 86400
print('exits last 24h:', cur.execute("SELECT COUNT(*) FROM trades WHERE side LIKE 'sell%' AND timestamp>=?", (day,)).fetchone()[0])
c.close()
