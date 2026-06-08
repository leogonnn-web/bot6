import sqlite3
conn=sqlite3.connect('/app/shared/state/trades.db')
c=conn.cursor()

# Get last 10 panic sell symbols and their dispatcher features
symbols = ['XLM/USDT', 'PARTI/USDT', 'FF/USDT', 'VVV/USDT', 'VIRTUAL/USDT', 
           'EDGE/USDT', 'SKY/USDT', 'ICNT/USDT', 'BSB/USDT', 'ATH/USDT']

print("Dispatcher features for panic sells:")
for sym in symbols:
    c.execute("SELECT symbol, score, confidence, rvol_spike, dump_depth, mode, timestamp FROM dispatcher_features WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1", (sym,))
    row = c.fetchone()
    if row:
        print(f"  {row[0]}: score={row[1]:.2f} conf={row[2]:.1f}% rvol={row[3]:.2f}x drop={row[4]:.2f}% mode={row[5]}")
    else:
        print(f"  {sym}: no dispatcher features")

# Compare with all features average
print("\nDispatcher features avg (all):")
c.execute("SELECT AVG(score), AVG(confidence), AVG(rvol_spike), AVG(dump_depth) FROM dispatcher_features")
row = c.fetchone()
print(f"  avg_score={row[0]:.2f} avg_conf={row[1]:.1f}% avg_rvol={row[2]:.2f}x avg_drop={row[3]:.2f}%")

conn.close()
