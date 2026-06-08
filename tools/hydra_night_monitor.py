#!/usr/bin/env python3
"""Hydra Night Monitor — собирает статистику каждые 5 мин для утреннего анализа."""
import json, subprocess, time, os
LOG = "/home/ubuntu/triada/logs/hydra_night_stats.jsonl"

def run_cmd(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
    except:
        return ""

def collect():
    logs = run_cmd("cd ~/triada && sudo docker compose logs --tail 1000 hydra-bot 2>&1")
    stats = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "approved": logs.count("SIGNAL_APPROVED"),
        "grid_active": logs.count("GRID_ACTIVE"),
        "grid_complete": logs.count("GRID_COMPLETE"),
        "tp_hit": logs.count("TP_HIT") + logs.count("DRY_RUN_TP"),
        "sl_hit": logs.count("SL_HIT") + logs.count("TRAILING_STOP"),
        "breakeven": logs.count("BREAKEVEN"),
        "rejected": logs.count("SIGNAL_REJECT"),
        "analyzer_skip": logs.count("rec=SKIP"),
        "analyzer_buy": logs.count("rec=BUY") + logs.count("rec=STRONG_BUY"),
    }
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(stats) + "\n")

if __name__ == "__main__":
    while True:
        collect()
        time.sleep(300)  # 5 мин
