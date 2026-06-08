"""
ToxicFlowFilter — adversarial-flow detection for trade entry gating.

Reads per-symbol trade aggregates from the WebSocket listener (see
`bybit_client.WebSocketListener.get_trade_stats`) and decides whether
a symbol is currently 'toxic' (i.e. dominated by aggressive sell-side
flow or unusually large prints), in which case primary entries should
be blocked for a cooldown period.

This module is pure decision logic. It does NOT subscribe to data, does
NOT place or cancel orders, and is thread-safe.

Integration contract:
  tox = ToxicFlowFilter(ws_listener)
  if tox.is_toxic(symbol):
      # skip entry for this symbol
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger('HYDRA')


# ---------------------------------------------------------------------------
# Default detector parameters (tuned conservatively per the design review)
# ---------------------------------------------------------------------------
DEFAULTS = {
    # Aggressive sweep
    'sweep_window_sec':        3.0,
    'sweep_min_trades':        10,        # ignore noise on illiquid pairs
    'sweep_sell_pct_min':      0.85,      # >=85% taker-sell
    'sweep_buy_pct_max':       0.10,      # <10% taker-buy  (looser than spec's 0%)
    'sweep_consec_down_min':   3,         # >=3 consecutive down-ticks
    # Large print
    'large_print_size_ratio':  5.0,       # last_size >= 5x EMA
    'large_print_warmup':      100,       # need >=100 trades for EMA stability
    # Cooldown / de-escalation
    'cooldown_sec':            600.0,     # 10 minutes hard lock
    'unlock_max_trades':       30,        # de-escalate when activity drops to <=30 trades/window
    # OBI gate placeholder — disabled until orderbook subscription lands
    'require_obi_unlock':      False,
}


@dataclass
class _SymbolState:
    """Per-symbol filter state."""
    is_toxic: bool = False
    lock_until_ts: float = 0.0
    last_trigger_reason: str = ''
    last_trigger_ts: float = 0.0
    block_count: int = 0


class ToxicFlowFilter:
    """Heuristic toxic-flow gate.

    Parameters
    ----------
    ws_listener : object exposing get_trade_stats(symbol, window_sec) -> dict
        The price/trade aggregator (usually `bybit_client.WebSocketListener`).
        Passed in by reference; the filter never mutates it.
    config : Optional[dict]
        Overrides for DEFAULTS. Unknown keys are ignored.
    clock : Optional[callable]
        Returns current time in seconds. Defaults to `time.time`. Injectable
        for deterministic tests.
    """

    def __init__(self, ws_listener, config: Optional[dict] = None,
                 clock=None):
        self._ws = ws_listener
        self._clock = clock or time.time
        self._cfg = dict(DEFAULTS)
        if config:
            for k, v in config.items():
                if k in self._cfg:
                    self._cfg[k] = v
        self._state: Dict[str, _SymbolState] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_toxic(self, symbol: str) -> bool:
        """Evaluate the filter for `symbol`. Side-effects: may update or
        clear internal lock state for this symbol. Always re-evaluates on
        every call (no caching beyond the lock window itself).
        """
        now = self._clock()
        state = self._get_or_create_state(symbol)

        with self._lock:
            # 1. If already locked, attempt de-escalation when cooldown expires.
            if state.is_toxic:
                if now < state.lock_until_ts:
                    state.block_count += 1
                    return True
                # Cooldown elapsed; check de-escalation conditions.
                if self._can_unlock(symbol):
                    state.is_toxic = False
                    state.last_trigger_reason = ''
                    return False
                # Still hot — extend lock by another cooldown window.
                state.lock_until_ts = now + self._cfg['cooldown_sec']
                state.block_count += 1
                return True

            # 2. Not currently locked. Run detectors.
            reason = self._evaluate(symbol)
            if reason:
                state.is_toxic = True
                state.lock_until_ts = now + self._cfg['cooldown_sec']
                state.last_trigger_reason = reason
                state.last_trigger_ts = now
                state.block_count += 1
                # Single WARNING log per storm (state 0→1 transition).
                # Repeat blocks during the cooldown stay silent at INFO; the
                # caller may DEBUG-log them if needed.
                logger.warning(
                    f"@TOXIC_TRIGGER@ {symbol} {reason} "
                    f"(locked for {self._cfg['cooldown_sec']:.0f}s)"
                )
                return True
            return False

    def get_state(self, symbol: str) -> Dict:
        """Snapshot of current per-symbol filter state (for metrics/diag)."""
        state = self._get_or_create_state(symbol)
        with self._lock:
            return {
                'symbol': symbol,
                'is_toxic': state.is_toxic,
                'lock_until_ts': state.lock_until_ts,
                'last_trigger_reason': state.last_trigger_reason,
                'last_trigger_ts': state.last_trigger_ts,
                'block_count': state.block_count,
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _get_or_create_state(self, symbol: str) -> _SymbolState:
        with self._lock:
            s = self._state.get(symbol)
            if s is None:
                s = _SymbolState()
                self._state[symbol] = s
            return s

    def _evaluate(self, symbol: str) -> str:
        """Run sweep + large-print detectors. Returns reason string if
        any of them triggered, else empty string.
        """
        cfg = self._cfg
        stats = self._ws.get_trade_stats(symbol, window_sec=cfg['sweep_window_sec'])
        if not stats or stats.get('count', 0) == 0:
            return ''

        # --- Aggressive sweep ---
        if (stats['count'] >= cfg['sweep_min_trades']
                and stats['sell_pct'] >= cfg['sweep_sell_pct_min']
                and stats['buy_pct'] <= cfg['sweep_buy_pct_max']
                and stats['consec_down'] >= cfg['sweep_consec_down_min']):
            return (f"sweep: sell={stats['sell_pct']*100:.0f}% "
                    f"buy={stats['buy_pct']*100:.0f}% "
                    f"down_ticks={stats['consec_down']} "
                    f"trades={stats['count']}")

        # --- Large print (sell-side only) ---
        if (stats['total_seen'] >= cfg['large_print_warmup']
                and stats['last_side'] == 'Sell'
                and stats['size_ratio'] >= cfg['large_print_size_ratio']):
            return (f"large_print: size_ratio={stats['size_ratio']:.1f}x "
                    f"last_size={stats['last_size']:.4f}")

        return ''

    def _can_unlock(self, symbol: str) -> bool:
        """Decide whether de-escalation conditions are met.

        Currently: trade activity has dropped to <= unlock_max_trades in
        the recent window, indicating the storm has passed. The OBI
        condition from the spec is a TODO (requires orderbook subscription).
        """
        cfg = self._cfg
        stats = self._ws.get_trade_stats(symbol, window_sec=cfg['sweep_window_sec'])
        if stats.get('count', 0) > cfg['unlock_max_trades']:
            return False
        # If sweep conditions still partially hold (sell dominant), keep lock.
        if (stats.get('sell_pct', 0) >= cfg['sweep_sell_pct_min']
                and stats.get('count', 0) >= cfg['sweep_min_trades']):
            return False
        # OBI placeholder: when implemented, require obi_top5 >= 0.2 here.
        if cfg['require_obi_unlock']:
            obi = getattr(self._ws, 'get_obi', lambda _s: None)(symbol)
            if obi is None or obi < 0.2:
                return False
        return True
