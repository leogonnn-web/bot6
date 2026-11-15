"""Which scan-time features predict the price after a dump signal? (B-35 follow-up)

Read-only. Every scan-time row of `dispatcher_features` (passed AND rejected
candidates, TZ-13A) is labelled with what the price did next, from 1m klines.

  fetch  inside the bot container (ccxt, trades.db): CSV on stdout, one row per candidate
  run    anywhere, offline: tables by filter, by feature bucket, simple rules

    docker compose run --rm --no-deps -T hydra-bot python - fetch --since 1792937316 --until 1793836800 \
        < scripts/analysis/label_candidates.py > candidates.csv
    python scripts/analysis/label_candidates.py run candidates.csv

Labels: fwd_N = close N minutes after the signal minute vs the ask at the signal, %
(gross; a round trip costs 0.2% in fees). mfe/mae = best / worst price inside the
window. x_N = fwd_N minus BTC over the same minutes (is it the coin or the market?).
Extra features computed from the klines before the signal: ret_5/15/60 (% change),
sigma_60 (st.dev. of 1m returns, %), vol_ratio (turnover of the last 5 bars / last 60).
"""
import argparse
import csv
import math
import os
import sqlite3
import statistics
import sys
import json
from collections import defaultdict

DB_COLS = ['id', 'timestamp', 'symbol', 'confidence', 'rvol_spike', 'dump_depth', 'obi_skew', 'btc_1h', 'score', 'mode',
           'spread_pct', 'bid_vol', 'ask_vol', 'turnover24h', 'bid_ask_source', 'entry_ask', 'reject_reason', 'picked',
           'sigma_1m', 'dump_z', 'vol_decay', 'higher_low', 'sell_ratio', 'idle_bars']
HORIZONS = (5, 15, 30, 60, 120)
LABELS = ([f'fwd_{m}' for m in HORIZONS] + ['mfe_30', 'mae_30', 'mfe_60', 'mae_60', 'x_30', 'x_60',
          'ret_5', 'ret_15', 'ret_60', 'sigma_60', 'vol_ratio', 'ref_price'])
BEFORE, AFTER, FEE_RT = 62, 122, 0.2


# ---------------------------------------------------------------- fetch
def klines(ex, symbol, minutes):
    """{minute ts: (open, high, low, close, turnover)} covering the given signal minutes."""
    spans = []
    for m in sorted(minutes):
        lo, hi = m - BEFORE * 60, m + AFTER * 60
        if spans and lo <= spans[-1][1] + 60:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    bars = {}
    for lo, hi in spans:
        start = lo
        while start <= hi:
            end = min(start + 999 * 60, hi)
            rows = ex.publicGetV5MarketKline({'category': 'spot', 'symbol': symbol, 'interval': '1',
                                              'start': start * 1000, 'end': end * 1000, 'limit': 1000})['result']['list']
            for r in rows:
                bars[int(r[0]) // 1000] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6]))
            start = end + 60
    return bars


def label(ts, ask, bars, btc):
    m0 = int(ts // 60 * 60)
    if m0 not in bars:
        return None
    p = ask if ask and ask > 0 else bars[m0][3]
    out = {'ref_price': p}
    for n in HORIZONS:
        b = bars.get(m0 + n * 60)
        out[f'fwd_{n}'] = (b[3] / p - 1) * 100 if b else None
    for n in (30, 60):
        win = [bars[m0 + k * 60] for k in range(1, n + 1) if m0 + k * 60 in bars]
        out[f'mfe_{n}'] = (max(b[1] for b in win) / p - 1) * 100 if len(win) >= n - 2 else None
        out[f'mae_{n}'] = (min(b[2] for b in win) / p - 1) * 100 if len(win) >= n - 2 else None
        b0, b1 = btc.get(m0), btc.get(m0 + n * 60)
        out[f'x_{n}'] = out[f'fwd_{n}'] - (b1[3] / b0[3] - 1) * 100 if b0 and b1 and out[f'fwd_{n}'] is not None else None
    prev = [bars.get(m0 - k * 60) for k in range(1, 62)]            # prev[0] = last closed bar
    for n in (5, 15, 60):
        out[f'ret_{n}'] = (prev[0][3] / prev[n][3] - 1) * 100 if prev[0] and prev[n] else None
    closes = [b[3] for b in prev[:61] if b]
    rets = [(a / b - 1) * 100 for a, b in zip(closes, closes[1:])]
    out['sigma_60'] = statistics.pstdev(rets) if len(rets) >= 30 else None
    vols = [b[4] for b in prev[:60] if b]
    out['vol_ratio'] = (sum(vols[:5]) / 5) / (sum(vols) / len(vols)) if len(vols) >= 30 and sum(vols) > 0 else None
    return out


def fetch(args):
    import ccxt
    cfg = json.load(open(args.config))
    ex = ccxt.bybit({'enableRateLimit': True, 'rateLimit': 100, 'options': {'defaultType': 'spot'}})
    if cfg.get('exchange', {}).get('demo_trading'):
        ex.enable_demo_trading(True)
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = con.execute(f"SELECT {','.join(DB_COLS)} FROM dispatcher_features WHERE profit IS NULL AND trade_id = 0 "
                       "AND timestamp >= ? AND timestamp < ? ORDER BY symbol, timestamp", (args.since, args.until)).fetchall()
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r[2]].append(r)
    span = (int(args.since // 60 * 60), int(args.until // 60 * 60))
    btc = klines(ex, 'BTCUSDT', range(span[0], span[1] + 60, (BEFORE + AFTER) * 60))
    w = csv.writer(sys.stdout, lineterminator='\n')
    w.writerow(DB_COLS + LABELS)
    for sym, group in by_sym.items():
        try:
            bars = klines(ex, sym.replace('/', ''), {int(r[1] // 60 * 60) for r in group})
        except Exception as e:
            print(f"{sym}: klines failed: {e}", file=sys.stderr)
            continue
        for r in group:
            lab = label(r[1], r[DB_COLS.index('entry_ask')], bars, btc)
            if lab:
                w.writerow(list(r) + [lab[k] if lab[k] is None or k == 'ref_price' else round(lab[k], 5) for k in LABELS])
    print(f"rows {len(rows)}, symbols {len(by_sym)}", file=sys.stderr)


# ---------------------------------------------------------------- run
FEATURES = ['dump_depth', 'rvol_spike', 'confidence', 'score', 'obi_skew', 'btc_1h', 'spread_pct', 'turnover24h',
            'idle_bars', 'sigma_1m', 'dump_z', 'vol_decay', 'sell_ratio', 'ret_5', 'ret_15', 'ret_60', 'sigma_60',
            'vol_ratio', 'dump_sigma']


def load(path):
    rows = []
    with open(path, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            row = {k: (v if k in ('symbol', 'mode', 'bid_ask_source', 'reject_reason') else float(v)) if v != '' else None
                   for k, v in r.items()}
            row['dump_sigma'] = row['dump_depth'] / row['sigma_60'] if row['dump_depth'] is not None and row['sigma_60'] else None
            row['stage'] = row['reject_reason'] or ('picked' if row['picked'] else 'passed_not_picked')
            row['cluster'] = (row['symbol'], int(row['timestamp'] // 86400))
            rows.append(row)
    return sorted(rows, key=lambda r: r['timestamp'])


def events(rows, gap_sec, key):
    """First row of every burst: the scanner re-logs a falling symbol every few seconds."""
    last, out = {}, []
    for r in rows:
        k = key(r)
        if r['timestamp'] - last.get(k, -1e18) >= gap_sec:
            out.append(r)
        last[k] = r['timestamp']
    return out


def mean_t(rows, col, shift=0.0):
    """(n, mean - shift, t) with standard errors clustered by symbol x day."""
    xs = [(r[col] - shift, r['cluster']) for r in rows if r[col] is not None]
    if len(xs) < 2:
        return len(xs), float('nan'), float('nan')
    m = sum(x for x, _ in xs) / len(xs)
    by = defaultdict(float)
    for x, c in xs:
        by[c] += x - m
    se = math.sqrt(sum(v * v for v in by.values())) / len(xs)
    return len(xs), m, m / se if se > 0 else float('nan')


def spearman(pairs):
    def ranks(v):
        order, out, i = sorted(range(len(v)), key=v.__getitem__), [0.0] * len(v), 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2
            i = j + 1
        return out
    if len(pairs) < 30:
        return float('nan')
    a, b = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / den if den else float('nan')


def run(args):
    rows = [r for r in load(args.dataset) if r[args.target] is not None]
    ev = events(rows, args.gap_min * 60, lambda r: r['symbol'])
    mid = ev[len(ev) // 2]['timestamp']
    tgt = args.target
    print(f"rows {len(rows)}, events (first per symbol per {args.gap_min:g} min) {len(ev)}, symbols {len({r['symbol'] for r in ev})}, "
          f"symbol-days {len({r['cluster'] for r in ev})}; target {tgt}; t = clustered by symbol x day; fee round trip {FEE_RT}%")

    print("\n== all events: what the price does after a dump signal, % from the ask ==")
    for col in [f'fwd_{m}' for m in HORIZONS] + ['x_30', 'x_60', 'mfe_30', 'mae_30']:
        n, m, t = mean_t(ev, col)
        xs = sorted(r[col] for r in ev if r[col] is not None)
        print(f"  {col:<8} n={n:>5} mean={m:>+7.3f} t={t:>+5.1f} median={xs[len(xs) // 2]:>+7.3f} "
              f"share > +{FEE_RT}: {100 * sum(x > FEE_RT for x in xs) / len(xs):>3.0f}%")

    print(f"\n== by scanner verdict (first row per symbol+verdict per {args.gap_min:g} min): does the filter cut bad or good signals? ==")
    sev = events(rows, args.gap_min * 60, lambda r: (r['symbol'], r['stage']))
    print(f"  {'verdict':<20}{'n':>6}{tgt:>9}{'t':>6}{'x_30':>8}{'fwd_60':>8}{'>fee':>6}{'mae_30 med':>12}")
    for stage in sorted({r['stage'] for r in sev}, key=lambda s: -sum(r['stage'] == s for r in sev)):
        g = [r for r in sev if r['stage'] == stage]
        n, m, t = mean_t(g, tgt)
        mae = sorted(r['mae_30'] for r in g if r['mae_30'] is not None)
        print(f"  {stage:<20}{n:>6}{m:>+9.3f}{t:>+6.1f}{mean_t(g, 'x_30')[1]:>+8.3f}{mean_t(g, 'fwd_60')[1]:>+8.3f}"
              f"{100 * sum(r[tgt] > FEE_RT for r in g) / max(1, n):>5.0f}%{mae[len(mae) // 2] if mae else float('nan'):>+12.2f}")

    print(f"\n== features vs {tgt}: Spearman rho (all / first half / second half) and the mean {tgt} by quintile of the feature ==")
    print(f"  {'feature':<12}{'n':>6}{'rho':>7}{'rho h1':>8}{'rho h2':>8}   " + ''.join(f"{'Q' + str(i):>8}" for i in range(1, 6)) + f"{'Q5-Q1':>8}")
    for f in FEATURES:
        g = sorted((r for r in ev if r[f] is not None), key=lambda r: r[f])
        if len(g) < 100:
            continue
        rho = [spearman([(r[f], r[tgt]) for r in part]) for part in
               (g, [r for r in g if r['timestamp'] < mid], [r for r in g if r['timestamp'] >= mid])]
        qs = [mean_t(g[len(g) * i // 5:len(g) * (i + 1) // 5], tgt)[1] for i in range(5)]
        print(f"  {f:<12}{len(g):>6}{rho[0]:>+7.3f}{rho[1]:>+8.3f}{rho[2]:>+8.3f}   " + ''.join(f"{q:>+8.3f}" for q in qs) + f"{qs[4] - qs[0]:>+8.3f}")

    print(f"\n== rules chosen on the first half, checked on the second (net of the {FEE_RT}% fee) ==")
    h1, h2 = [r for r in ev if r['timestamp'] < mid], [r for r in ev if r['timestamp'] >= mid]
    conds = []
    for f in FEATURES:
        vals = sorted(r[f] for r in h1 if r[f] is not None)
        if len(vals) < 100:
            continue
        for q in (0.2, 0.4, 0.6, 0.8):
            cut = vals[int(len(vals) * q)]
            conds.append((f"{f} >= {cut:.4g}", lambda r, f=f, cut=cut: r[f] is not None and r[f] >= cut))
            conds.append((f"{f} <= {cut:.4g}", lambda r, f=f, cut=cut: r[f] is not None and r[f] <= cut))
    singles = []
    for name, fn in conds:
        n, m, t = mean_t([r for r in h1 if fn(r)], tgt, FEE_RT)
        if n >= 100:
            singles.append((t, name, fn))
    singles.sort(key=lambda s: -s[0])
    rules = [(name, fn) for _, name, fn in singles[:10]]
    rules += [(f"{a[1]} & {b[1]}", lambda r, a=a[2], b=b[2]: a(r) and b(r))
              for i, a in enumerate(singles[:12]) for b in singles[i + 1:12] if a[1].split()[0] != b[1].split()[0]]
    scored = []
    for name, fn in rules:
        s1, s2 = mean_t([r for r in h1 if fn(r)], tgt, FEE_RT), mean_t([r for r in h2 if fn(r)], tgt, FEE_RT)
        if s1[0] >= 60 and s2[0] >= 30:
            scored.append((s1[2], name, s1, s2))
    print(f"  {'rule':<52}{'h1 n':>6}{'net':>8}{'t':>6}   {'h2 n':>6}{'net':>8}{'t':>6}")
    for _, name, s1, s2 in sorted(scored, key=lambda s: -s[0])[:20]:
        print(f"  {name:<52}{s1[0]:>6}{s1[1]:>+8.3f}{s1[2]:>+6.1f}   {s2[0]:>6}{s2[1]:>+8.3f}{s2[2]:>+6.1f}")
    n, m, t = mean_t(h2, tgt, FEE_RT)
    print(f"  {'(no rule: every event of the second half)':<52}{'':>20}   {n:>6}{m:>+8.3f}{t:>+6.1f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('fetch')
    f.add_argument('--since', type=float, required=True)
    f.add_argument('--until', type=float, required=True)
    f.add_argument('--db', default='/app/shared/state/trades.db')
    f.add_argument('--config', default='/app/shared/config.json')
    r = sub.add_parser('run')
    r.add_argument('dataset')
    r.add_argument('--gap-min', type=float, default=15, help='one event per symbol per this many minutes')
    r.add_argument('--target', default='fwd_30')
    args = ap.parse_args()
    fetch(args) if args.cmd == 'fetch' else run(args)


if __name__ == '__main__':
    main()
