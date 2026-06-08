import sqlite3, time

# Test insert directly
conn = sqlite3.connect('/app/shared/state/trades.db')
cur = conn.cursor()
cur.execute(
    '''INSERT INTO dispatcher_features
       (trade_id, timestamp, symbol, confidence, rvol_spike,
        rvol_local, dump_depth, obi_skew, btc_1h, score, mode)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
    (0, time.time(), 'TEST/USDT', 50.0, 1.5, 1.5, 0.5, 0.0, 0.0, 1.5, 'normal')
)
conn.commit()
conn.close()
print('Insert OK')

# Verify
conn = sqlite3.connect('/app/shared/state/trades.db')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM dispatcher_features')
count = cur.fetchone()[0]
print(f'Total records: {count}')
conn.close()
