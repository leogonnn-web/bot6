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

# 2. Compare drops that led to panic vs normal sell
# Match trades with features
c.execute("""
    SELECT t.symbol, t.profit, t.timestamp, t.side
    FROM trades t
    WHERE t.side LIKE 'sell%'
    ORDER BY t.timestamp DESC
    LIMIT 300
""")
trades = c.fetchall()

panic_drops = []
normal_drops = []
for sym, profit, ts, side in trades:
    c.execute("""
        SELECT dump_depth FROM dispatcher_features
        WHERE symbol = ? AND ABS(timestamp - ?) < 120
        ORDER BY ABS(timestamp - ?) LIMIT 1
    """, (sym, ts, ts))
    row = c.fetchone()
    if row and row[0] is not None:
        if side == 'sell_panic':
            panic_drops.append(row[0])
        else:
            normal_drops.append(row[0])

print(f"\n=== DUMP_DEPTH: PANIC vs NORMAL ===")
print(f"Panic  drops: n={len(panic_drops)}  mean={statistics.mean(panic_drops):.2f}%  std={statistics.stdev(panic_drops):.2f}%")
print(f"Normal drops: n={len(normal_drops)}  mean={statistics.mean(normal_drops):.2f}%  std={statistics.stdev(normal_drops):.2f}%")

# 3. Simulate different panic_stop values
# We know actual profit for each trade. Let's see what panic_stop would have saved losses.
print(f"\n=== PANIC_STOP SIMULATION ===")
c.execute("""
    SELECT t.symbol, t.profit, t.side, f.dump_depth
    FROM trades t
    JOIN dispatcher_features f ON t.symbol = f.symbol AND ABS(t.timestamp - f.timestamp) < 120
    WHERE t.side LIKE 'sell%' AND t.profit < 0 AND t.side = 'sell_panic'
    ORDER BY t.timestamp DESC
    LIMIT 50
""")
panic_rows = c.fetchall()

print(f"Analyzing {len(panic_rows)} panic sells with matched features...")

# For each panic, the drop at entry was `dump_depth`. The actual loss is `profit`.
# A tighter stop would close earlier but might hit more often.
# We don't have intra-trade drawdown data, so we estimate:
# If panic_stop = X%, and entry drop = D%, the position likely went to -(X+D)% before panic.
for stop in [0.5, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]:
    saved = 0
    extra_losses = 0
    for sym, profit, side, drop in panic_rows:
        # Current panic_stop=1.2 means we exit when price drops another 1.2% below entry.
        # Entry was at price after `drop`% dump.
        # If we set stop to X%, we exit earlier → smaller loss per trade.
        # Approx: loss scales linearly with stop size
        current_loss = abs(profit)
        ratio = stop / 1.2  # linear approximation
        new_loss = current_loss * ratio
        saved += current_loss - new_loss
    print(f"  panic_stop={stop}%: est saved=${saved:.2f} across {len(panic_rows)} panics")

# 4. Grid knee analysis — check how many buy_grid_complete per symbol
c.execute("""
    SELECT symbol, COUNT(*) as cnt FROM trades WHERE side = 'buy_grid_complete' GROUP BY symbol HAVING cnt > 1 ORDER BY cnt DESC LIMIT 10
""")
print(f"\n=== GRID KNEES (buy_grid_complete per symbol) ===")
for sym, cnt in c.fetchall():
    print(f"  {sym}: {cnt} grid completions")

# Total unique symbols with buy_grid_complete
c.execute("SELECT COUNT(DISTINCT symbol) FROM trades WHERE side = 'buy_grid_complete'")
uniq = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM trades WHERE side = 'buy_grid_complete'")
total = c.fetchone()[0]
print(f"  Total: {total} grid completions across {uniq} unique symbols")
print(f"  Avg knees per symbol: {total/max(uniq,1):.1f}")

conn.close()
