#!/usr/bin/env python3
"""Read-only validation of Bybit API keys from environment.

Fetches account balance via ccxt (NO orders placed). Prints free USDT and
basic permission/auth status so we can confirm keys work before going live.
"""
import os
import sys


def main():
    key = os.getenv("BYBIT_API_KEY", "")
    sec = os.getenv("BYBIT_API_SECRET", "")
    print(f"key_set={bool(key)} ({len(key)} chars), secret_set={bool(sec)} ({len(sec)} chars)")
    if not key or not sec:
        print("FAIL: keys not present in environment")
        sys.exit(1)

    import ccxt
    ex = ccxt.bybit({
        "apiKey": key,
        "secret": sec,
        "enableRateLimit": True,
        "options": {"version": "v5", "defaultType": "spot"},
    })
    try:
        bal = ex.fetch_balance()
    except Exception as e:
        print(f"FAIL auth/balance: {type(e).__name__}: {e}")
        sys.exit(1)

    free = bal.get("free", {}) if isinstance(bal, dict) else {}
    total = bal.get("total", {}) if isinstance(bal, dict) else {}
    usdt_free = free.get("USDT", 0)
    usdt_total = total.get("USDT", 0)
    print(f"AUTH OK. USDT free={usdt_free} total={usdt_total}")
    nonzero = {k: v for k, v in total.items() if v and v > 0}
    print(f"non-zero balances: {nonzero}")


if __name__ == "__main__":
    main()
