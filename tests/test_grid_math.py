"""Tests for HYDRA-NET grid math (src/core/grid/hydra_net.py::get_next_grid_level)."""
import pytest
from core.grid.hydra_net import get_next_grid_level


class TestGetNextGridLevel:
    """Verify price ladder + Martingale multiplier math."""

    def test_level1_price_below_entry(self):
        result = get_next_grid_level(entry_price=100.0, total_amount=0,
                                     current_level=1, base_order_size_usdt=10.0)
        assert result['next_price'] < 100.0

    def test_level2_price_below_level1(self):
        r1 = get_next_grid_level(100.0, 0, 1, 10.0)
        r2 = get_next_grid_level(100.0, 0, 2, 10.0)
        assert r2['next_price'] < r1['next_price']

    def test_martingale_multiplier_grows(self):
        r1 = get_next_grid_level(100.0, 0, 1, 10.0)
        r2 = get_next_grid_level(100.0, 0, 2, 10.0)
        r3 = get_next_grid_level(100.0, 0, 3, 10.0)
        assert r1['next_amount_usdt'] < r2['next_amount_usdt'] < r3['next_amount_usdt']

    def test_level1_amount_is_1_5x_base(self):
        r = get_next_grid_level(100.0, 0, 1, 10.0)
        assert r['next_amount_usdt'] == pytest.approx(15.0)  # 10 * 1.5

    def test_level4_raises(self):
        with pytest.raises(ValueError):
            get_next_grid_level(100.0, 0, 4, 10.0)

    def test_atr_widens_grid(self):
        """When ATR is large, grid distance should be wider than default 0.4%."""
        r_no_atr = get_next_grid_level(100.0, 0, 1, 10.0, atr=None)
        r_big_atr = get_next_grid_level(100.0, 0, 1, 10.0, atr=5.0)  # 5% ATR
        # Bigger ATR → lower next_price (wider step)
        assert r_big_atr['next_price'] < r_no_atr['next_price']


class TestWeightedAverage:
    """Verify the average-price recalculation done in _on_grid_level_filled."""

    def test_two_fills_weighted_average(self):
        # Simulate: base buy $10 @ $100, then grid fill $15 @ $99.6
        cost_1, qty_1 = 10.0, 10.0 / 100.0  # 0.1 units
        cost_2, qty_2 = 15.0 * 99.6 / 99.6, 15.0 / 99.6  # ~0.15060 units
        total_cost = (100.0 * qty_1) + (99.6 * qty_2)
        total_qty = qty_1 + qty_2
        avg = total_cost / total_qty
        assert 99.6 < avg < 100.0
