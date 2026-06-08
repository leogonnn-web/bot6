import sqlite3
conn = sqlite3.connect('/app/shared/state/trades.db')
c = conn.cursor()
c.execute("PRAGMA table_info(trades)")
for col in c.fetchall():
    print(f"  {col[1]} ({col[2]})")
conn.close()
