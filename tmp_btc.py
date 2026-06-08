import ccxt
ex = ccxt.bybit({"enableRateLimit": True})
try:
    o = ex.fetch_ohlcv("BTC/USDT", "1h", limit=2)
    ch = ((o[-1][4] - o[-2][1]) / o[-2][1]) * 100
    print("BTC 1h:", round(ch, 2), "%")
    if ch < -2.0: print("Zone: CRASH")
    elif ch < -0.8: print("Zone: BEARISH")
    elif ch <= 0.8: print("Zone: FLET")
    else: print("Zone: BULLISH")
except Exception as e:
    print("Error:", e)
