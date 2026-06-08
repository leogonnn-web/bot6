"""Hydra Dispatcher — Context-AI Risk Manager (score-based prototype).

Selects the best symbol from the active pool and sets grid aggressiveness
based on real-time microstructure signals.

Phase 1 (now): static weights, no feedback loop.
Phase 2 (later): Widrow-Hoff delta after each trade.
"""

import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class SymbolScore:
    symbol: str
    score: float
    confidence: float
    rvol_spike: float
    rvol_local: float
    dump_depth: float
    obi_skew: float
    btc_1h: float
    mode: str


@dataclass
class GridParams:
    grid_distance_pct: float
    take_profit_pct: float
    max_grid_levels: int
    min_confidence: float
    slot_multiplier: float


class HydraDispatcher:
    """Score-based symbol selector and grid parameter tuner.

    Updated with sigmoidal dump tracking and score-driven mode selection.
    """

    DEFAULT_WEIGHTS = {
        "confidence": 1.0,
        "rvol_spike": 1.0,
        "dump_depth": 1.0,
        "obi_skew": 0.0,  # Phase 1: pure data collection, OBI does not affect score
        "btc_ok": 0.5,
    }

    MODE_PARAMS = {
        "red_light":    GridParams(0.0,   0.0,  0, 100.0, 0.0),
        "panic_grid":   GridParams(1.5,   0.6,  2, 5.0,   0.5),
        "aggressive":   GridParams(0.30,  1.5,  3, 10.0,  1.2),
        "normal":       GridParams(0.50,  0.8,  3, 15.0,  1.0),
        "conservative": GridParams(0.80,  0.5,  3, 25.0,  0.8),
    }

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = weights or dict(self.DEFAULT_WEIGHTS)

    # ------------------------------------------------------------------
    # 1. Score calculation
    # ------------------------------------------------------------------
    def calculate_score(
        self,
        confidence: float,
        rvol_spike: float,
        dump_depth: float,
        obi_skew: float,
        btc_1h: float,
    ) -> float:
        """Normalized score 0..~5. Higher is better."""
        c_norm = _clamp(confidence / 100.0, 0.0, 1.0)
        r_norm = _clamp(min(rvol_spike, 5.0) / 5.0, 0.0, 1.0)

        # Calibrated sigmoid for meme-coin dumps (centre at 4.5%)
        d_norm = 1.0 / (1.0 + math.exp(-0.7 * (dump_depth - 4.5)))
        d_norm = _clamp(d_norm, 0.0, 1.0)

        o_norm = _clamp((obi_skew + 1.0) / 2.0, 0.0, 1.0)

        if btc_1h < -2.0:
            btc_ok = 0.0
        elif btc_1h < -1.0:
            btc_ok = 0.5
        else:
            btc_ok = 1.0

        w = self.weights
        score = (
            w.get("confidence", 1.0) * c_norm +
            w.get("rvol_spike", 1.0) * r_norm +
            w.get("dump_depth", 1.0) * d_norm +
            w.get("obi_skew", 0.5) * o_norm +
            w.get("btc_ok", 0.5) * btc_ok
        )
        return score

    # ------------------------------------------------------------------
    # 2. Mode selection
    # ------------------------------------------------------------------
    def select_mode(self, score: float, btc_1h: float) -> str:
        if btc_1h < -2.0:
            return "red_light"

        if score >= 3.0:
            return "aggressive"
        elif score >= 2.0:
            return "normal"
        elif score >= 1.0:
            return "conservative"

        return "conservative"

    # ------------------------------------------------------------------
    # 3. Top symbol pick
    # ------------------------------------------------------------------
    def pick_best(
        self,
        candidates: List[Dict[str, float]],
    ) -> Optional[SymbolScore]:
        """Pick best symbol from list of candidate dicts.

        Each candidate dict must contain keys:
          symbol, confidence, rvol_spike, rvol_local, dump_depth, obi_skew, btc_1h
        """
        if not candidates:
            return None

        scored = []
        for c in candidates:
            sym = c["symbol"]
            conf = float(c.get("confidence", 0))
            rvol_s = float(c.get("rvol_spike", 0))
            rvol_l = float(c.get("rvol_local", 0))
            dump = float(c.get("dump_depth", 0))
            obi = float(c.get("obi_skew", 0))
            btc = float(c.get("btc_1h", 0))

            score = self.calculate_score(conf, rvol_s, dump, obi, btc)
            mode = self.select_mode(score, btc)
            scored.append(SymbolScore(
                symbol=sym,
                score=score,
                confidence=conf,
                rvol_spike=rvol_s,
                rvol_local=rvol_l,
                dump_depth=dump,
                obi_skew=obi,
                btc_1h=btc,
                mode=mode,
            ))

        # Exclude red_light unless every candidate is red
        non_red = [s for s in scored if s.mode != "red_light"]
        pool = non_red if non_red else scored

        best = max(pool, key=lambda x: x.score)
        return best

    # ------------------------------------------------------------------
    # 4. Grid params for mode
    # ------------------------------------------------------------------
    def get_grid_params(self, mode: str) -> GridParams:
        return self.MODE_PARAMS.get(mode, self.MODE_PARAMS["conservative"])

    def get_min_score(self, btc_change_1h: float, dynamic_config: dict = None) -> float:
        """Dynamic entry threshold based on BTC health.

        Args:
            btc_change_1h: BTC 1h change percentage
            dynamic_config: Optional override dict with keys:
                base, btc_bearish_penalty, btc_crash_penalty, flet_bonus

        Returns:
            Minimum dispatcher score required for entry
        """
        if not dynamic_config:
            # Default hardcoded values; bot.py injects config from JSON
            dynamic_config = {
                "base": 1.0,
                "btc_bearish_penalty": 0.5,
                "btc_crash_penalty": 1.0,
                "flet_bonus": -0.3,
            }

        base = float(dynamic_config.get("base", 1.0))
        penalty_bearish = float(dynamic_config.get("btc_bearish_penalty", 0.5))
        penalty_crash = float(dynamic_config.get("btc_crash_penalty", 1.0))
        bonus_flet = float(dynamic_config.get("flet_bonus", -0.3))

        if btc_change_1h < -2.0:
            # BTC crash — only highest conviction entries
            return base + penalty_crash
        elif btc_change_1h < -0.8:
            # BTC bearish — tighten threshold
            return base + penalty_bearish
        elif -0.8 <= btc_change_1h <= 0.8:
            # Flet — relax threshold for more entries
            return max(0.3, base + bonus_flet)
        else:
            # BTC bullish — normal threshold
            return base

    # ------------------------------------------------------------------
    # 5. Feedback loop (Phase 2)
    # ------------------------------------------------------------------
    def update_weights(
        self,
        features: Dict[str, float],
        profit: float,
        take_profit_pct: float,
        learning_rate: float = 0.02,
    ) -> None:
        """Widrow-Hoff delta rule. Call after each closed trade."""
        if take_profit_pct <= 0:
            return

        # Soft proportional error instead of binary ±1
        error = profit / take_profit_pct

        for key, value in features.items():
            if key in self.weights:
                # Normalise incoming feature to match calculate_score
                if key == "confidence":
                    f_val = value / 100.0
                elif key == "rvol_spike":
                    f_val = min(value, 5.0) / 5.0
                elif key == "dump_depth":
                    f_val = 1.0 / (1.0 + math.exp(-0.7 * (value - 4.5)))
                elif key == "obi_skew":
                    f_val = (value + 1.0) / 2.0
                else:
                    f_val = value

                delta = learning_rate * error * _clamp(f_val, 0.0, 1.0)
                # Wider guardrails against burn-out
                self.weights[key] = _clamp(
                    self.weights[key] + delta, 0.2, 4.0
                )
