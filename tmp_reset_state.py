import json, os
path = '/data/hydra_state.json'
with open(path, 'r') as f:
    data = json.load(f)
data['state'] = 'IDLE'
data['session_profit'] = 0.0
data['state_data'] = {}
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
print('State reset OK:', data)
