"""Tests for Capital Router bootstrap-trap guard (shared/capital_router.py)."""
import json
import os
import pytest
from capital_router import CapitalRouter, CapitalState, RESERVE_PCT


class TestCapitalRouter:
    def _make(self, tmp_path):
        state_file = str(tmp_path / 'capital_state.json')
        return CapitalRouter(state_file=state_file)

    # ── Frozen state ──
    def test_balance_10_frozen(self, tmp_path):
        cr = self._make(tmp_path)
        s = cr.evaluate(10.0)
        assert s.mode == 'frozen'
        assert s.grid_allowed is False
        assert s.slot_size == 0.0

    # ── Single-shot ($15-$24) ──
    def test_balance_20_single_shot(self, tmp_path):
        cr = self._make(tmp_path)
        s = cr.evaluate(20.0)
        assert s.mode == 'single_shot'
        assert s.grid_allowed is False
        assert s.max_grid_levels == 0
        assert s.slot_size > 0

    # ── Grid 1 level ($25-$49) ──
    def test_balance_30_grid_1(self, tmp_path):
        cr = self._make(tmp_path)
        s = cr.evaluate(30.0)
        assert s.mode == 'grid_1'
        assert s.max_grid_levels == 1
        assert s.grid_allowed is True

    # ── Grid 2 levels ($50-$99) ──
    def test_balance_60_grid_2(self, tmp_path):
        cr = self._make(tmp_path)
        s = cr.evaluate(60.0)
        assert s.mode == 'grid_2'
        assert s.max_grid_levels == 2

    # ── Grid 3 levels ($100+) ──
    def test_balance_150_grid_3(self, tmp_path):
        cr = self._make(tmp_path)
        s = cr.evaluate(150.0)
        assert s.mode == 'grid_3'
        assert s.max_grid_levels == 3

    # ── Arb unlock ($200+) ──
    def test_arb_unlocked_at_250(self, tmp_path):
        cr = self._make(tmp_path)
        s = cr.evaluate(250.0)
        assert s.arb_allowed is True

    def test_arb_locked_at_100(self, tmp_path):
        cr = self._make(tmp_path)
        s = cr.evaluate(100.0)
        assert s.arb_allowed is False

    # ── Reserve ──
    def test_reserve_5pct(self, tmp_path):
        cr = self._make(tmp_path)
        s = cr.evaluate(200.0)
        assert s.reserve == pytest.approx(200.0 * RESERVE_PCT)
        assert s.available == pytest.approx(200.0 * (1 - RESERVE_PCT))

    # ── can_use_martingale shortcut ──
    def test_can_use_martingale_true(self, tmp_path):
        cr = self._make(tmp_path)
        cr.evaluate(150.0)
        assert cr.can_use_martingale(3) is True

    def test_can_use_martingale_false_low_balance(self, tmp_path):
        cr = self._make(tmp_path)
        cr.evaluate(20.0)
        assert cr.can_use_martingale(1) is False

    # ── Atomic write + read ──
    def test_state_persisted_to_json(self, tmp_path):
        cr = self._make(tmp_path)
        cr.evaluate(110.0)  # available = 104.5 >= 100 → grid_3
        assert os.path.isfile(cr._state_file)
        with open(cr._state_file) as f:
            data = json.load(f)
        assert data['mode'] == 'grid_3'

    def test_load_state(self, tmp_path):
        cr = self._make(tmp_path)
        cr.evaluate(60.0)
        loaded = cr.load_state()
        assert loaded is not None
        assert loaded.mode == 'grid_2'

    # ── Hysteresis ──
    def test_hysteresis_prevents_flicker(self, tmp_path):
        cr = self._make(tmp_path)
        cr.evaluate(115.0)  # available = 109.25 → grid_3
        assert cr.state.mode == 'grid_3'
        # Drop: available = 104*0.95 = 98.8, hysteresis threshold = 100-2 = 98 → still grid_3
        cr.evaluate(104.0)
        assert cr.state.mode == 'grid_3'
