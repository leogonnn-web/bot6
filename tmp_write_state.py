import json, time

state = {
    "state": "IN_POSITION",
    "state_data": {
        "symbol": "TEST/USDT",
        "buy_price": 1.0,
        "amount": 100,
        "buy_time": time.time(),
        "is_dry_run": True,
        "target_sell_price": 1.01,
        "is_breakeven": True,
        "partial_tp_hit": True,
        "trailing_high": 1.02,
        "entry_price": 1.0,
        "current_level": 3,
        "total_cost": 100,
        "total_qty": 100,
        "order_id": "virtual_test_12345",
        "is_grid_active": False
    },
    "state_entry_time": time.time(),
    "session_profit": 50.0,
    "saved_at": time.time()
}

with open('/data/hydra_state.json', 'w') as f:
    json.dump(state, f, indent=2)

print("Test state written")
