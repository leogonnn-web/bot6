import json, requests

url = "https://api.bybit.com/v5/market/tickers?category=spot"
r = requests.get(url, timeout=30).json()
if r.get("retCode") != 0:
    print("API error:", r)
    exit(1)

bybit_symbols = {item["symbol"] for item in r["result"]["list"]}
print("Bybit spot symbols:", len(bybit_symbols))

with open("/home/ubuntu/triada/shared/config.json") as f:
    cfg = json.load(f)

our_symbols = cfg.get("symbols", [])
invalid = []
for sym in our_symbols:
    bybit_fmt = sym.replace("/", "")
    if bybit_fmt not in bybit_symbols:
        invalid.append(sym)

print("Our symbols:", len(our_symbols))
print("INVALID count:", len(invalid))
for s in invalid:
    print("  ", s)
