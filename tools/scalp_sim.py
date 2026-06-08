#!/usr/bin/env python3
"""
scalp_sim.py — Standalone expectancy & compounding evaluator for the
Micro-Scalp Trigger sub-module.

Read-only tool: does NOT import the bot, does NOT touch production, does
NOT place orders. Verifies the math before any trading code is written.

Sections:
  1. Per-trade EV — net win, net loss, breakeven WR for each preset.
  2. EV matrix   — TP/SL combos × execution scenarios × win-rate sweep.
  3. 21-day Monte-Carlo compounding starting at $25.

Run:  python tools/scalp_sim.py
      python tools/scalp_sim.py --runs 5000 --slippage 0.08
"""
from __future__ import annotations

import argparse
import random
import statistics
from dataclasses import dataclass
from typing import List, Tuple


# Bybit spot V5 tier-0 fees: 0.1% maker / 0.1% taker.
# Each scenario = (description, entry_fee%, tp_exit_fee%, sl_exit_fee%).
FEE_SCENARIOS = {
    "all_taker": ("entry mkt + TP mkt + SL mkt — worst case",        0.10, 0.10, 0.10),
    "mixed":     ("entry maker + TP maker + SL taker — realistic",   0.10, 0.10, 0.10),
    "all_maker": ("entry maker + TP maker + SL maker — risky idle",  0.10, 0.10, 0.10),
}


@dataclass
class Preset:
    name: str
    tp_pct: float
    sl_pct: float


def compute_ev(tp_pct: float, sl_pct: float,
               fees_total_pct: float, slippage_pct: float) -> Tuple[float, float, float]:
    """Return (net_win%, net_loss%, breakeven_winrate)."""
    overhead = fees_total_pct + 2.0 * slippage_pct
    net_win = tp_pct - overhead
    net_loss = sl_pct + overhead
    if net_win + net_loss <= 0:
        return net_win, net_loss, float("inf")
    return net_win, net_loss, net_loss / (net_win + net_loss)


def expected_value(net_win: float, net_loss: float, wr: float) -> float:
    return wr * net_win - (1.0 - wr) * net_loss


def render_matrix(presets: List[Preset], slippage_pct: float) -> str:
    out: List[str] = []
    out.append("=" * 100)
    out.append(f"SECTION 2 - EV MATRIX  (slippage {slippage_pct:.2f}% per side, fees 0.10% x 2 = 0.20% per round-trip)")
    out.append("=" * 100)
    hdr = (f"{'preset':<22}{'scenario':<12}"
           f"{'net_win%':>10}{'net_loss%':>11}{'BE_WR%':>9}"
           f"{'EV@40%':>10}{'EV@50%':>10}{'EV@60%':>10}{'EV@70%':>10}")
    out.append(hdr)
    out.append("-" * 100)
    for p in presets:
        for scen, (_desc, ef, tp_f, sl_f) in FEE_SCENARIOS.items():
            fees_win = ef + tp_f
            fees_loss = ef + sl_f
            net_win, _, _ = compute_ev(p.tp_pct, p.sl_pct, fees_win, slippage_pct)
            _, net_loss, _ = compute_ev(p.tp_pct, p.sl_pct, fees_loss, slippage_pct)
            denom = net_win + net_loss
            be_str = f"{(net_loss/denom)*100:7.1f}" if denom > 0 else "    N/A"
            evs = [expected_value(net_win, net_loss, wr) for wr in (0.40, 0.50, 0.60, 0.70)]
            out.append(
                f"{p.name:<22}{scen:<12}"
                f"{net_win:>9.3f}%{net_loss:>10.3f}%{be_str:>9}"
                f"{evs[0]:>+9.3f}%{evs[1]:>+9.3f}%{evs[2]:>+9.3f}%{evs[3]:>+9.3f}%"
            )
        out.append("")
    return "\n".join(out)


def simulate_compounding(*, capital_0: float, days: int,
                         trades_min: int, trades_max: int,
                         position_size: float, win_rate: float,
                         net_win_pct: float, net_loss_pct: float,
                         runs: int, rng: random.Random,
                         min_capital: float = 5.0) -> List[List[float]]:
    paths: List[List[float]] = []
    for _ in range(runs):
        cap = capital_0
        traj = [cap]
        for _ in range(days):
            n_trades = rng.randint(trades_min, trades_max)
            for _ in range(n_trades):
                if cap < min_capital:
                    break
                size = min(position_size, cap)
                if rng.random() < win_rate:
                    cap += size * (net_win_pct / 100.0)
                else:
                    cap -= size * (net_loss_pct / 100.0)
                if cap < 0:
                    cap = 0.0
            traj.append(cap)
        paths.append(traj)
    return paths


def summarise(paths: List[List[float]], capital_0: float) -> dict:
    finals = sorted(p[-1] for p in paths)
    n = len(finals)
    def q(x: float) -> float:
        return finals[max(0, min(n - 1, int(x * n)))]
    return {
        "median": statistics.median(finals),
        "mean":   statistics.mean(finals),
        "p5":     q(0.05),
        "p25":    q(0.25),
        "p75":    q(0.75),
        "p95":    q(0.95),
        "above_start": sum(1 for f in finals if f > capital_0) / n,
        "doubled":     sum(1 for f in finals if f >= 2 * capital_0) / n,
        "ruined":      sum(1 for f in finals if f < 5.0) / n,
        "min":    finals[0],
        "max":    finals[-1],
    }


def render_compounding(presets: List[Preset], win_rates: List[float], *,
                       capital_0: float, days: int,
                       trades_min: int, trades_max: int,
                       position_size: float, slippage_pct: float,
                       runs: int, rng: random.Random) -> str:
    out: List[str] = []
    out.append("=" * 100)
    out.append(
        f"SECTION 3 - 21-DAY MONTE-CARLO COMPOUNDING  "
        f"(start=${capital_0:.2f}, {days}d, {trades_min}-{trades_max} trades/day, "
        f"position=${position_size:.2f}, runs={runs})"
    )
    out.append("=" * 100)
    hdr = (f"{'preset':<22}{'WR':>5}{'EV%/trd':>9}"
           f"{'median$':>10}{'mean$':>10}"
           f"{'p5$':>9}{'p25$':>9}{'p75$':>9}{'p95$':>9}"
           f"{'>start':>8}{'2x':>5}{'ruin':>6}")
    out.append(hdr)
    out.append("-" * 100)
    fees_total = 0.20  # 2 × 0.10%, identical across scenarios at tier 0
    for preset in presets:
        net_win, net_loss, _ = compute_ev(preset.tp_pct, preset.sl_pct,
                                          fees_total, slippage_pct)
        for wr in win_rates:
            ev = expected_value(net_win, net_loss, wr)
            paths = simulate_compounding(
                capital_0=capital_0, days=days,
                trades_min=trades_min, trades_max=trades_max,
                position_size=position_size, win_rate=wr,
                net_win_pct=net_win, net_loss_pct=net_loss,
                runs=runs, rng=rng,
            )
            s = summarise(paths, capital_0)
            out.append(
                f"{preset.name:<22}{wr*100:>4.0f}%{ev:>+8.3f}%"
                f"{s['median']:>9.2f}{s['mean']:>9.2f}"
                f"{s['p5']:>8.2f}{s['p25']:>8.2f}{s['p75']:>8.2f}{s['p95']:>8.2f}"
                f"{s['above_start']*100:>7.0f}%{s['doubled']*100:>4.0f}%{s['ruined']*100:>5.0f}%"
            )
        out.append("")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Scalp strategy expectancy & compounding evaluator")
    ap.add_argument("--slippage", type=float, default=0.05,
                    help="per-side slippage %% (default 0.05)")
    ap.add_argument("--capital", type=float, default=25.0)
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--trades-min", type=int, default=15)
    ap.add_argument("--trades-max", type=int, default=30)
    ap.add_argument("--position", type=float, default=15.0)
    ap.add_argument("--runs", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260524)
    args = ap.parse_args()

    rng = random.Random(args.seed)

    presets = [
        Preset("spec  TP+0.2 SL-0.4", tp_pct=0.20, sl_pct=0.40),
        Preset("rec   TP+0.6 SL-0.3", tp_pct=0.60, sl_pct=0.30),
        Preset("alt-A TP+0.5 SL-0.3", tp_pct=0.50, sl_pct=0.30),
        Preset("alt-B TP+0.4 SL-0.2", tp_pct=0.40, sl_pct=0.20),
        Preset("alt-C TP+0.3 SL-0.3", tp_pct=0.30, sl_pct=0.30),
    ]

    # SECTION 1 — per-trade economics for the two main contenders
    print("=" * 100)
    print(f"SECTION 1 - PER-TRADE ECONOMICS  (slippage {args.slippage:.2f}% per side, "
          f"round-trip fees 0.20%, total cost overhead = "
          f"{0.20 + 2*args.slippage:.2f}%)")
    print("=" * 100)
    print(f"{'preset':<22}{'TP%':>7}{'SL%':>7}"
          f"{'net_win%':>11}{'net_loss%':>11}{'BE_WR%':>9}"
          f"{'EV@50%':>10}{'EV@60%':>10}{'EV@70%':>10}")
    print("-" * 100)
    for p in presets:
        nw, nl, be = compute_ev(p.tp_pct, p.sl_pct, 0.20, args.slippage)
        be_s = f"{be*100:7.1f}" if be != float("inf") else "    N/A"
        evs = [expected_value(nw, nl, wr) for wr in (0.50, 0.60, 0.70)]
        print(f"{p.name:<22}{p.tp_pct:>6.2f}%{p.sl_pct:>6.2f}%"
              f"{nw:>10.3f}%{nl:>10.3f}%{be_s:>9}"
              f"{evs[0]:>+9.3f}%{evs[1]:>+9.3f}%{evs[2]:>+9.3f}%")
    print()

    # SECTION 2 — full matrix with execution scenarios
    print(render_matrix(presets, args.slippage))

    # SECTION 3 — Monte-Carlo compounding
    print(render_compounding(
        presets,
        win_rates=[0.40, 0.50, 0.55, 0.60, 0.65, 0.70],
        capital_0=args.capital, days=args.days,
        trades_min=args.trades_min, trades_max=args.trades_max,
        position_size=args.position, slippage_pct=args.slippage,
        runs=args.runs, rng=rng,
    ))


if __name__ == "__main__":
    main()
