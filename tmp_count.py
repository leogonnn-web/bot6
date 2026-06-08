import sqlite3
conn = sqlite3.connect('/app/shared/state/trades.db')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM dispatcher_features')
count = cur.fetchone()[0]
print('Dispatcher records:', count)
conn.close()
