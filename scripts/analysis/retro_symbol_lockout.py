"""TZ-22 retro check: replay the symbol-lockout rules over real `trades`.

Read-only, SQLite only. Entry = a `buy` row; its outcome = the next closing
sell (`sell` / `sell_panic`) on the same symbol. For each entry the rules see
only closes and buys strictly before it, the same view the scanner has.

    python scripts/analysis/retro_symbol_lockout.py --db trades.db --since 1761350400
"""
import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

DEFAULTS = {
    'lockout_after_panic_min': 180,
    'lockout_losses_count': 2,
    'lockout_losses_window_min': 360,
    'lockout_losses_duration_min': 720,
    'max_entries_per_symbol_6h': 3,
}
RULES = ('panic', 'losses', 'entries')


def lock_reason(ts, closes, buys, cfg):
    """Same rules and order as ScanningStateMixin._symbol_locked, evaluated at `ts`."""
    panic_sec = cfg['lockout_after_panic_min'] * 60
    n, window, dur = (cfg['lockout_losses_count'], cfg['lockout_losses_window_min'] * 60,
                      cfg['lockout_losses_duration_min'] * 60)
    if panic_sec and any(side == 'sell_panic' and ts - panic_sec <= cts < ts for cts, side, _ in closes):
        return 'panic'
    if n and window and dur:
        losses = [cts for cts, _, p in closes if cts < ts and (p or 0.0) < 0]
        if any(losses[i] - losses[i - n + 1] <= window and losses[i] + dur > ts
               for i in range(n - 1, len(losses))):
            return 'losses'
    cap = cfg['max_entries_per_symbol_6h']
    if cap and sum(1 for bts in buys if ts - 6 * 3600 <= bts < ts) >= cap:
        return 'entries'
    return None


def load(db, since):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT symbol, side, timestamp, profit FROM trades "
        "WHERE side IN ('buy', 'sell', 'sell_panic') ORDER BY timestamp").fetchall()
    conn.close()
    by_sym = defaultdict(list)
    for sym, side, ts, profit in rows:
        by_sym[sym].append((ts, side, profit))
    entries = []
    for sym, trades in by_sym.items():
        closes = [(ts, side, p) for ts, side, p in trades if side != 'buy']
        buys = [ts for ts, side, _ in trades if side == 'buy']
        for i, (ts, side, _) in enumerate(trades):
            if side != 'buy' or ts < since:
                continue
            out = next(((cts, cside, p) for cts, cside, p in trades[i + 1:] if cside != 'buy'), None)
            entries.append({'symbol': sym, 'ts': ts, 'closes': closes, 'buys': buys,
                            'exit_side': out[1] if out else None,
                            'pnl': float(out[2] or 0.0) if out else 0.0})
    entries.sort(key=lambda e: e['ts'])
    return entries


def hhmm(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%d.%m %H:%M')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--db', required=True)
    ap.add_argument('--since', type=float, default=0.0, help='epoch; entries before it are not counted')
    for k, v in DEFAULTS.items():
        ap.add_argument(f"--{k.replace('_', '-')}", type=int, default=v)
    ap.add_argument('--symbol', help='list every entry of this symbol with its verdict')
    args = ap.parse_args()
    cfg = {k: getattr(args, k) for k in DEFAULTS}

    entries = load(args.db, args.since)
    if not entries:
        sys.exit("no buy rows in range")
    for e in entries:
        e['reason'] = lock_reason(e['ts'], e['closes'], e['buys'], cfg)

    def stats(group):
        return (len(group), sum(e['pnl'] for e in group),
                sum(1 for e in group if e['exit_side'] == 'sell_panic'),
                sum(1 for e in group if e['exit_side'] is None))

    print(f"entries since {hhmm(entries[0]['ts'])} .. {hhmm(entries[-1]['ts'])} UTC; config {cfg}")
    print("Note: blocked entries are counted as if the rest of history were unchanged "
          "(a skipped entry would not itself feed later locks).\n")
    print(f"{'group':<12}{'entries':>8}{'pnl $':>10}{'panics':>8}{'open':>6}")
    for label, group in [*((r, [e for e in entries if e['reason'] == r]) for r in RULES),
                         ('blocked', [e for e in entries if e['reason']]),
                         ('kept', [e for e in entries if not e['reason']]),
                         ('all', entries)]:
        n, pnl, panics, still_open = stats(group)
        print(f"{label:<12}{n:>8}{pnl:>10.2f}{panics:>8}{still_open:>6}")

    print("\nby symbol (top 10 by entries):")
    per = defaultdict(list)
    for e in entries:
        per[e['symbol']].append(e)
    for sym, group in sorted(per.items(), key=lambda kv: -len(kv[1]))[:10]:
        blocked = [e for e in group if e['reason']]
        print(f"  {sym:<14} entries={len(group):>3} pnl={sum(e['pnl'] for e in group):>7.2f} "
              f"blocked={len(blocked):>3} blocked_pnl={sum(e['pnl'] for e in blocked):>7.2f}")

    if args.symbol:
        print(f"\n{args.symbol}:")
        for e in per.get(args.symbol, []):
            print(f"  {hhmm(e['ts'])}Z  {e['exit_side'] or 'open':<10} {e['pnl']:>7.2f}  {e['reason'] or '-'}")


if __name__ == '__main__':
    main()
