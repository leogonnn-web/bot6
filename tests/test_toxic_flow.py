"""Tests for ToxicFlowFilter.

Uses a FakeWS that returns canned `get_trade_stats` results, so the filter
logic is exercised in isolation from any WebSocket / network code.
"""
from __future__ import annotations

import os
import sys
from typing import Dict

import pytest

# Load `toxic_flow` directly by file path. Going through `core/__init__.py`
# would import the full TradingBot stack (database, exchange, etc.), which
# is unnecessary for this unit and would force a heavy test environment.
import importlib.util
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_spec = importlib.util.spec_from_file_location(
    "toxic_flow", os.path.join(ROOT, 'src', 'core', 'toxic_flow.py'),
)
_mod = importlib.util.module_from_spec(_spec)
# Register before exec so dataclass decorator can resolve its module.
sys.modules["toxic_flow"] = _mod
_spec.loader.exec_module(_mod)
ToxicFlowFilter = _mod.ToxicFlowFilter
DEFAULTS = _mod.DEFAULTS


class FakeClock:
    def __init__(self, t0: float = 1_000_000.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeWS:
    """Records canned trade-stats responses per symbol."""

    def __init__(self):
        self._stats: Dict[str, Dict] = {}

    def set(self, symbol: str, **fields) -> None:
        base = {
            'count': 0, 'sell_count': 0, 'buy_count': 0,
            'sell_pct': 0.0, 'buy_pct': 0.0, 'consec_down': 0,
            'last_size': 0.0, 'last_side': '',
            'ema_size': None, 'size_ratio': 1.0,
            'total_seen': 0,
        }
        base.update(fields)
        self._stats[symbol] = base

    def get_trade_stats(self, symbol: str, window_sec: float = 3.0) -> Dict:
        return dict(self._stats.get(symbol, {
            'count': 0, 'sell_count': 0, 'buy_count': 0,
            'sell_pct': 0.0, 'buy_pct': 0.0, 'consec_down': 0,
            'last_size': 0.0, 'last_side': '',
            'ema_size': None, 'size_ratio': 1.0,
            'total_seen': 0,
        }))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_sweep_triggers_lock():
    ws = FakeWS()
    clk = FakeClock()
    tox = ToxicFlowFilter(ws, clock=clk)
    sym = 'X/USDT'
    # 100% sells, 12 trades, 5 down-ticks → trigger.
    ws.set(sym, count=12, sell_count=12, buy_count=0,
           sell_pct=1.0, buy_pct=0.0, consec_down=5, total_seen=500)
    assert tox.is_toxic(sym) is True
    state = tox.get_state(sym)
    assert state['is_toxic'] is True
    assert state['last_trigger_reason'].startswith('sweep')
    # Cooldown ~10 min from now.
    assert abs(state['lock_until_ts'] - (clk.t + DEFAULTS['cooldown_sec'])) < 0.01


def test_sweep_does_not_trigger_on_low_volume():
    ws = FakeWS()
    clk = FakeClock()
    tox = ToxicFlowFilter(ws, clock=clk)
    sym = 'X/USDT'
    # All 5 trades are sells, but count<min_trades → noise, NOT toxic.
    ws.set(sym, count=5, sell_count=5,
           sell_pct=1.0, buy_pct=0.0, consec_down=4, total_seen=500)
    assert tox.is_toxic(sym) is False


def test_sweep_does_not_trigger_on_mixed_flow():
    ws = FakeWS()
    clk = FakeClock()
    tox = ToxicFlowFilter(ws, clock=clk)
    sym = 'X/USDT'
    # 60% sells, 40% buys — clearly not a sweep.
    ws.set(sym, count=20, sell_count=12, buy_count=8,
           sell_pct=0.6, buy_pct=0.4, consec_down=2, total_seen=500)
    assert tox.is_toxic(sym) is False


def test_large_print_triggers():
    ws = FakeWS()
    clk = FakeClock()
    tox = ToxicFlowFilter(ws, clock=clk)
    sym = 'X/USDT'
    # No sweep conditions, but one huge sell print 7x EMA → trigger.
    ws.set(sym, count=3, sell_count=2, buy_count=1,
           sell_pct=2/3, buy_pct=1/3, consec_down=1,
           last_side='Sell', last_size=7.0,
           ema_size=1.0, size_ratio=7.0, total_seen=500)
    assert tox.is_toxic(sym) is True
    assert tox.get_state(sym)['last_trigger_reason'].startswith('large_print')


def test_large_print_does_not_trigger_before_warmup():
    ws = FakeWS()
    clk = FakeClock()
    tox = ToxicFlowFilter(ws, clock=clk)
    sym = 'X/USDT'
    # Huge ratio, but total_seen < warmup → ignore.
    ws.set(sym, count=2, sell_count=1, buy_count=1,
           sell_pct=0.5, buy_pct=0.5,
           last_side='Sell', last_size=7.0, ema_size=1.0, size_ratio=7.0,
           total_seen=10)   # << DEFAULTS['large_print_warmup'] = 100
    assert tox.is_toxic(sym) is False


def test_cooldown_blocks_subsequent_calls():
    ws = FakeWS()
    clk = FakeClock()
    tox = ToxicFlowFilter(ws, clock=clk)
    sym = 'X/USDT'
    ws.set(sym, count=12, sell_count=12, sell_pct=1.0,
           consec_down=5, total_seen=500)
    assert tox.is_toxic(sym) is True

    # Even if flow now looks calm, we stay locked until cooldown expires.
    ws.set(sym, count=2, sell_count=1, buy_count=1,
           sell_pct=0.5, buy_pct=0.5, consec_down=0, total_seen=500)
    clk.advance(60)   # 1 minute later
    assert tox.is_toxic(sym) is True


def test_unlock_after_cooldown_with_calm_flow():
    ws = FakeWS()
    clk = FakeClock()
    tox = ToxicFlowFilter(ws, clock=clk)
    sym = 'X/USDT'
    ws.set(sym, count=12, sell_count=12, sell_pct=1.0,
           consec_down=5, total_seen=500)
    assert tox.is_toxic(sym) is True

    # 10 min later, flow is back to normal (few trades, balanced).
    ws.set(sym, count=4, sell_count=2, buy_count=2,
           sell_pct=0.5, buy_pct=0.5, consec_down=0, total_seen=600)
    clk.advance(DEFAULTS['cooldown_sec'] + 1)
    assert tox.is_toxic(sym) is False
    assert tox.get_state(sym)['is_toxic'] is False


def test_no_unlock_if_storm_still_active():
    ws = FakeWS()
    clk = FakeClock()
    tox = ToxicFlowFilter(ws, clock=clk)
    sym = 'X/USDT'
    ws.set(sym, count=12, sell_count=12, sell_pct=1.0,
           consec_down=5, total_seen=500)
    assert tox.is_toxic(sym) is True

    # Cooldown elapsed but the sweep is still ongoing → lock should extend.
    clk.advance(DEFAULTS['cooldown_sec'] + 1)
    ws.set(sym, count=15, sell_count=15, sell_pct=1.0,
           consec_down=6, total_seen=900)
    assert tox.is_toxic(sym) is True
    # And a fresh lock_until_ts should have been set.
    assert tox.get_state(sym)['lock_until_ts'] >= clk.t + DEFAULTS['cooldown_sec'] - 1


def test_block_count_increments_per_blocked_call():
    ws = FakeWS()
    clk = FakeClock()
    tox = ToxicFlowFilter(ws, clock=clk)
    sym = 'X/USDT'
    ws.set(sym, count=12, sell_count=12, sell_pct=1.0,
           consec_down=5, total_seen=500)
    for _ in range(5):
        assert tox.is_toxic(sym) is True
    assert tox.get_state(sym)['block_count'] >= 5


def test_different_symbols_have_independent_state():
    ws = FakeWS()
    clk = FakeClock()
    tox = ToxicFlowFilter(ws, clock=clk)
    a, b = 'A/USDT', 'B/USDT'
    ws.set(a, count=12, sell_count=12, sell_pct=1.0,
           consec_down=5, total_seen=500)
    ws.set(b, count=20, sell_count=8, buy_count=12,
           sell_pct=0.4, buy_pct=0.6, consec_down=1, total_seen=500)
    assert tox.is_toxic(a) is True
    assert tox.is_toxic(b) is False
