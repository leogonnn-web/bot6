"""Unit tests for src/core/dispatcher.py — score-based selection."""

import pytest
from src.core.dispatcher import HydraDispatcher, SymbolScore, GridParams


class TestScoreCalculation:
    def test_perfect_score(self):
        d = HydraDispatcher()
        score = d.calculate_score(
            confidence=100, rvol_spike=5.0, dump_depth=8.0,
            obi_skew=1.0, btc_1h=0.0,
        )
        # Phase 1: obi_skew weight = 0.0 (OBI is observation-only).
        # dump=8% -> sigmoid ~0.92; conf=1.0 + rvol=1.0 + dump=0.92 + btc_ok=0.5
        assert score == pytest.approx(3.42, rel=1e-2)

    def test_zero_score(self):
        d = HydraDispatcher()
        score = d.calculate_score(
            confidence=0, rvol_spike=0, dump_depth=0,
            obi_skew=-1.0, btc_1h=-3.0,
        )
        # btc_1h < -2 => btc_ok = 0, other norms ≈ 0
        # dump=0 -> sigmoid ~0.04, so score ≈ 0.04 (not exactly 0)
        assert score == pytest.approx(0.04, abs=0.02)

    def test_btc_penalty(self):
        d = HydraDispatcher()
        ok = d.calculate_score(100, 5.0, 8.0, 1.0, 0.5)
        half = d.calculate_score(100, 5.0, 8.0, 1.0, -1.5)
        bad = d.calculate_score(100, 5.0, 8.0, 1.0, -2.5)
        assert ok > half > bad
        # Phase 1: obi weight = 0.0. btc_ok=0 -> 1.0 + 1.0 + 0.92 + 0 = 2.92
        assert bad == pytest.approx(2.92, rel=1e-2)  # btc_ok=0


class TestModeSelection:
    def test_red_light_on_btc_crash(self):
        d = HydraDispatcher()
        assert d.select_mode(score=5.0, btc_1h=-2.5) == "red_light"

    def test_aggressive_conditions(self):
        d = HydraDispatcher()
        assert d.select_mode(score=3.0, btc_1h=0.0) == "aggressive"

    def test_normal_conditions(self):
        d = HydraDispatcher()
        assert d.select_mode(score=2.0, btc_1h=0.0) == "normal"

    def test_conservative_fallback(self):
        d = HydraDispatcher()
        assert d.select_mode(score=1.0, btc_1h=0.0) == "conservative"


class TestPickBest:
    def test_picks_highest_score(self):
        d = HydraDispatcher()
        candidates = [
            {"symbol": "PEPE/USDT", "confidence": 30, "rvol_spike": 2.0,
             "rvol_local": 1.8, "dump_depth": 1.0, "obi_skew": 0.5, "btc_1h": 0.0},
            {"symbol": "SOL/USDT", "confidence": 80, "rvol_spike": 4.0,
             "rvol_local": 3.0, "dump_depth": 8.0, "obi_skew": 0.8, "btc_1h": 0.0},
        ]
        best = d.pick_best(candidates)
        assert best is not None
        assert best.symbol == "SOL/USDT"
        # dump=8% -> sigmoid ~0.92, score ~3.82 -> aggressive
        assert best.mode == "aggressive"

    def test_red_light_excluded_when_others_available(self):
        d = HydraDispatcher()
        candidates = [
            {"symbol": "BTC/USDT", "confidence": 100, "rvol_spike": 5.0,
             "rvol_local": 3.0, "dump_depth": 2.0, "obi_skew": 1.0, "btc_1h": -3.0},
            {"symbol": "ETH/USDT", "confidence": 50, "rvol_spike": 2.0,
             "rvol_local": 1.6, "dump_depth": 1.0, "obi_skew": 0.0, "btc_1h": -3.0},
        ]
        best = d.pick_best(candidates)
        assert best is not None
        # Both red_light because btc_1h < -2, but ETH has lower score
        # If ALL are red_light, pick highest score anyway
        assert best.symbol == "BTC/USDT"

    def test_empty_pool(self):
        d = HydraDispatcher()
        assert d.pick_best([]) is None


class TestGridParams:
    def test_aggressive_params(self):
        d = HydraDispatcher()
        p = d.get_grid_params("aggressive")
        assert p.grid_distance_pct == 0.30
        assert p.take_profit_pct == 1.5
        assert p.max_grid_levels == 3
        assert p.slot_multiplier == 1.2

    def test_panic_grid_params(self):
        d = HydraDispatcher()
        p = d.get_grid_params("panic_grid")
        assert p.grid_distance_pct == 1.5
        assert p.take_profit_pct == 0.6
        assert p.max_grid_levels == 2
        assert p.slot_multiplier == 0.5

    def test_unknown_mode_defaults_to_conservative(self):
        d = HydraDispatcher()
        p = d.get_grid_params("garbage")
        assert p == d.MODE_PARAMS["conservative"]


class TestSigmoidDump:
    def test_noise_is_low(self):
        d = HydraDispatcher()
        score = d.calculate_score(
            confidence=30, rvol_spike=1.0, dump_depth=1.5,
            obi_skew=0.0, btc_1h=0.0,
        )
        # dump=1.5% -> sigmoid ~0.11, weak confidence -> score should be low
        assert score < 1.5

    def test_strong_dump_is_high(self):
        d = HydraDispatcher()
        score = d.calculate_score(
            confidence=50, rvol_spike=2.0, dump_depth=8.0,
            obi_skew=0.0, btc_1h=0.0,
        )
        # Phase 1: obi weight = 0.0. dump=8% -> sigmoid ~0.92
        # conf=0.5 + rvol=0.4 + dump=0.92 + btc_ok=0.5 = ~2.32
        assert score > 2.0

    def test_sigmoid_inflection_at_4_5(self):
        d = HydraDispatcher()
        s_4 = d.calculate_score(50, 2.0, 4.0, 0.0, 0.0)
        s_5 = d.calculate_score(50, 2.0, 5.0, 0.0, 0.0)
        s_6 = d.calculate_score(50, 2.0, 6.0, 0.0, 0.0)
        # Crossing the inflection point should show clear growth
        assert s_4 < s_5 < s_6


class TestFeedbackLoop:
    def test_weight_increase_on_profit(self):
        d = HydraDispatcher(weights={"confidence": 1.0})
        d.update_weights({"confidence": 100}, profit=0.5,
                          take_profit_pct=0.8, learning_rate=0.1)
        assert d.weights["confidence"] > 1.0

    def test_weight_decrease_on_loss(self):
        d = HydraDispatcher(weights={"confidence": 1.0})
        d.update_weights({"confidence": 100}, profit=-0.5,
                          take_profit_pct=0.8, learning_rate=0.1)
        assert d.weights["confidence"] < 1.0

    def test_weight_clamped(self):
        d = HydraDispatcher(weights={"confidence": 3.9})
        d.update_weights({"confidence": 100}, profit=5.0,
                          take_profit_pct=0.5, learning_rate=1.0)
        # Upper clamp is now 4.0
        assert d.weights["confidence"] == pytest.approx(4.0, abs=1e-6)
