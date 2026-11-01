"""Pure, stateless features computed from the 1m OHLCV the scanner already holds.

TZ-21 (B-32): idle candles. TZ-16 will add dump_z / vol_decay / higher_low here.
"""
from typing import List, Sequence

IDLE_BARS_SHORT_HISTORY = 60   # value reported when there are too few closed bars


def is_idle_bar(bar: Sequence) -> bool:
    """A 1m bar nobody traded: zero volume, or high == low (one print or none).

    Bybit returns a kline for every minute; a minute without trades comes back
    flat (O=H=L=C=previous close, volume 0). On the 33 real demo entries checked
    on 2026-11-01 the extra criterion "open == close with tiny volume" added
    nothing beyond these two, so it is not used.
    """
    try:
        high, low, volume = float(bar[2]), float(bar[3]), float(bar[5])
    except (IndexError, TypeError, ValueError):
        return True
    return volume <= 0 or high == low


def idle_bar_count(ohlcv: List[Sequence], min_bars: int = 50) -> int:
    """Idle bars among the *closed* candles (the last, still-forming bar is
    dropped: in its first seconds it is always empty).

    Fewer than `min_bars` closed bars (new listing / truncated history) counts
    as fully idle: the symbol has no hour of history to judge liquidity by.
    """
    closed = list(ohlcv[:-1]) if ohlcv else []
    if len(closed) < min_bars:
        return IDLE_BARS_SHORT_HISTORY
    return sum(1 for b in closed if is_idle_bar(b))
