#!/usr/bin/env python3
"""Safely apply go-live config changes to shared/config.json.

Usage:
  python golive_config.py <config_path>            # phase 1: sizing only (dry_run unchanged)
  python golive_config.py <config_path> --golive   # phase 2: set session_start_ts=now + dry_run=false

Always writes a timestamped .bak next to the file before modifying.
"""
import json
import os
import sys
import time
import shutil


def main():
    if len(sys.argv) < 2:
        print("usage: golive_config.py <config_path> [--golive]")
        sys.exit(2)
    path = sys.argv[1]
    golive = "--golive" in sys.argv[2:]

    bak = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, bak)

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    t = cfg["trading"]

    # Sizing + universe (idempotent, always enforced)
    t["slot_size"] = 6.0
    t["base_order_size_usdt"] = 6.0
    t["min_exchange_limit"] = 6.0
    t["max_trades_per_day"] = 30
    cfg.setdefault("hydra_net", {})["min_order_size_usdt"] = 5.0
    remove = {"H/USDT", "TON/USDT"}
    before = len(cfg.get("symbols", []))
    cfg["symbols"] = [s for s in cfg.get("symbols", []) if s not in remove]
    after = len(cfg["symbols"])
    print(
        f"SIZING: slot_size={t['slot_size']} base={t['base_order_size_usdt']} "
        f"min_exchange_limit={t['min_exchange_limit']} max_trades_per_day={t['max_trades_per_day']} "
        f"hydra_net.min_order_size_usdt={cfg['hydra_net']['min_order_size_usdt']} "
        f"symbols {before}->{after} (removed {before - after}: {sorted(remove)})"
    )

    if golive:
        now = int(time.time())
        t["session_start_ts"] = now
        t["dry_run"] = False
        print(f"GOLIVE: session_start_ts={now} dry_run={t['dry_run']}")
    else:
        print(f"PHASE1: dry_run={t.get('dry_run')} (unchanged)")

    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    print(f"OK backup={os.path.basename(bak)}")


if __name__ == "__main__":
    main()
