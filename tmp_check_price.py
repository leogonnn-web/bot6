import json
with open('shared/state/hydra_state.json') as f:
    state = json.load(f)
symbol = state['state_data']['symbol']
buy = state['state_data']['buy_price']
target = state['state_data']['target_sell_price']
entry = state['state_data']['entry_price']

# Try to read ws_tickers_cache from logs or estimate
print(f"Symbol: {symbol}")
print(f"Entry: {entry}")
print(f"Buy (avg): {buy}")
print(f"Target TP: {target}")
print(f"Target %: {((target/buy)-1)*100:.2f}%")
print(f"Breakeven price: {entry}")
print(f"Current breakeven flag: {state['state_data']['is_breakeven']}")
