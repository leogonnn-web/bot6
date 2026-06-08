import json

with open('shared/config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

existing = set(config.get('symbols', []))

new_coins = [
    "LTC/USDT", "LINK/USDT", "MATIC/USDT", "AVAX/USDT", "FTM/USDT",
    "SUI/USDT", "SEI/USDT", "INJ/USDT", "TIA/USDT", "SATS/USDT",
    "ORDI/USDT", "SAND/USDT", "MANA/USDT", "AXS/USDT", "GALA/USDT",
    "CHZ/USDT", "ENJ/USDT", "BAT/USDT", "COMP/USDT", "AAVE/USDT",
    "MKR/USDT", "YFI/USDT", "SNX/USDT", "KNC/USDT", "ZRX/USDT",
    "LRC/USDT", "IMX/USDT", "FLOW/USDT", "MINA/USDT", "ROSE/USDT",
    "KAVA/USDT", "COTI/USDT", "STX/USDT", "EGLD/USDT", "ONE/USDT",
    "CSPR/USDT", "QNT/USDT", "GRT/USDT", "THETA/USDT", "FIL/USDT",
    "XTZ/USDT", "EOS/USDT", "IOTA/USDT", "NEO/USDT", "VET/USDT",
    "ONT/USDT", "QTUM/USDT", "ZEC/USDT", "DASH/USDT", "ANKR/USDT",
]

added = 0
for coin in new_coins:
    if coin not in existing:
        config['symbols'].append(coin)
        existing.add(coin)
        added += 1

print(f"Added {added} new coins. Total symbols: {len(config['symbols'])}")

with open('shared/config.json', 'w', encoding='utf-8') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)

print("Saved to shared/config.json")
