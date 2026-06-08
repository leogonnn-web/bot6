import sqlite3
conn = sqlite3.connect('/app/shared/state/trades.db')
c = conn.cursor()
c.execute('''
    CREATE TABLE IF NOT EXISTS dispatcher_features (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER,
        timestamp REAL,
        symbol TEXT,
        confidence REAL,
        rvol_spike REAL,
        rvol_local REAL,
        dump_depth REAL,
        obi_skew REAL,
        btc_1h REAL,
        score REAL,
        mode TEXT
    )
''')
conn.commit()
conn.close()
print('Table dispatcher_features created or already exists')
