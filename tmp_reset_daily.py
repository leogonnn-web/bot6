import sqlite3, time
conn = sqlite3.connect('/app/shared/state/trades.db')
c = conn.cursor()
c.execute('UPDATE trades SET timestamp = timestamp - 86400 WHERE timestamp >= ?', (int(time.time()) - 86400,))
print(f'Reset {c.rowcount} trades to previous day')
conn.commit()
conn.close()
