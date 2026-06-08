import sqlite3
c = sqlite3.connect('/var/lib/docker/volumes/triada_shared-data/_data/trades.db')
cur = c.cursor()
print('linked trade_id>0:', cur.execute('SELECT COUNT(*) FROM dispatcher_features WHERE trade_id>0').fetchone()[0])
print('profit not null:', cur.execute('SELECT COUNT(*) FROM dispatcher_features WHERE profit IS NOT NULL').fetchone()[0])
print('last 5 outcome rows:', cur.execute('SELECT mode,profit FROM dispatcher_features WHERE profit IS NOT NULL ORDER BY timestamp DESC LIMIT 5').fetchall())
c.close()
