import sqlite3
conn = sqlite3.connect('/app/shared/state/trades.db')
cur = conn.cursor()
cur.execute("PRAGMA table_info(dispatcher_features)")
for c in cur.fetchall():
    print(c)
conn.close()
