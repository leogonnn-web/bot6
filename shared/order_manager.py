"""
OrderManager — unified order-execution abstraction.

Current strategy: SimpleLimitStrategy (thin wrapper around BybitClient).
Future drop-in replacements: IcebergStrategy, TWAPStrategy (when capital > $1000).

Usage in bot code:
    from order_manager import OrderManager, SimpleLimitStrategy
    om = OrderManager(strategy=SimpleLimitStrategy(exchange_client))
    result = om.buy(symbol, amount, price)
"""

from __future__ import annotations

import abc
import time
from typing import Any, Dict, Optional

try:
    from metrics import METRICS
except ImportError:
    METRICS = None


class ExecutionStrategy(abc.ABC):
    """Abstract base for order-execution strategies."""

    @abc.abstractmethod
    def execute_buy(self, symbol: str, amount: float, price: float) -> Dict[str, Any]:
        """Place a buy order. Returns exchange response dict."""

    @abc.abstractmethod
    def execute_sell(self, symbol: str, amount: float, price: float) -> Dict[str, Any]:
        """Place a sell order. Returns exchange response dict."""

    @abc.abstractmethod
    def execute_market_sell(self, symbol: str, amount: float) -> Dict[str, Any]:
        """Place a market sell (panic exit). Returns exchange response dict."""

    @abc.abstractmethod
    def cancel(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Cancel an open order."""

    @abc.abstractmethod
    def amend(self, order_id: str, symbol: str, amount: float, price: float) -> Optional[Dict[str, Any]]:
        """Amend (modify) an open order in-place."""


class SimpleLimitStrategy(ExecutionStrategy):
    """Delegates directly to BybitClient — zero overhead wrapper."""

    def __init__(self, exchange_client):
        self._client = exchange_client

    def execute_buy(self, symbol: str, amount: float, price: float) -> Dict[str, Any]:
        return self._client.create_limit_buy_order(symbol, amount, price)

    def execute_sell(self, symbol: str, amount: float, price: float) -> Dict[str, Any]:
        return self._client.create_limit_sell_order(symbol, amount, price)

    def execute_market_sell(self, symbol: str, amount: float) -> Dict[str, Any]:
        return self._client.create_market_sell_order(symbol, amount)

    def cancel(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return self._client.cancel_order(order_id, symbol)

    def amend(self, order_id: str, symbol: str, amount: float, price: float) -> Optional[Dict[str, Any]]:
        return self._client.amend_order(order_id, symbol, amount, price)


class OrderManager:
    """Facade: routes all order calls through the active ExecutionStrategy.

    Tracks last-order metadata for diagnostics / Prometheus integration.
    Strategy can be hot-swapped at runtime via `set_strategy()`.
    """

    def __init__(self, strategy: ExecutionStrategy):
        self._strategy = strategy
        self.last_order_ts: float = 0.0
        self.last_order_id: Optional[str] = None

    # -- strategy hot-swap -----------------------------------------------------
    def set_strategy(self, strategy: ExecutionStrategy) -> None:
        self._strategy = strategy

    @property
    def strategy_name(self) -> str:
        return type(self._strategy).__name__

    # -- order routing ---------------------------------------------------------
    def buy(self, symbol: str, amount: float, price: float) -> Dict[str, Any]:
        return self._execute_with_metrics('buy', lambda: self._strategy.execute_buy(symbol, amount, price))

    def sell(self, symbol: str, amount: float, price: float) -> Dict[str, Any]:
        return self._execute_with_metrics('sell', lambda: self._strategy.execute_sell(symbol, amount, price))

    def market_sell(self, symbol: str, amount: float) -> Dict[str, Any]:
        return self._execute_with_metrics('market_sell', lambda: self._strategy.execute_market_sell(symbol, amount))

    def cancel(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return self._strategy.cancel(order_id, symbol)

    def amend(self, order_id: str, symbol: str, amount: float, price: float) -> Optional[Dict[str, Any]]:
        return self._strategy.amend(order_id, symbol, amount, price)

    # -- internal --------------------------------------------------------------
    def _execute_with_metrics(self, side: str, fn) -> Dict[str, Any]:
        """Execute order function with latency/counter metrics."""
        t0 = time.time()
        try:
            result = fn()
            elapsed = time.time() - t0
            self.last_order_ts = time.time()
            self.last_order_id = result.get('id')
            if METRICS:
                METRICS.order_total.labels(side=side, strategy=self.strategy_name).inc()
                METRICS.order_latency.labels(side=side).observe(elapsed)
            return result
        except Exception as e:
            if METRICS:
                METRICS.order_errors.labels(side=side, error_type=type(e).__name__).inc()
            raise
