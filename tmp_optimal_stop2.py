import sqlite3
import statistics
from collections import defaultdict

conn = sqlite3.connect('/app/shared/state/trades.db')
c = conn.cursor()

# 1. Distribution of dump_depth in all dispatcher features
c.execute("SELECT dump_depth FROM dispatcher_features WHERE dump_depth IS NOT NULL")
drops = [r[0] for r in c.fetchall()]

print("=== DUMP_DEPTH DISTRIBUTION (all features) ===")
print(f"  count: {len(drops)}")
print(f"  mean:  {statistics.mean(drops):.2f}%")
print(f"  std:   {statistics.stdev(drops):.2f}%")
print(f"  min:   {min(drops):.2f}%")
print(f"  max:   {max(drops):.2f}%")
for p in [10, 25, 50, 75, 90, 95]:
    val = sorted(drops)[int(len(drops) * p / 100)]
    print(f"  p{p:02d}:   {val:.2f}%")

# 2. Get features for panic sells (loose matching by symbol)
c.execute("""
    SELECT t.symbol, t.profit, t.timestamp, t.side
    FROM trades t
    WHERE t.side = 'sell_panic'
    ORDER BY t.timestamp DESC
    LIMIT 50
""")
panic_trades = c.fetchall()

panic_drops = []
for sym, profit, ts, side in panic_trades:
    c.execute("""
        SELECT dump_depth FROM dispatcher_features
        WHERE symbol = ? ORDER BY ABS(timestamp - ?) LIMIT 1
    """, (sym, ts))
    row = c.fetchone()
    if row and row[0] is not None:
        panic_drops.append((sym, profit, row[0]))

# Get features for normal sells
c.execute("""
    SELECT t.symbol, t.profit, t.timestamp, t.side
    FROM trades t
    WHERE t.side = 'sell' AND t.profit > 0
    ORDER BY RANDOM() LIMIT 50
""")
normal_trades = c.fetchall()

normal_drops = []
for sym, profit, ts, side in normal_trades:
    c.execute("""
        SELECT dump_depth FROM dispatcher_features
        WHERE symbol = ? ORDER BY ABS(timestamp - ?) LIMIT 1
    """, (sym, ts))
    row = c.fetchone()
    if row and row[0] is not None:
        normal_drops.append((sym, profit, row[0]))

print(f"\n=== DUMP_DEPTH: PANIC vs NORMAL ===")
if panic_drops:
    pd_vals = [d[2] for d in panic_drops]
    print(f"Panic  drops: n={len(pd_vals)}  mean={statistics.mean(pd_vals):.2f}%  std={statistics.stdev(pd_vals):.2f}%  min={min(pd_vals):.2f}%  max={max(pd_vals):.2f}%")
else:
    print("Panic drops: no data")

if normal_drops:
    nd_vals = [d[2] for d in normal_drops]
    print(f"Normal drops: n={len(nd_vals)}  mean={statistics.mean(nd_vals):.2f}%  std={statistics.stdev(nd_vals):.2f}%  min={min(nd_vals):.2f}%  max={max(nd_vals):.2f}%")
else:
    print("Normal drops: no data")

# 3. Simulate different panic_stop values
print(f"\n=== PANIC_STOP SIMULATION ===")
if panic_drops:
    for stop in [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]:
        total_saved = 0
        hit_count = 0
        for sym, profit, drop in panic_drops:
            current_loss = abs(profit)
            # Linear: if current stop is 1.2%, new loss = current_loss * (new_stop / 1.2)
            new_loss = current_loss * (stop / 1.2)
            saved = current_loss - new_loss
            total_saved += saved
            hit_count += 1
        print(f"  panic_stop={stop:4.1f}%: est saved=${total_saved:6.2f}  trades={hit_count}")

# 4. Grid knee analysis
print(f"\n=== GRID KNEES (buy_grid_complete per symbol) ===")
c.execute("""
    SELECT symbol, COUNT(*) as cnt FROM trades WHERE side = 'buy_grid_complete' GROUP BY symbol HAVING cnt > 1 ORDER BY cnt DESC LIMIT 10
""")
for sym, cnt in c.fetchall():
    print(f"  {sym}: {cnt} grid completions")

c.execute("SELECT COUNT(DISTINCT symbol) FROM trades WHERE side = 'buy_grid_complete'")
uniq = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM trades WHERE side = 'buy_grid_complete'")
total = c.fetchone()[0]
print(f"  Total: {total} grid completions across {uniq} unique symbols")
print(f"  Avg knees per symbol: {total/max(uniq,1):.1f}")

# Also check how many buy entries per symbol (grid orders placed)
c.execute("""
    SELECT symbol, COUNT(*) FROM trades WHERE side = 'buy' GROUP BY symbol HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC LIMIT 10
""")
print(f"\n  Multi-buy symbols (grid orders placed):")
for sym, cnt in c.fetchall():
    print(f"    {sym}: {cnt} buy orders")

conn.close()
