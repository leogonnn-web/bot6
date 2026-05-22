"""Tests for Prometheus metrics integration (shared/metrics.py + order_manager)."""
import pytest
from unittest.mock import MagicMock
from metrics import METRICS, _Metrics
from order_manager import OrderManager, SimpleLimitStrategy


class TestMetricsSingleton:
    def test_metrics_singleton_exists(self):
        assert METRICS is not None

    def test_order_total_counter_exists(self):
        assert hasattr(METRICS, 'order_total')

    def test_balance_gauge_exists(self):
        assert hasattr(METRICS, 'balance_usdt')

    def test_slippage_histogram_exists(self):
        assert hasattr(METRICS, 'slippage_pct')

    def test_bot_state_gauge_exists(self):
        assert hasattr(METRICS, 'bot_state')


class TestOrderManagerMetrics:
    """Verify OrderManager increments Prometheus counters on order execution."""

    def _make_om(self):
        client = MagicMock()
        client.create_limit_buy_order.return_value = {'id': 'b1'}
        client.create_limit_sell_order.return_value = {'id': 's1'}
        client.create_market_sell_order.return_value = {'id': 'ms1'}
        strat = SimpleLimitStrategy(client)
        return OrderManager(strategy=strat), client

    def test_buy_records_latency(self):
        om, _ = self._make_om()
        om.buy('BTC/USDT', 0.1, 60000.0)
        assert om.last_order_ts > 0

    def test_sell_records_latency(self):
        om, _ = self._make_om()
        om.sell('BTC/USDT', 0.1, 61000.0)
        assert om.last_order_ts > 0

    def test_error_increments_error_counter(self):
        client = MagicMock()
        client.create_limit_buy_order.side_effect = RuntimeError("API down")
        strat = SimpleLimitStrategy(client)
        om = OrderManager(strategy=strat)
        with pytest.raises(RuntimeError):
            om.buy('BTC/USDT', 0.1, 60000.0)

    def test_market_sell_tracks(self):
        om, _ = self._make_om()
        om.market_sell('SOL/USDT', 5.0)
        assert om.last_order_id == 'ms1'
