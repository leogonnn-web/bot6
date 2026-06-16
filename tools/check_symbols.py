#!/usr/bin/env python3
"""Validate config symbols against live Bybit spot markets.

Prints which configured symbols do NOT exist as Bybit spot markets so they
can be removed before going live (one invalid symbol breaks fetch_tickers).
Read-only.
"""
import json
import os
import sys


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/app/shared/config.json"
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    symbols = cfg.get("symbols", [])

    import ccxt
    ex = ccxt.bybit({
        "apiKey": os.getenv("BYBIT_API_KEY", ""),
        "secret": os.getenv("BYBIT_API_SECRET", ""),
        "enableRateLimit": True,
        "options": {"version": "v5", "defaultType": "spot"},
    })
    markets = ex.load_markets()
    spot = {s for s, m in markets.items() if m.get("spot")}

    missing = [s for s in symbols if s not in markets]
    not_spot = [s for s in symbols if s in markets and s not in spot]
    print(f"config_symbols={len(symbols)} bybit_markets={len(markets)} bybit_spot={len(spot)}")
    print(f"MISSING ({len(missing)}): {missing}")
    print(f"NOT_SPOT ({len(not_spot)}): {not_spot}")
    bad = sorted(set(missing) | set(not_spot))
    print(f"REMOVE ({len(bad)}): {bad}")


if __name__ == "__main__":
    main()
