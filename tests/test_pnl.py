"""Tests for shared/utils.py::realized_pnl — fee/slippage-aware exit PnL."""
import pytest
from utils import realized_pnl


class TestRealizedPnl:
    def test_gross_when_no_costs(self):
        # fee=0, slippage=0 -> plain (sell-buy)*amount
        assert realized_pnl(100.0, 100.8, 1.0, fee_pct=0.0, slippage_pct=0.0) == pytest.approx(0.8)

    def test_fees_reduce_profit(self):
        gross = realized_pnl(100.0, 100.8, 1.0, fee_pct=0.0)
        net = realized_pnl(100.0, 100.8, 1.0, fee_pct=0.1)
        assert net < gross
        # round-trip fee ≈ (100 + 100.8) * 0.001 = 0.2008
        assert net == pytest.approx(0.8 - 0.2008, abs=1e-4)

    def test_slippage_reduces_sell_side(self):
        no_slip = realized_pnl(100.0, 100.8, 1.0, fee_pct=0.1, slippage_pct=0.0)
        with_slip = realized_pnl(100.0, 100.8, 1.0, fee_pct=0.1, slippage_pct=0.1)
        assert with_slip < no_slip

    def test_small_tp_turns_negative_after_fees(self):
        # +0.15% TP cannot cover 0.2% round-trip fees
        net = realized_pnl(100.0, 100.15, 1.0, fee_pct=0.1)
        assert net < 0

    def test_loss_gets_worse_with_costs(self):
        gross = realized_pnl(100.0, 99.0, 1.0, fee_pct=0.0)
        net = realized_pnl(100.0, 99.0, 1.0, fee_pct=0.1, slippage_pct=0.1)
        assert net < gross < 0

    def test_scales_with_amount(self):
        one = realized_pnl(100.0, 100.8, 1.0, fee_pct=0.1)
        ten = realized_pnl(100.0, 100.8, 10.0, fee_pct=0.1)
        assert ten == pytest.approx(one * 10.0, rel=1e-9)
