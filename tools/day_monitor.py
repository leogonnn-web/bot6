#!/usr/bin/env python3
"""Hydra 24h Monitor — собирает статистику каждые 5 мин для дневного анализа."""
import json, subprocess, time, os, sys
LOG = "/home/ubuntu/triada/logs/hydra_day_stats.jsonl"

def collect():
    logs = subprocess.check_output("cd ~/triada && sudo docker compose logs --tail 2000 hydra-bot 2>&1", shell=True, text=True)
    stats = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "approved": logs.count("SIGNAL_APPROVED"),
        "grid_complete": logs.count("GRID_COMPLETE"),
        "tp_hit": logs.count("Virtual TP hit"),
        "sl_hit": logs.count("Virtual SL hit"),
        "breakeven": logs.count("BREAKEVEN_TIMEOUT"),
        "partial_tp": logs.count("Partial TP hit"),
        "panic": logs.count("PANIC"),
    }
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(stats) + "\n")

if __name__ == "__main__":
    while True:
        collect()
        time.sleep(300)
