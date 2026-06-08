"""Offline batch calibration for HYDRA Dispatcher weights.

Reads dispatcher_features rows with profit NOT NULL from trades.db,
replays Widrow-Hoff updates in batch order, and prints suggested
starting weights. Can also write them back to a JSON file.

Usage:
    python tools/calibrate_dispatcher.py --db /path/to/trades.db --out weights.json
"""
import argparse
import json
import sqlite3
import importlib.util
import os

# Load dispatcher.py directly to avoid triggering src/core/__init__.py,
# which imports the full bot/ccxt stack (not needed for offline calibration).
_DISPATCHER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'src', 'core', 'dispatcher.py')
)
_spec = importlib.util.spec_from_file_location('hydra_dispatcher', _DISPATCHER_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
HydraDispatcher = _mod.HydraDispatcher


def load_outcomes(db_path: str):
    """Fetch rows from dispatcher_features where profit is known (trade closed)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT symbol, confidence, rvol_spike, dump_depth, obi_skew, btc_1h,
               score, mode, profit, take_profit_pct
        FROM dispatcher_features
        WHERE profit IS NOT NULL
        ORDER BY timestamp ASC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def calibrate(rows, learning_rate=0.02, epochs=1):
    """Replay weight updates across the dataset."""
    d = HydraDispatcher()
    # Warm-up: replay updates in chronological order
    for _ in range(epochs):
        for r in rows:
            if not r['take_profit_pct']:
                continue
            feat = {
                'confidence': r['confidence'],
                'rvol_spike': r['rvol_spike'],
                'dump_depth': r['dump_depth'],
                'obi_skew': r['obi_skew'],
                'btc_ok': 1.0 if r['btc_1h'] > -1.0 else (0.5 if r['btc_1h'] > -2.0 else 0.0),
            }
            d.update_weights(feat, r['profit'], r['take_profit_pct'], learning_rate)
    return d.weights


def main():
    parser = argparse.ArgumentParser(description='Calibrate dispatcher weights from historical outcomes')
    parser.add_argument('--db', required=True, help='Path to trades.db')
    parser.add_argument('--lr', type=float, default=0.02, help='Learning rate')
    parser.add_argument('--epochs', type=int, default=1, help='Passes over data')
    parser.add_argument('--out', help='Write resulting weights to JSON file')
    args = parser.parse_args()

    rows = load_outcomes(args.db)
    print(f"Loaded {len(rows)} closed outcomes from {args.db}")
    if not rows:
        print("No data. Run the bot with feedback loop logging enabled until some trades close.")
        return

    weights = calibrate(rows, args.lr, args.epochs)
    print("\nSuggested weights:")
    for k, v in weights.items():
        print(f"  {k:12s}: {v:.4f}")

    if args.out:
        with open(args.out, 'w') as f:
            json.dump(weights, f, indent=2)
        print(f"\nWritten to {args.out}")


if __name__ == '__main__':
    main()
