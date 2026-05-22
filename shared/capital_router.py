"""
Capital Router — cap-based capital distribution + Bootstrap $15 Trap guard.

Responsibilities:
  1. Read total USDT balance → decide allocation per bot (Sniper/Hydra/Arb).
  2. Enforce minimum-balance thresholds: if free capital < $25, Martingale
     grid is DISABLED and Hydra falls back to single-shot mode.
  3. Write atomic capital_state.json for inter-bot communication.
  4. Hysteresis: state changes only when balance crosses thresholds by ≥$2
     to avoid flip-flopping on boundary.

Thresholds (agreed with user):
  $15  → single-shot only, grid DISABLED
  $25  → grid ENABLED (1 level max)
  $50  → grid 2 levels
  $100 → grid 3 levels (full Martingale)
  $200+→ unlock Arb allocation

Reserve: 5% of total balance is never allocated.
"""

from __future__ import annotations

import json
import os
import time
import tempfile
from dataclasses import dataclass, field
from typing import Optional

# Lazy logger — module works even without shared/logger_setup.py
try:
    from logger_setup import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RESERVE_PCT = 0.05
HYSTERESIS_USD = 2.0

# Threshold → max grid levels allowed
GRID_THRESHOLDS = [
    (100.0, 3),   # >= $100 → 3 levels
    (50.0, 2),    # >= $50  → 2 levels
    (25.0, 1),    # >= $25  → 1 level (single grid, no Martingale doubling)
]
MIN_SINGLE_SHOT = 15.0  # Absolute floor for any trading
ARB_UNLOCK = 200.0


@dataclass
class CapitalState:
    """Snapshot of capital allocation decision."""
    total_balance: float = 0.0
    available: float = 0.0          # after reserve
    reserve: float = 0.0
    grid_allowed: bool = False
    max_grid_levels: int = 0
    arb_allowed: bool = False
    mode: str = 'frozen'            # frozen | single_shot | grid_1 | grid_2 | grid_3
    slot_size: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            'total_balance': round(self.total_balance, 2),
            'available': round(self.available, 2),
            'reserve': round(self.reserve, 2),
            'grid_allowed': self.grid_allowed,
            'max_grid_levels': self.max_grid_levels,
            'arb_allowed': self.arb_allowed,
            'mode': self.mode,
            'slot_size': round(self.slot_size, 2),
            'timestamp': self.timestamp,
        }


class CapitalRouter:
    """Stateful router: call `evaluate(balance)` each cycle, read `state`."""

    def __init__(self, state_file: Optional[str] = None, min_order_usdt: float = 5.0):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        self._state_file = state_file or os.path.join(root, 'shared', 'capital_state.json')
        self._min_order = min_order_usdt
        self._prev_mode: Optional[str] = None
        self.state = CapitalState()

    # ------------------------------------------------------------------
    def evaluate(self, total_balance: float, base_slot_size: float = 12.0) -> CapitalState:
        """Core logic: balance → allocation decision with hysteresis."""
        reserve = total_balance * RESERVE_PCT
        available = total_balance - reserve

        # --- Determine grid capability ---
        grid_allowed = False
        max_levels = 0

        for threshold, levels in GRID_THRESHOLDS:
            effective_threshold = threshold
            # Hysteresis: if we were already at this level, require
            # balance to drop below threshold - HYSTERESIS before downgrade
            if self._prev_mode and self._level_for_mode(self._prev_mode) >= levels:
                effective_threshold = threshold - HYSTERESIS_USD
            if available >= effective_threshold:
                grid_allowed = True
                max_levels = levels
                break

        # --- Determine mode ---
        if available < MIN_SINGLE_SHOT:
            mode = 'frozen'
        elif not grid_allowed:
            mode = 'single_shot'
        else:
            mode = f'grid_{max_levels}'

        # --- Slot size: never exceed available / (1 + max_levels * 1.5) ---
        if mode == 'frozen':
            slot = 0.0
        elif mode == 'single_shot':
            slot = min(base_slot_size, available - self._min_order)
            slot = max(slot, self._min_order)
        else:
            # Ensure balance can sustain all grid levels
            # Level costs: base * 1.5^1 + base * 1.5^2 + ... + base * 1.5^max_levels
            total_grid_cost = sum(base_slot_size * (1.5 ** i) for i in range(1, max_levels + 1))
            total_needed = base_slot_size + total_grid_cost
            if available < total_needed:
                # Scale down slot to fit
                slot = available / (1 + sum(1.5 ** i for i in range(1, max_levels + 1)))
            else:
                slot = base_slot_size
            slot = max(slot, self._min_order)

        arb_allowed = available >= ARB_UNLOCK

        self.state = CapitalState(
            total_balance=total_balance,
            available=available,
            reserve=reserve,
            grid_allowed=grid_allowed,
            max_grid_levels=max_levels,
            arb_allowed=arb_allowed,
            mode=mode,
            slot_size=slot,
            timestamp=time.time(),
        )
        self._prev_mode = mode

        logger.info(
            f"@CAPITAL@ balance=${total_balance:.2f} avail=${available:.2f} "
            f"mode={mode} grid_lvl={max_levels} slot=${slot:.2f}"
        )

        self._write_state()
        return self.state

    # ------------------------------------------------------------------
    def can_use_martingale(self, desired_levels: int = 3) -> bool:
        """Quick check: can the bot deploy N grid levels right now?"""
        return self.state.grid_allowed and self.state.max_grid_levels >= desired_levels

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _write_state(self) -> None:
        """Atomic write to capital_state.json (write-tmp-then-rename)."""
        try:
            data = json.dumps(self.state.to_dict(), indent=2)
            dir_name = os.path.dirname(self._state_file)
            fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
            try:
                os.write(fd, data.encode('utf-8'))
            finally:
                os.close(fd)
            # Atomic rename (POSIX) / replace (Windows)
            os.replace(tmp, self._state_file)
        except Exception as e:
            logger.error(f"@CAPITAL_WRITE_ERROR@ {e}")

    def load_state(self) -> Optional[CapitalState]:
        """Read last persisted state (used by other bots via JSON bridge)."""
        try:
            if not os.path.isfile(self._state_file):
                return None
            with open(self._state_file, 'r', encoding='utf-8') as f:
                d = json.load(f)
            return CapitalState(**{k: v for k, v in d.items() if k in CapitalState.__dataclass_fields__})
        except Exception as e:
            logger.error(f"@CAPITAL_READ_ERROR@ {e}")
            return None

    # ------------------------------------------------------------------
    @staticmethod
    def _level_for_mode(mode: str) -> int:
        if mode.startswith('grid_'):
            try:
                return int(mode.split('_')[1])
            except (IndexError, ValueError):
                return 0
        return 0
