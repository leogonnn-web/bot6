import json

with open('shared/config.json') as f:
    config = json.load(f)

symbols = [
  "BTC/USDT", "HYPE/USDT", "ETH/USDT", "H/USDT", "SOL/USDT",
  "XLM/USDT", "MNT/USDT", "XRP/USDT", "BNB/USDT", "NEAR/USDT",
  "ASTER/USDT", "LIT/USDT", "TON/USDT", "BILL/USDT", "ONDO/USDT",
  "SUI/USDT", "HOLO/USDT", "ADA/USDT", "BSB/USDT", "VVV/USDT",
  "PORTAL/USDT", "WLD/USDT", "MON/USDT", "IP/USDT", "ENA/USDT",
  "HBAR/USDT", "HOME/USDT", "DOGE/USDT", "ZORA/USDT", "FET/USDT",
  "LINK/USDT", "XPL/USDT", "LTC/USDT", "AAVE/USDT", "NIGHT/USDT",
  "AVAX/USDT", "SEI/USDT", "INJ/USDT", "TA/USDT", "ATH/USDT",
  "RENDER/USDT", "PENGU/USDT", "JTO/USDT", "TRX/USDT", "CC/USDT",
  "DOT/USDT", "ICP/USDT", "MEME/USDT", "BASED/USDT", "VIRTUAL/USDT",
  "FF/USDT", "GRASS/USDT", "DRIFT/USDT", "WLFI/USDT", "PEPE/USDT",
  "ICNT/USDT", "BBSOL/USDT", "HNT/USDT", "MEGA/USDT", "BCH/USDT",
  "MANTRA/USDT", "BARD/USDT", "OPG/USDT", "LA/USDT", "ALGO/USDT",
  "WIF/USDT", "PARTI/USDT", "APT/USDT", "PUMP/USDT", "IO/USDT",
  "ARB/USDT", "EDGE/USDT", "SKY/USDT", "FHE/USDT", "DYM/USDT",
  "TRUMP/USDT", "LUNC/USDT", "HYPER/USDT", "SIGN/USDT", "UNI/USDT",
  "ZAMA/USDT", "AERO/USDT", "SHIB/USDT", "APE/USDT", "JUP/USDT",
  "ID/USDT", "DEGEN/USDT", "NVDAX/USDT", "SAHARA/USDT", "POL/USDT",
  "APEX/USDT", "OP/USDT", "BLAST/USDT", "WAL/USDT", "STETH/USDT"
]

config['symbols'] = symbols

with open('shared/config.json', 'w') as f:
    json.dump(config, f, indent=2)

print(f"Updated symbols list: {len(symbols)} pairs")
