"""
Prometheus metrics for HYDRA Trading Bot.

All metrics are defined here as module-level singletons.
Import and use from any module:
    from metrics import METRICS
    METRICS.order_total.labels(side='buy', strategy='SimpleLimitStrategy').inc()

HTTP server started once via `start_metrics_server(port)`.
"""

from __future__ import annotations

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
    start_http_server,
)

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

class _Metrics:
    """Container for all Prometheus metrics — instantiated once at import."""

    def __init__(self):
        # ── Orders ──
        self.order_total = Counter(
            'hydra_orders_total',
            'Total orders placed',
            ['side', 'strategy'],
        )
        self.order_errors = Counter(
            'hydra_order_errors_total',
            'Order placement failures',
            ['side', 'error_type'],
        )
        self.order_latency = Histogram(
            'hydra_order_latency_seconds',
            'Order round-trip latency',
            ['side'],
            buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        )

        # ── Slippage ──
        self.slippage_pct = Histogram(
            'hydra_slippage_pct',
            'Order slippage in percent (actual vs requested price)',
            ['side'],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0),
        )

        # ── Balance / Capital Router ──
        self.balance_usdt = Gauge(
            'hydra_balance_usdt',
            'Current USDT balance',
        )
        self.capital_mode = Info(
            'hydra_capital_mode',
            'Current Capital Router mode',
        )
        self.grid_max_levels = Gauge(
            'hydra_grid_max_levels',
            'Max grid levels allowed by Capital Router',
        )

        # ── Bot state ──
        self.bot_state = Gauge(
            'hydra_bot_state',
            'Current bot state (0=IDLE,1=SCANNING,2=BUYING,3=IN_POSITION,4=EXITING)',
        )
        self.scan_cycle_total = Counter(
            'hydra_scan_cycles_total',
            'Total scan cycles executed',
        )
        self.active_positions = Gauge(
            'hydra_active_positions',
            'Number of active positions',
        )

        # ── Grid ──
        self.grid_level = Gauge(
            'hydra_grid_current_level',
            'Current Martingale grid level',
            ['symbol'],
        )
        self.grid_avg_price = Gauge(
            'hydra_grid_avg_price',
            'Weighted average grid entry price',
            ['symbol'],
        )

        # ── Session ──
        self.session_profit = Gauge(
            'hydra_session_profit_usdt',
            'Cumulative session profit in USDT',
        )

    def start_server(self, port: int = 9090) -> None:
        """Start the Prometheus HTTP scrape endpoint (call once)."""
        try:
            start_http_server(port)
        except OSError:
            # Port already bound (e.g. bot restarted without killing old process)
            pass


# Module-level singleton
METRICS = _Metrics()
