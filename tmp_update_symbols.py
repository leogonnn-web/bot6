import json

with open('/tmp/top100.json', 'r') as f:
    top100 = json.load(f)

with open('/home/ubuntu/triada/shared/config.json', 'r') as f:
    config = json.load(f)

# Filter out obvious stablecoins and suspicious tickers
stablecoins = {'USDC/USDT', 'USDE/USDT', 'RLUSD/USDT', 'XAUT/USDT', 'STETH/USDT'}
filtered = [s for s in top100 if s not in stablecoins]

config['symbols'] = filtered

with open('/home/ubuntu/triada/shared/config.json', 'w') as f:
    json.dump(config, f, indent=2)

print(f'Updated symbols: {len(filtered)} pairs')
print(f'First 20: {filtered[:20]}')
