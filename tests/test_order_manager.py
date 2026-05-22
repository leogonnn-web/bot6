"""Tests for OrderManager interface contract (shared/order_manager.py)."""
import pytest
from unittest.mock import MagicMock
from order_manager import OrderManager, SimpleLimitStrategy, ExecutionStrategy


class TestSimpleLimitStrategy:
    """SimpleLimitStrategy must delegate every call 1:1 to the exchange client."""

    def _make(self):
        client = MagicMock()
        client.create_limit_buy_order.return_value = {'id': 'buy_1'}
        client.create_limit_sell_order.return_value = {'id': 'sell_1'}
        client.create_market_sell_order.return_value = {'id': 'msell_1'}
        client.cancel_order.return_value = {'id': 'cancel_1'}
        client.amend_order.return_value = {'id': 'amend_1'}
        return client, SimpleLimitStrategy(client)

    def test_buy_delegates(self):
        client, strat = self._make()
        r = strat.execute_buy('BTC/USDT', 0.1, 60000.0)
        client.create_limit_buy_order.assert_called_once_with('BTC/USDT', 0.1, 60000.0)
        assert r['id'] == 'buy_1'

    def test_sell_delegates(self):
        client, strat = self._make()
        r = strat.execute_sell('BTC/USDT', 0.1, 61000.0)
        client.create_limit_sell_order.assert_called_once_with('BTC/USDT', 0.1, 61000.0)
        assert r['id'] == 'sell_1'

    def test_market_sell_delegates(self):
        client, strat = self._make()
        r = strat.execute_market_sell('BTC/USDT', 0.1)
        client.create_market_sell_order.assert_called_once_with('BTC/USDT', 0.1)
        assert r['id'] == 'msell_1'

    def test_cancel_delegates(self):
        client, strat = self._make()
        r = strat.cancel('ord_123', 'BTC/USDT')
        client.cancel_order.assert_called_once_with('ord_123', 'BTC/USDT')
        assert r['id'] == 'cancel_1'

    def test_amend_delegates(self):
        client, strat = self._make()
        r = strat.amend('ord_123', 'BTC/USDT', 0.2, 59000.0)
        client.amend_order.assert_called_once_with('ord_123', 'BTC/USDT', 0.2, 59000.0)
        assert r['id'] == 'amend_1'


class TestOrderManager:
    """OrderManager facade must route and track."""

    def _make_om(self):
        client = MagicMock()
        client.create_limit_buy_order.return_value = {'id': 'b1'}
        client.create_limit_sell_order.return_value = {'id': 's1'}
        client.create_market_sell_order.return_value = {'id': 'ms1'}
        strat = SimpleLimitStrategy(client)
        return OrderManager(strategy=strat), client

    def test_buy_tracks_order_id(self):
        om, _ = self._make_om()
        om.buy('SOL/USDT', 1.0, 150.0)
        assert om.last_order_id == 'b1'
        assert om.last_order_ts > 0

    def test_sell_tracks_order_id(self):
        om, _ = self._make_om()
        om.sell('SOL/USDT', 1.0, 155.0)
        assert om.last_order_id == 's1'

    def test_strategy_name(self):
        om, _ = self._make_om()
        assert om.strategy_name == 'SimpleLimitStrategy'

    def test_hot_swap_strategy(self):
        om, _ = self._make_om()
        new_strat = MagicMock(spec=ExecutionStrategy)
        new_strat.execute_buy.return_value = {'id': 'ice_1'}
        om.set_strategy(new_strat)
        om.buy('ETH/USDT', 0.5, 3000.0)
        new_strat.execute_buy.assert_called_once()
        assert om.last_order_id == 'ice_1'

    def test_abstract_cannot_instantiate(self):
        with pytest.raises(TypeError):
            ExecutionStrategy()
