"""Replay real entries over 1m klines with different exit rules (TP / stop / grid / timers).

Read-only. Entries are what the bot really bought (exchange execution history),
so the question answered is "same entries, other exits", not "other entries".

  fetch  inside the bot container (ccxt, API keys, trades.db): round trips from the
         execution history + 1m klines around each entry -> JSON on stdout
  run    anywhere, offline: simulate the exit lifecycle over a parameter grid

    docker compose run --rm --no-deps -T hydra-bot python - fetch --since 1792937316 --until 1793836800 \
        < scripts/analysis/replay_exits.py > replay.json
    python scripts/analysis/replay_exits.py run replay.json

Model (mirrors hydra_net.py / in_position.py / breakeven.py):
  averaging phase  - level 0 held, level 1 rests at entry*(1 - max(mode distance, ATR%/2));
                     no stop here, only TP, the -3% knife guard and grid_max_hold
  position phase   - starts when level 1 fills / the grid times out (or at once with
                     levels=0): stop from the average price, TP -> breakeven sell after
                     `be` seconds, market exit after `hard` seconds
Limits of 1m data: the rest of the entry minute is reduced to its close; when one
bar spans both the stop and the TP the order is unknown, so every figure is given
as a pessimistic (adverse first) and an optimistic (TP first) bound. A longer hold
skips the real entries that happened while the simulated position was still open.
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

# dispatcher.MODE_PARAMS: grid distance %, take profit %, slot multiplier
MODES = {'conservative': (0.8, 0.5, 0.8), 'normal': (0.5, 0.8, 1.0), 'aggressive': (0.3, 1.5, 1.2)}
FEE, SLIP, BE_MULT, KNIFE_PCT, GRID_HOLD = 0.001, 0.001, 1.0022, 3.0, 1800
BARS_BEFORE, BARS_AFTER = 20, 160
AS_IS = {'tp': None, 'sl': 1.2, 'levels': 1, 'be': 900, 'hard': 1800}


# ---------------------------------------------------------------- fetch
def fetch(args):
    import ccxt
    cfg = json.load(open(args.config))
    demo = bool(cfg.get('exchange', {}).get('demo_trading'))
    ex = ccxt.bybit({'apiKey': os.getenv('BYBIT_DEMO_API_KEY' if demo else 'BYBIT_API_KEY', ''),
                     'secret': os.getenv('BYBIT_DEMO_API_SECRET' if demo else 'BYBIT_API_SECRET', ''),
                     'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
    if demo:
        ex.enable_demo_trading(True)

    execs, start, end_all = [], int(args.since * 1000), int((args.until + 2 * 3600) * 1000)
    while start < end_all:
        end, cursor = min(start + 7 * 86400_000 - 1, end_all), None
        while True:
            q = {'category': 'spot', 'startTime': start, 'endTime': end, 'limit': 100}
            if cursor:
                q['cursor'] = cursor
            res = ex.privateGetV5ExecutionList(q)['result']
            execs += res['list']
            cursor = res.get('nextPageCursor')
            if not cursor or not res['list']:
                break
        start = end + 1
    trips = [t for t in round_trips(execs) if args.since <= t['entry_ts'] < args.until]

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    feats = defaultdict(list)
    for sym, ts, mode, score, dump, profit in con.execute(
            "SELECT symbol, timestamp, mode, score, dump_depth, profit FROM dispatcher_features "
            "WHERE profit IS NOT NULL AND timestamp >= ?", (args.since,)):
        feats[sym.replace('/', '')].append((ts, mode, score, dump, profit))
    for t in trips:
        near = min(feats.get(t['symbol'], []), key=lambda f: abs(f[0] - t['close_ts']), default=None)
        if near and abs(near[0] - t['close_ts']) < 600:
            t.update(mode=near[1], score=near[2], dump_depth=near[3], db_profit=near[4])
        first = int(t['entry_ts'] // 60 * 60 - BARS_BEFORE * 60) * 1000
        rows = ex.publicGetV5MarketKline({'category': 'spot', 'symbol': t['symbol'], 'interval': '1', 'start': first,
                                          'end': first + (BARS_BEFORE + BARS_AFTER) * 60_000, 'limit': 1000})['result']['list']
        t['bars'] = sorted([int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows)
    json.dump({'since': args.since, 'until': args.until, 'demo': demo, 'executions': len(execs), 'trips': trips}, sys.stdout)


def round_trips(execs):
    """Flat -> first buy -> ... -> flat again (remainder under 1 USDT), per symbol."""
    by_sym = defaultdict(list)
    for e in execs:
        px, qty = float(e['execPrice']), float(e['execQty'])
        fee = float(e.get('execFee') or 0)
        # spot: the buy fee is taken in the base coin (it shrinks what can be sold,
        # so it is already inside `sold`), the sell fee in USDT
        in_usdt = (e.get('feeCurrency') or ('USDT' if e['side'] == 'Sell' else '')) == 'USDT'
        by_sym[e['symbol']].append((int(e['execTime']) / 1000, e['side'], px, qty, e['orderId'],
                                    fee if in_usdt else 0.0, 0.0 if in_usdt else fee))
    trips = []
    for sym, rows in by_sym.items():
        cur = None
        for ts, side, px, qty, oid, fee_usdt, fee_coin in sorted(rows):
            if side == 'Buy':
                if cur is None:
                    cur = {'symbol': sym, 'entry_ts': ts, 'entry_order': oid, 'entry_qty': 0.0, 'entry_cost': 0.0,
                           'buy_orders': set(), 'held': 0.0, 'bought': 0.0, 'sold': 0.0, 'fees': 0.0}
                cur['buy_orders'].add(oid)
                cur['held'] += qty - fee_coin
                cur['bought'] += px * qty
                if oid == cur['entry_order']:
                    cur['entry_qty'] += qty
                    cur['entry_cost'] += px * qty
            elif cur is not None:
                cur['held'] -= qty
                cur['sold'] += px * qty
                if cur['held'] * px < 1.0:
                    cur['close_ts'] = ts
            if cur is not None:
                cur['fees'] += fee_usdt
                if 'close_ts' in cur:
                    trips.append({'symbol': sym, 'entry_ts': cur['entry_ts'], 'close_ts': cur['close_ts'],
                                  'entry_price': cur['entry_cost'] / cur['entry_qty'], 'entry_usdt': cur['entry_cost'],
                                  'buy_orders': len(cur['buy_orders']), 'bought_usdt': cur['bought'],
                                  'pnl': cur['sold'] - cur['bought'] - cur['fees']})
                    cur = None
    return sorted(trips, key=lambda t: t['entry_ts'])


# ---------------------------------------------------------------- simulate
def atr(bars, period=14):
    """ATRAnalyzer.calculate: SMA seed over `period` true ranges, then EMA."""
    trs = [max(h - l, abs(h - pc), abs(l - pc)) for (_, _, h, l, _), pc in zip(bars, [bars[0][2]] + [b[4] for b in bars[:-1]])]
    if len(trs) < period:
        return 0.0
    ema, k = sum(trs[:period]) / period, 2 / (period + 1)
    for tr in trs[period:]:
        ema = tr * k + ema * (1 - k)
    return ema


def simulate(trip, p, pess, slot=6.0):
    """-> (pnl in USDT, exit ts, reason, level 1 filled) for one entry under parameters `p`."""
    dist_pct, tp_mode, mult = MODES.get(trip.get('mode'), MODES['conservative'])
    tp_pct = p['tp'] or tp_mode
    entry, t0 = trip['entry_price'], trip['entry_ts']
    i0 = next((i for i, b in enumerate(trip['bars']) if b[0] <= t0 < b[0] + 60), None)
    if i0 is None:
        return None
    qty, cost, avg = slot / entry, slot, entry
    level_px = level_usdt = None
    if p['levels'] >= 1:
        a = atr(trip['bars'][max(0, i0 - BARS_BEFORE):i0])
        level_px = entry * (1 - max(dist_pct, a / entry * 100 * 0.5) / 100)
        level_usdt = slot * mult * 1.5
    averaging, pos_t, be = level_px is not None, None if level_px is not None else t0, False

    def done(px, ts, reason, market):
        return qty * (1 - FEE) * px * (1 - (SLIP if market else 0)) * (1 - FEE) - cost, ts, reason, cost > slot

    for k, (ts, o, h, l, c) in enumerate(trip['bars'][i0:]):
        if k == 0:
            o, h, l = entry, max(entry, c), min(entry, c)
        now = ts + 60
        tp_px = avg * (BE_MULT if be else 1 + tp_pct / 100)
        if not pess and h >= tp_px:
            return done(tp_px, ts, 'breakeven' if be else 'tp', False)
        opened_here = False
        if averaging:
            if l <= level_px:
                fill = min(level_px, o)
                qty, cost = qty + level_usdt / fill, cost + level_usdt
                avg, averaging, pos_t, opened_here = cost / qty, False, ts, True
                tp_px = avg * (1 + tp_pct / 100)
            elif l <= entry * (1 - KNIFE_PCT / 100):
                averaging, pos_t, opened_here = False, ts, True
        if not averaging:
            stop_px = avg * (1 - p['sl'] / 100)
            if l <= stop_px:
                return done(stop_px if opened_here or o >= stop_px else o, ts, 'stop', True)
        if h >= tp_px:
            return done(tp_px, ts, 'breakeven' if be else 'tp', False)
        if averaging:
            if now - t0 > GRID_HOLD:
                averaging, pos_t = False, now
            continue
        if now - pos_t > p['hard']:
            return done(c, now, 'hard_exit', True)
        if p['be'] and not be and now - pos_t > p['be']:
            be = True
            if c >= avg * BE_MULT:
                return done(c, now, 'breakeven', False)
    last = trip['bars'][-1]
    return done(last[4], last[0] + 60, 'no_data', True)


def evaluate(trips, p, pess):
    out, free_at = [], 0.0
    for t in trips:
        if t['entry_ts'] < free_at:
            continue
        r = simulate(t, p, pess)
        if r:
            free_at = r[1]
            out.append((t, *r))
    return out


def stats(pnls):
    wins, losses = [x for x in pnls if x > 0.005], [x for x in pnls if x < -0.005]
    return {'n': len(pnls), 'net': sum(pnls), 'win%': 100 * len(wins) / max(1, len(pnls)),
            'avg_win': sum(wins) / max(1, len(wins)), 'avg_loss': sum(losses) / max(1, len(losses))}


def line(label, s):
    return (f"{label:<34} n={s['n']:>3}  net={s['net']:>+6.2f}  win={s['win%']:>4.0f}%  "
            f"avg win={s['avg_win']:>+.3f}  avg loss={s['avg_loss']:>+.3f}")


def run(args):
    data = json.load(open(args.dataset, encoding='utf-8-sig'))
    trips = [t for t in data['trips'] if t['close_ts'] - t['entry_ts'] <= args.max_real_hold and t.get('bars')]
    day = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime('%d.%m')
    print(f"round trips: {len(trips)} of {len(data['trips'])} ({day(trips[0]['entry_ts'])}..{day(trips[-1]['entry_ts'])} UTC), "
          f"modes {dict(Counter(t.get('mode') for t in trips))}, "
          f"entry outside its 1m bar: {sum(1 for t in trips if not any(b[0] <= t['entry_ts'] < b[0] + 60 and b[3] * 0.999 <= t['entry_price'] <= b[2] * 1.001 for b in t['bars']))}")

    print("\n== calibration: the model with the live parameters vs what really happened ==")
    print(line("real (exchange fills)", stats([t['pnl'] for t in trips])))
    for pess in (True, False):
        res = evaluate(trips, AS_IS, pess)
        print(line(f"model as-is, {'pessimistic' if pess else 'optimistic'}", stats([r[1] for r in res])),
              dict(Counter(r[3] for r in res)))
        same = sum(1 for r in res if (r[1] > 0.005) == (r[0]['pnl'] > 0.005))
        print(f"   same win/not-win verdict as reality: {same}/{len(res)}; level 1 filled: "
              f"model {sum(1 for r in res if r[4])} vs real {sum(1 for t in trips if t['buy_orders'] > 1)}")

    tps, sls, corner = (None, 0.5, 0.8, 1.0, 1.5, 2.0), (0.8, 1.2, 2.0, 3.0), 'TP / stop'
    families = [('grid level 1, breakeven 15m, hard exit 30m (live)', dict(levels=1, be=900, hard=1800)),
                ('no grid, breakeven 15m, hard exit 30m', dict(levels=0, be=900, hard=1800)),
                ('no grid, no breakeven, hard exit 30m', dict(levels=0, be=0, hard=1800)),
                ('no grid, no breakeven, hard exit 60m', dict(levels=0, be=0, hard=3600)),
                ('no grid, no breakeven, hard exit 120m', dict(levels=0, be=0, hard=7200)),
                ('grid level 1, no breakeven, hard exit 60m', dict(levels=1, be=0, hard=3600))]
    ranked = []
    for title, fam in families:
        print(f"\n== {title}: net USDT pessimistic / optimistic (trades, win% pessimistic) ==")
        print(f"{corner:<10}" + ''.join(f"{str(sl) + '%':>26}" for sl in sls))
        for tp in tps:
            cells = []
            for sl in sls:
                p = dict(fam, tp=tp, sl=sl)
                rp, ro = evaluate(trips, p, True), evaluate(trips, p, False)
                sp, so = stats([r[1] for r in rp]), stats([r[1] for r in ro])
                cells.append(f"{sp['net']:>+6.2f} /{so['net']:>+6.2f} ({sp['n']:>3},{sp['win%']:>3.0f}%)")
                ranked.append((sp['net'], so['net'], title, tp, sl, rp))
            print(f"{'mode' if tp is None else str(tp) + '%':<10}" + ''.join(f"{c:>26}" for c in cells))

    print("\n== ten best by the pessimistic bound: is the result stable across the two halves and the days? ==")
    mid = trips[len(trips) // 2]['entry_ts']
    for net, opt, title, tp, sl, res in sorted(ranked, key=lambda r: -r[0])[:10]:
        days = defaultdict(float)
        for r in res:
            days[day(r[0]['entry_ts'])] += r[1]
        h1 = sum(r[1] for r in res if r[0]['entry_ts'] < mid)
        print(f"  {net:>+6.2f} /{opt:>+6.2f}  TP {'mode' if tp is None else tp} stop {sl} | {title} | halves {h1:>+.2f} / {net - h1:>+.2f} | "
              f"positive days {sum(1 for v in days.values() if v > 0)}/{len(days)} | {dict(Counter(r[3] for r in res))}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('fetch')
    f.add_argument('--since', type=float, required=True, help='epoch: first entry')
    f.add_argument('--until', type=float, required=True, help='epoch: last entry')
    f.add_argument('--db', default='/app/shared/state/trades.db')
    f.add_argument('--config', default='/app/shared/config.json')
    r = sub.add_parser('run')
    r.add_argument('dataset')
    r.add_argument('--max-real-hold', type=float, default=4 * 3600,
                   help='drop round trips really held longer than this (bot was down), seconds')
    args = ap.parse_args()
    fetch(args) if args.cmd == 'fetch' else run(args)


if __name__ == '__main__':
    main()
