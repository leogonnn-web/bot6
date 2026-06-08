import ccxt
exchange = ccxt.bybit({'enableRateLimit': True})
ticker = exchange.fetch_ticker('HBAR/USDT')
print(f"HBAR/USDT last: {ticker['last']}")
print(f"HBAR/USDT bid: {ticker['bid']}")
print(f"HBAR/USDT ask: {ticker['ask']}")
