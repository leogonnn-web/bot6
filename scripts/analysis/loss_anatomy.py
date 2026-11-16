"""Where do the losses come from, and what did the market look like when they started? (B-35)

Read-only, offline. Inputs are the datasets of the two sibling scripts plus,
optionally, the hourly futures cache and the liquidation journal of the
"coin scanner" project (Binance USDT-M 1h candles / OI / funding / ratios).

    python scripts/analysis/loss_anatomy.py --trips replay_a.json replay_b.json \
        --events candidates.csv --scanner "C:/Users/me/Desktop/scanner"

1. anatomy   - real round trips: which exits lose, how deep, did the price come back
2. context   - winners vs losers by the state of the coin and the market at entry
3. knives    - the same context on every scan event (passed or rejected), labelled
               "knife" when the price went KNIFE_PCT below the ask within an hour:
               that is the averaging level plus the stop, i.e. a full-size loss
4. liquidations around the entries the journal covers

AUC = probability that a random loser/knife has a larger value of the feature than
a random winner/non-knife; 0.5 = no information.
"""
import argparse
import csv
import glob
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import label_candidates as lc  # noqa: E402

KNIFE_PCT, H = 1.6, 3600


def auc(pos, neg):
    if len(pos) < 8 or len(neg) < 8:
        return float('nan')
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    rank_sum, i = 0.0, 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        rank_sum += sum((i + j) / 2 + 1 for k in range(i, j + 1) if allv[k][1])
        i = j + 1
    return (rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else float('nan')


# ---------------------------------------------------------------- hourly context
class Hourly:
    """Per symbol {hour ts: (close, turnover, taker buy, oi, funding, topacc, global, taker ratio)}."""

    def __init__(self, scanner_dir, t_min, t_max):
        self.sym, lo, hi = {}, (t_min - 30 * H) * 1000, (t_max + H) * 1000
        for path in glob.glob(os.path.join(scanner_dir, 'cache', 'binance', '*.json')):
            d = json.load(open(path, encoding='utf-8'))['data']
            r = d['ratios']
            rows = {c[0] // 1000: (c[3], c[4], c[5], d['oi'][i], d['fund'][i], r['topacc'][i], r['global'][i], r['taker'][i])
                    for i, c in enumerate(d['candles']) if lo <= c[0] <= hi}
            if rows:
                self.sym[os.path.basename(path)[:-5]] = rows
        self._market = {}

    def name(self, symbol):
        s = symbol.replace('/', '')
        return s if s in self.sym else '1000' + s if '1000' + s in self.sym else None

    def _chg(self, rows, h, n):
        a, b = rows.get(h - H), rows.get(h - (n + 1) * H)
        return (a[0] / b[0] - 1) * 100 if a and b and b[0] else None

    def market(self, h):
        if h not in self._market:
            c1 = [x for x in (self._chg(r, h, 1) for r in self.sym.values()) if x is not None]
            c4 = [x for x in (self._chg(r, h, 4) for r in self.sym.values()) if x is not None]
            btc = self.sym.get('BTCUSDT', {})
            self._market[h] = {
                'mkt_down_share': 100 * sum(x < 0 for x in c1) / len(c1) if c1 else None,
                'mkt_dump_share': 100 * sum(x < -1 for x in c1) / len(c1) if c1 else None,
                'mkt_med_1h': statistics.median(c1) if c1 else None,
                'mkt_med_4h': statistics.median(c4) if c4 else None,
                'btc_chg_1h': self._chg(btc, h, 1), 'btc_chg_4h': self._chg(btc, h, 4), 'btc_chg_24h': self._chg(btc, h, 24),
            }
        return self._market[h]

    def features(self, symbol, ts):
        """Everything is taken from hours that had closed before `ts`."""
        h = int(ts // H * H)
        out = dict(self.market(h))
        rows = self.sym.get(self.name(symbol) or '')
        if rows:
            b1, b5 = rows.get(h - H), rows.get(h - 5 * H)
            prev = [rows[h - k * H][1] for k in range(2, 26) if h - k * H in rows]
            out.update({
                'coin_chg_1h': self._chg(rows, h, 1), 'coin_chg_4h': self._chg(rows, h, 4), 'coin_chg_24h': self._chg(rows, h, 24),
                'taker_buy_share': 100 * b1[2] / b1[1] if b1 and b1[1] else None,
                'turnover_ratio': b1[1] / (sum(prev) / len(prev)) if b1 and len(prev) >= 12 and sum(prev) else None,
                'oi_chg_4h': (b1[3] / b5[3] - 1) * 100 if b1 and b5 and b1[3] and b5[3] else None,
                'funding_bp': b1[4] * 1e4 if b1 else None,
                'top_accounts_ls': b1[5] or None if b1 else None,
                'global_ls': b1[6] or None if b1 else None,
            })
            if out['coin_chg_4h'] is not None and out['btc_chg_4h'] is not None:
                out['coin_vs_btc_4h'] = out['coin_chg_4h'] - out['btc_chg_4h']
        return out


def breadth(rows, window=600):
    """{row id: distinct symbols with a scan row in the previous `window` seconds} - how wide the dump is."""
    out, q, counts, j = {}, [], defaultdict(int), 0
    for r in rows:
        while j < len(q) and q[j][0] < r['timestamp'] - window:
            counts[q[j][1]] -= 1
            if not counts[q[j][1]]:
                del counts[q[j][1]]
            j += 1
        q.append((r['timestamp'], r['symbol']))
        counts[r['symbol']] += 1
        out[r['id']] = len(counts)
    return out


# ---------------------------------------------------------------- sections
def anatomy(trips):
    print(f"== 1. anatomy of {len(trips)} real round trips ==")
    total = sum(t['pnl'] for t in trips)
    bought = sum(t['bought_usdt'] for t in trips)
    print(f"net {total:+.2f} USDT on {bought:.0f} bought; round-trip fees ~{bought * 0.002:.2f}; before fees {total + bought * 0.002:+.2f}")
    for t in trips:
        t['ret'] = 100 * t['pnl'] / t['bought_usdt']
        t['cls'] = ('stop-size loss (<= -1%)' if t['ret'] <= -1 else 'small loss (-1..-0.05%)' if t['ret'] < -0.05
                    else 'about zero' if t['ret'] <= 0.05 else 'win')
        after = [b for b in t['bars'] if b[0] >= t['entry_ts'] // 60 * 60 + 60]
        held = [b for b in after if b[0] <= t['close_ts']]
        later = [b for b in after if t['close_ts'] < b[0] <= t['close_ts'] + 3600]
        t['mae'] = (min(b[3] for b in held) / t['entry_price'] - 1) * 100 if held else 0.0
        t['hold_min'] = (t['close_ts'] - t['entry_ts']) / 60
        t['back_above_entry'] = bool(later) and max(b[2] for b in later) >= t['entry_price']
        t['lower_after'] = bool(later and held) and min(b[3] for b in later) < min(b[3] for b in held)
    print(f"\n  {'outcome':<26}{'n':>5}{'net':>8}{'avg':>8}{'averaged':>10}{'hold med':>10}{'worst dip med':>15}")
    for cls in ('win', 'about zero', 'small loss (-1..-0.05%)', 'stop-size loss (<= -1%)'):
        g = [t for t in trips if t['cls'] == cls]
        if g:
            print(f"  {cls:<26}{len(g):>5}{sum(t['pnl'] for t in g):>+8.2f}{sum(t['pnl'] for t in g) / len(g):>+8.3f}"
                  f"{sum(t['buy_orders'] > 1 for t in g):>10}{med(t['hold_min'] for t in g):>9.0f}m{med(t['mae'] for t in g):>+14.2f}%")
    for label, g in (('single entry', [t for t in trips if t['buy_orders'] == 1]), ('averaged (level 1 filled)', [t for t in trips if t['buy_orders'] > 1])):
        print(f"  {label:<26}{len(g):>5}{sum(t['pnl'] for t in g):>+8.2f}  wins {sum(t['cls'] == 'win' for t in g)}, "
              f"stop-size losses {sum(t['cls'].startswith('stop') for t in g)}")
    losers = [t for t in trips if t['ret'] < -0.05]
    print(f"\n  after the {len(losers)} losing exits, within the next hour: price back above the ENTRY in "
          f"{sum(t['back_above_entry'] for t in losers)}, a new low below the one seen while holding in {sum(t['lower_after'] for t in losers)}")
    by_sym = defaultdict(list)
    for t in trips:
        by_sym[t['symbol']].append(t['pnl'])
    worst = sorted(by_sym.items(), key=lambda kv: sum(kv[1]))[:8]
    print("  worst symbols (trades, net): " + ', '.join(f"{s} {len(v)}/{sum(v):+.2f}" for s, v in worst))
    by_hour = defaultdict(list)
    for t in trips:
        by_hour[datetime.fromtimestamp(t['entry_ts'], timezone.utc).hour // 4 * 4].append(t)
    print("  by entry hour UTC (trades, losers, net): " + ', '.join(
        f"{h:02d}-{h + 4:02d}: {len(g)}/{sum(t['ret'] < -0.05 for t in g)}/{sum(t['pnl'] for t in g):+.2f}" for h, g in sorted(by_hour.items())))


def compare(title, items, is_bad, feats, split_ts=None):
    """AUC of every feature for the bad class, plus medians; items are dicts."""
    bad, good = [x for x in items if is_bad(x)], [x for x in items if not is_bad(x)]
    print(f"\n== {title}: {len(bad)} bad vs {len(good)} other ({100 * len(bad) / max(1, len(items)):.0f}% bad) ==")
    head = f"  {'feature':<18}{'n':>6}{'AUC':>7}" + (f"{'AUC h1':>8}{'AUC h2':>8}" if split_ts else '') + f"{'median bad':>12}{'median other':>14}"
    print(head)
    rows = []
    for f in feats:
        b, g = [x[f] for x in bad if x.get(f) is not None], [x[f] for x in good if x.get(f) is not None]
        a = auc(b, g)
        if a != a:
            continue
        extra = ''
        if split_ts:
            halves = [auc([x[f] for x in bad if x.get(f) is not None and (x['timestamp'] < split_ts) == first],
                          [x[f] for x in good if x.get(f) is not None and (x['timestamp'] < split_ts) == first]) for first in (True, False)]
            extra = f"{halves[0]:>8.2f}{halves[1]:>8.2f}"
        rows.append((abs(a - 0.5), f"  {f:<18}{len(b) + len(g):>6}{a:>7.2f}{extra}{med(b):>+12.3f}{med(g):>+14.3f}"))
    for _, text in sorted(rows, reverse=True):
        print(text)


def quintiles(items, f, is_bad, value):
    g = sorted((x for x in items if x.get(f) is not None), key=lambda x: x[f])
    parts = [g[len(g) * i // 5:len(g) * (i + 1) // 5] for i in range(5)]
    return (f"  {f:<18}" + ''.join(f"{100 * sum(is_bad(x) for x in p) / max(1, len(p)):>6.0f}%" for p in parts) + '   |'
            + ''.join(f"{statistics.mean(value(x) for x in p):>+8.3f}" for p in parts)
            + f"   | cuts {parts[0][-1][f]:.3g} / {parts[3][-1][f]:.3g}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--trips', nargs='+', required=True, help='replay_exits.py fetch output(s)')
    ap.add_argument('--events', help='label_candidates.py fetch output')
    ap.add_argument('--scanner', help='coin scanner project dir (cache/binance/*.json, journal/liquidations.csv)')
    args = ap.parse_args()

    trips = sorted((t for p in args.trips for t in json.load(open(p, encoding='utf-8-sig'))['trips']
                    if t.get('bars') and t['close_ts'] - t['entry_ts'] <= 4 * H), key=lambda t: t['entry_ts'])
    anatomy(trips)

    rows = [r for r in lc.load(args.events) if r['mae_60'] is not None] if args.events else []
    wide = breadth(rows) if rows else {}
    hourly = None
    if args.scanner:
        ts_all = [t['entry_ts'] for t in trips] + [r['timestamp'] for r in rows]
        hourly = Hourly(args.scanner, min(ts_all), max(ts_all))
        print(f"\nhourly futures context: {len(hourly.sym)} symbols loaded")

    # ---- 2. real trades
    for t in trips:
        t['timestamp'] = t['entry_ts']
        i0 = next(i for i, b in enumerate(t['bars']) if b[0] <= t['entry_ts'] < b[0] + 60)
        pre = t['bars'][max(0, i0 - 20):i0]
        t['pre_ret_5'] = (pre[-1][4] / pre[-6][4] - 1) * 100 if len(pre) >= 6 else None
        t['pre_ret_15'] = (pre[-1][4] / pre[-16][4] - 1) * 100 if len(pre) >= 16 else None
        rets = [(b[4] / a[4] - 1) * 100 for a, b in zip(pre, pre[1:])]
        t['pre_sigma'] = statistics.pstdev(rets) if len(rets) >= 10 else None
        t['dump_sigma'] = t['dump_depth'] / t['pre_sigma'] if t.get('dump_depth') and t['pre_sigma'] else None
        t['hour_utc'] = datetime.fromtimestamp(t['entry_ts'], timezone.utc).hour
        near = [r for r in rows if abs(r['timestamp'] - t['entry_ts']) < 60]
        t['symbols_dumping'] = max((wide[r['id']] for r in near), default=None)
        if hourly:
            t.update(hourly.features(t['symbol'], t['entry_ts']))
    hf = ['mkt_down_share', 'mkt_dump_share', 'mkt_med_1h', 'mkt_med_4h', 'btc_chg_1h', 'btc_chg_4h', 'btc_chg_24h',
          'coin_chg_1h', 'coin_chg_4h', 'coin_chg_24h', 'coin_vs_btc_4h', 'taker_buy_share', 'turnover_ratio', 'oi_chg_4h',
          'funding_bp', 'top_accounts_ls', 'global_ls']
    compare("2. real trades, losers (worse than -0.05%) vs the rest", trips, lambda t: t['ret'] < -0.05,
            ['score', 'dump_depth', 'dump_sigma', 'pre_ret_5', 'pre_ret_15', 'pre_sigma', 'hour_utc', 'symbols_dumping'] + hf)

    # ---- 3. every scan event
    if rows:
        ev = lc.events(rows, 900, lambda r: r['symbol'])
        for r in ev:
            r['symbols_dumping'] = wide[r['id']]
            r['hour_utc'] = datetime.fromtimestamp(r['timestamp'], timezone.utc).hour
            if hourly:
                r.update(hourly.features(r['symbol'], r['timestamp']))
        knife = lambda r: r['mae_60'] <= -KNIFE_PCT
        feats = ['dump_depth', 'dump_sigma', 'rvol_spike', 'idle_bars', 'ret_5', 'ret_15', 'ret_60', 'sigma_60', 'vol_ratio',
                 'spread_pct', 'turnover24h', 'obi_skew', 'hour_utc', 'symbols_dumping'] + hf
        mid = ev[len(ev) // 2]['timestamp']
        compare(f"3. scan events, knife (-{KNIFE_PCT}% within 60 m) vs the rest", ev, knife, feats, split_ts=mid)
        print("\n  knife rate by quintile of the feature (low -> high)   | mean fwd_30 of the same quintiles, %")
        for f in ('sigma_60', 'symbols_dumping', 'mkt_dump_share', 'mkt_med_1h', 'btc_chg_1h', 'coin_chg_4h', 'coin_chg_24h',
                  'coin_vs_btc_4h', 'ret_60', 'dump_sigma', 'turnover24h', 'oi_chg_4h', 'taker_buy_share', 'funding_bp'):
            if sum(r.get(f) is not None for r in ev) >= 200:
                print(quintiles(ev, f, knife, lambda r: r['fwd_30']))

    # ---- 4. liquidations
    liq_path = os.path.join(args.scanner or '', 'journal', 'liquidations.csv')
    if args.scanner and os.path.exists(liq_path):
        liq = []
        with open(liq_path, encoding='utf-8-sig', newline='') as f:
            for r in csv.DictReader(f, delimiter=';'):
                if r['symbol'] != '__GAP__':
                    liq.append((int(r['ts_ms']) / 1000, r['symbol'], r['side'], float(r['liq_usd'])))
        liq.sort()
        covered = [t for t in trips if liq and liq[0][0] + H <= t['entry_ts'] <= liq[-1][0]]
        print(f"\n== 4. liquidations (Bybit, all symbols) in the hour before entry: {len(covered)} trades covered ==")
        for t in covered:
            win = [x for x in liq if t['entry_ts'] - H <= x[0] < t['entry_ts']]
            t['liq_long_60m'] = sum(x[3] for x in win if x[2] == 'Buy')
            t['liq_short_60m'] = sum(x[3] for x in win if x[2] == 'Sell')
            t['liq_long_share'] = 100 * t['liq_long_60m'] / (t['liq_long_60m'] + t['liq_short_60m']) if win else None
            t['liq_coin_60m'] = sum(x[3] for x in win if x[1] == t['symbol'])
        if covered:
            compare("liquidations, losers vs the rest", covered, lambda t: t['ret'] < -0.05,
                    ['liq_long_60m', 'liq_short_60m', 'liq_long_share', 'liq_coin_60m'])
            g = sorted(covered, key=lambda t: t['liq_long_60m'])
            for label, part in (('calm third', g[:len(g) // 3]), ('middle', g[len(g) // 3:2 * len(g) // 3]), ('most long liquidations', g[2 * len(g) // 3:])):
                print(f"  {label:<24} n={len(part):>3} losers={sum(t['ret'] < -0.05 for t in part):>2} net={sum(t['pnl'] for t in part):>+6.2f} "
                      f"long liq 60m: {part[0]['liq_long_60m']:,.0f}..{part[-1]['liq_long_60m']:,.0f} USD")


if __name__ == '__main__':
    main()
