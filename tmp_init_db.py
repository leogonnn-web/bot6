import sqlite3, os
DB_PATH = '/app/shared/state/trades.db'
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, side TEXT, amount REAL, price REAL,
    timestamp REAL, confidence REAL
)''')
c.execute('''CREATE TABLE IF NOT EXISTS dispatcher_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER, timestamp REAL, symbol TEXT,
    confidence REAL, rvol_spike REAL, rvol_local REAL,
    dump_depth REAL, obi_skew REAL, btc_1h REAL,
    score REAL, mode TEXT
)''')
conn.commit()
conn.close()
print('DB initialized')
