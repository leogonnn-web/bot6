import sqlite3, time

c = sqlite3.connect("/var/lib/docker/volumes/triada_shared-data/_data/trades.db")
cur = c.cursor()

print("=== DISPATCHER DATA COLLECTION STATUS ===")

total = cur.execute("SELECT COUNT(*) FROM dispatcher_features").fetchone()[0]
linked = cur.execute("SELECT COUNT(*) FROM dispatcher_features WHERE trade_id > 0").fetchone()[0]
with_profit = cur.execute("SELECT COUNT(*) FROM dispatcher_features WHERE profit IS NOT NULL").fetchone()[0]
print("Total features logged:", total)
print("Linked to trades:     ", linked)
print("With profit recorded: ", with_profit)

print("\n--- By Mode ---")
modes = cur.execute("SELECT mode, COUNT(*), ROUND(AVG(profit),4), MIN(profit), MAX(profit) FROM dispatcher_features WHERE profit IS NOT NULL GROUP BY mode").fetchall()
for m in modes:
    print("  {:12s}: {:3d} samples  avg={:+.4f}  min={:+.4f}  max={:+.4f}".format(m[0], m[1], m[2] or 0, m[3] or 0, m[4] or 0))

print("\n--- Profit Distribution ---")
profits = [p[0] for p in cur.execute("SELECT profit FROM dispatcher_features WHERE profit IS NOT NULL").fetchall()]
wins = sum(1 for p in profits if p > 0)
losses = sum(1 for p in profits if p <= 0)
print("  Wins:  ", wins, "({:.1f}%)".format(wins/len(profits)*100))
print("  Losses:", losses, "({:.1f}%)".format(losses/len(profits)*100))

print("\n--- Last 5 outcomes ---")
rows = cur.execute("SELECT symbol, mode, ROUND(score,2), ROUND(profit,4), datetime(timestamp,'unixepoch') FROM dispatcher_features WHERE profit IS NOT NULL ORDER BY timestamp DESC LIMIT 5").fetchall()
for r in rows:
    print("  ", r[4], " ", r[0], " mode=", r[1], " score=", r[2], " profit=", r[3])

print("\n--- Calibration Readiness ---")
print("Recommended minimum: 50 samples per mode")
for m in modes:
    ready = "READY" if m[1] >= 50 else "need " + str(50-m[1]) + " more"
    print("  {:12s}: {:3d} samples  -> {}".format(m[0], m[1], ready))

c.close()
