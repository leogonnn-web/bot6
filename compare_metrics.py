#!/usr/bin/env python3
"""Compare local and server metrics every minute."""
import requests
import time
from datetime import datetime

LOCAL_METRICS = "http://localhost:9090/metrics"
SERVER_METRICS = "http://54.179.1.197:9090/metrics"

METRICS_TO_COMPARE = [
    "hydra_scan_cycles_total",
    "hydra_balance_usdt",
    "hydra_bot_state",
    "hydra_active_positions",
    "hydra_session_profit_usdt",
    "hydra_capital_mode_info",
    "hydra_grid_max_levels",
]

def parse_metrics(metrics_url):
    """Parse metrics from /metrics endpoint."""
    try:
        response = requests.get(metrics_url, timeout=5)
        response.raise_for_status()
        metrics = {}
        for line in response.text.split('\n'):
            if line.startswith('#') or not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                metric_name = parts[0]
                metric_value = parts[1]
                try:
                    metrics[metric_name] = float(metric_value)
                except ValueError:
                    pass
        return metrics
    except Exception as e:
        return {}

def compare_metrics():
    """Compare metrics between local and server."""
    print(f"\n{'='*60}")
    print(f"Metrics Comparison - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    local_metrics = parse_metrics(LOCAL_METRICS)
    server_metrics = parse_metrics(SERVER_METRICS)
    
    discrepancies = []
    
    for metric in METRICS_TO_COMPARE:
        local_value = local_metrics.get(metric)
        server_value = server_metrics.get(metric)
        
        if local_value is None and server_value is None:
            continue
        
        local_str = f"{local_value:.2f}" if local_value is not None else "N/A"
        server_str = f"{server_value:.2f}" if server_value is not None else "N/A"
        
        if local_value != server_value:
            discrepancies.append(metric)
            print(f"[!] {metric:40s} | Local: {local_str:10s} | Server: {server_str:10s}")
        else:
            print(f"[+] {metric:40s} | Local: {local_str:10s} | Server: {server_str:10s}")
    
    if discrepancies:
        print(f"\n[!] Discrepancies found: {len(discrepancies)}")
    else:
        print(f"\n[+] All metrics match")
    
    return len(discrepancies) == 0

if __name__ == "__main__":
    print("Starting metrics comparison (Ctrl+C to stop)...")
    
    try:
        while True:
            compare_metrics()
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nStopped.")
