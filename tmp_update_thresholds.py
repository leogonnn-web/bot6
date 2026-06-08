import json

with open('/home/ubuntu/triada/shared/config.json', 'r') as f:
    config = json.load(f)

config['trading']['drop_threshold'] = 0.65
config['trading']['min_confidence_threshold'] = 40.0
config['trading']['min_rvol_threshold'] = 1.7

with open('/home/ubuntu/triada/shared/config.json', 'w') as f:
    json.dump(config, f, indent=2)

print('Updated thresholds:')
print(f'  drop_threshold: {config[\"trading\"][\"drop_threshold\"]}%')
print(f'  min_confidence: {config[\"trading\"][\"min_confidence_threshold\"]}%')
print(f'  min_rvol: {config[\"trading\"][\"min_rvol_threshold\"]}x')
