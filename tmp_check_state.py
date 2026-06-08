import json
with open('shared/state/hydra_state.json') as f:
    d = json.load(f)
print(f"State: {d['state']}")
print(f"Symbol: {d['state_data']['symbol']}")
print(f"Buy: {d['state_data']['buy_price']}")
print(f"Target: {d['state_data']['target_sell_price']}")
print(f"Breakeven: {d['state_data']['is_breakeven']}")
print(f"Grid active: {d['state_data']['is_grid_active']}")
print(f"Session profit: {d.get('session_profit', 0)}")
