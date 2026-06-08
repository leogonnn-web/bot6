import sqlite3
conn = sqlite3.connect('/app/shared/state/trades.db')
cur = conn.cursor()
cur.execute('PRAGMA table_info(trades)')
for c in cur.fetchall():
    print(c[1], c[2])
conn.close()
