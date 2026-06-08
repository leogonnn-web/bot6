import ccxt, json

exchange = ccxt.bybit({'enableRateLimit': True})
exchange.load_markets()

usdt_pairs = []
for s in exchange.symbols:
    m = exchange.markets.get(s, {})
    if s.endswith('/USDT') and m.get('active') and m.get('spot'):
        usdt_pairs.append(s)

tickers = exchange.fetch_tickers(usdt_pairs)

volumes = []
for symbol, ticker in tickers.items():
    vol = ticker.get('quoteVolume', 0)
    if vol and vol > 0:
        volumes.append((symbol, vol))

volumes.sort(key=lambda x: x[1], reverse=True)
top100 = [s for s, v in volumes[:100]]

with open('/tmp/top100_symbols.json', 'w') as f:
    json.dump(top100, f, indent=2)

print(f'Top 100 USDT pairs saved. First 20: {top100[:20]}')
