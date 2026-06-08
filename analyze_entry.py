import sqlite3
import statistics
from collections import defaultdict

conn = sqlite3.connect('/app/shared/state/trades.db')
c = conn.cursor()

N = 15  # number of negative and positive samples

# Get last N panic sells (negative)
c.execute("""
    SELECT t.symbol, t.profit, t.timestamp, t.id
    FROM trades t
    WHERE t.side LIKE 'sell%' AND t.profit < 0
    ORDER BY t.timestamp DESC LIMIT ?
""", (N,))
neg_trades = c.fetchall()

# Get N random positive sells
c.execute("""
    SELECT t.symbol, t.profit, t.timestamp, t.id
    FROM trades t
    WHERE t.side LIKE 'sell%' AND t.profit > 0
    ORDER BY RANDOM() LIMIT ?
""", (N,))
pos_trades = c.fetchall()

print(f"Samples: {len(neg_trades)} negative, {len(pos_trades)} positive")
print("=" * 80)

def fetch_features(symbol, timestamp):
    """Find dispatcher features for a trade by symbol and nearest timestamp."""
    c.execute("""
        SELECT confidence, rvol_spike, rvol_local, dump_depth, obi_skew, btc_1h, score, mode
        FROM dispatcher_features
        WHERE symbol = ? AND ABS(timestamp - ?) < 120
        ORDER BY ABS(timestamp - ?) LIMIT 1
    """, (symbol, timestamp, timestamp))
    return c.fetchone()

def analyze_set(trades, label):
    rows = []
    for sym, profit, ts, tid in trades:
        f = fetch_features(sym, ts)
        if f:
            rows.append({
                'symbol': sym,
                'profit': profit,
                'confidence': f[0],
                'rvol_spike': f[1],
                'rvol_local': f[2],
                'dump_depth': f[3],
                'obi_skew': f[4],
                'btc_1h': f[5],
                'score': f[6],
                'mode': f[7],
            })
        else:
            # fallback: try any feature for symbol
            c.execute("""
                SELECT confidence, rvol_spike, rvol_local, dump_depth, obi_skew, btc_1h, score, mode
                FROM dispatcher_features WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1
            """, (sym,))
            f = c.fetchone()
            if f:
                rows.append({
                    'symbol': sym,
                    'profit': profit,
                    'confidence': f[0],
                    'rvol_spike': f[1],
                    'rvol_local': f[2],
                    'dump_depth': f[3],
                    'obi_skew': f[4],
                    'btc_1h': f[5],
                    'score': f[6],
                    'mode': f[7],
                })
    
    print(f"\n{label} — matched {len(rows)}/{len(trades)} with features")
    if not rows:
        return
    
    for r in rows:
        print(f"  {r['symbol']:12s} profit={r['profit']:+7.3f}  score={r['score']:.2f}  conf={r['confidence']:.1f}%  rvol={r['rvol_spike']:.2f}x  drop={r['dump_depth']:.2f}%  btc={r['btc_1h']:+.2f}%  mode={r['mode']}")
    
    def stats(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return vals, statistics.mean(vals) if vals else 0, statistics.stdev(vals) if len(vals) > 1 else 0, min(vals) if vals else 0, max(vals) if vals else 0
    
    print(f"\n{label} STATS:")
    for key in ['score', 'confidence', 'rvol_spike', 'rvol_local', 'dump_depth', 'obi_skew', 'btc_1h']:
        vals, mean, stdev, mn, mx = stats(key)
        print(f"  {key:14s} mean={mean:7.2f}  std={stdev:6.2f}  min={mn:7.2f}  max={mx:7.2f}  n={len(vals)}")
    
    modes = defaultdict(int)
    for r in rows:
        modes[r['mode']] += 1
    print(f"  mode dist: {dict(modes)}")
    return rows

neg = analyze_set(neg_trades, "NEGATIVE (panic losses)")
pos = analyze_set(pos_trades, "POSITIVE (normal sells)")

if neg and pos:
    print("\n" + "=" * 80)
    print("DELTA (NEGATIVE minus POSITIVE means):")
    keys = ['score', 'confidence', 'rvol_spike', 'rvol_local', 'dump_depth', 'obi_skew', 'btc_1h']
    for key in keys:
        nvals = [r[key] for r in neg if r[key] is not None]
        pvals = [r[key] for r in pos if r[key] is not None]
        if nvals and pvals:
            delta = statistics.mean(nvals) - statistics.mean(pvals)
            print(f"  {key:14s} delta={delta:+7.2f}")

    print("\nANOMALY DETECTION:")
    # Check if negative trades cluster at low confidence or low drop
    neg_conf = [r['confidence'] for r in neg if r['confidence'] is not None]
    pos_conf = [r['confidence'] for r in pos if r['confidence'] is not None]
    if neg_conf and pos_conf:
        low_conf_neg = sum(1 for c in neg_conf if c < 40) / len(neg_conf)
        low_conf_pos = sum(1 for c in pos_conf if c < 40) / len(pos_conf)
        print(f"  Low confidence (<40%):  neg={low_conf_neg:.0%}  pos={low_conf_pos:.0%}")
    
    neg_drop = [r['dump_depth'] for r in neg if r['dump_depth'] is not None]
    pos_drop = [r['dump_depth'] for r in pos if r['dump_depth'] is not None]
    if neg_drop and pos_drop:
        shallow_neg = sum(1 for d in neg_drop if d < 1.0) / len(neg_drop)
        shallow_pos = sum(1 for d in pos_drop if d < 1.0) / len(pos_drop)
        print(f"  Shallow drop (<1%):     neg={shallow_neg:.0%}  pos={shallow_pos:.0%}")
    
    neg_btc = [r['btc_1h'] for r in neg if r['btc_1h'] is not None]
    pos_btc = [r['btc_1h'] for r in pos if r['btc_1h'] is not None]
    if neg_btc and pos_btc:
        bad_btc_neg = sum(1 for b in neg_btc if b < -0.5) / len(neg_btc)
        bad_btc_pos = sum(1 for b in pos_btc if b < -0.5) / len(pos_btc)
        print(f"  BTC dumping (<-0.5%):   neg={bad_btc_neg:.0%}  pos={bad_btc_pos:.0%}")

conn.close()
