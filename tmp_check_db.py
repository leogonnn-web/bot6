import sqlite3
import os

db_path = r'C:\Users\leogo\Desktop\bot4-main\bot4-main\shared\state\trades.db'
if not os.path.exists(db_path):
    print('DB does not exist at', db_path)
else:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print('Tables:', [t[0] for t in c.fetchall()])
    try:
        c.execute("SELECT COUNT(*) FROM dispatcher_features WHERE mode='conservative'")
        print('Conservative samples:', c.fetchone()[0])
        c.execute("SELECT COUNT(*) FROM dispatcher_features")
        print('Total samples:', c.fetchone()[0])
        c.execute("SELECT MAX(timestamp) FROM dispatcher_features")
        print('Latest timestamp:', c.fetchone()[0])
    except Exception as e:
        print('Error querying dispatcher_features:', e)
    conn.close()
