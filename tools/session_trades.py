#!/usr/bin/env python3
"""List trades since a given epoch from the bot's SQLite DB (read-only)."""
import sqlite3
import sys

db = sys.argv[1] if len(sys.argv) > 1 else "/app/shared/state/trades.db"
since = int(sys.argv[2]) if len(sys.argv) > 2 else 0

c = sqlite3.connect(db)
cur = c.cursor()
cur.execute(
    "SELECT datetime(timestamp,'unixepoch'), side, symbol, amount, price "
    "FROM trades WHERE timestamp >= ? ORDER BY timestamp",
    (since,),
)
rows = cur.fetchall()
print(f"trades since {since}: {len(rows)}")
for ts, side, sym, amt, price in rows:
    notional = (amt or 0) * (price or 0)
    print(f"{ts}  {side:18s} {sym:12s} amt={amt} px={price} notional=${notional:.2f}")
c.close()
