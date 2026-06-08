import sqlite3
conn = sqlite3.connect('/app/shared/state/trades.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dispatcher_features'")
tables = cur.fetchall()
print('Tables:', tables)
if tables:
    cur.execute('SELECT COUNT(*) FROM dispatcher_features')
    count = cur.fetchone()[0]
    print('Records:', count)
    if count > 0:
        cur.execute('SELECT symbol, score, mode, timestamp FROM dispatcher_features ORDER BY timestamp DESC LIMIT 3')
        for row in cur.fetchall():
            print(' ', row)
conn.close()
