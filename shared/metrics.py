"""
Prometheus metrics for HYDRA Trading Bot.

All metrics are defined here as module-level singletons.
Import and use from any module:
    from metrics import METRICS
    METRICS.order_total.labels(side='buy', strategy='SimpleLimitStrategy').inc()

HTTP server started once via `start_metrics_server(port)`.
Also serves /maintenance endpoint for graceful emergency exit.
"""

from __future__ import annotations

import weakref
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
    REGISTRY,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

class _Metrics:
    """Container for all Prometheus metrics — instantiated once at import."""

    def __init__(self):
        self.registry = REGISTRY
        # Clear stale metrics on re-import / restart
        for name in list(self.registry._names_to_collectors.keys()):
            try:
                self.registry.unregister(self.registry._names_to_collectors[name])
            except KeyError:
                pass

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
        self.health_status = Gauge(
            'hydra_health_status',
            'Health check status (1=healthy, 0=degraded)',
        )
        self.heartbeat_timestamp = Gauge(
            'hydra_heartbeat_timestamp',
            'Unix timestamp of last main loop iteration',
        )

        # ── Toxic-flow filter ──
        self.toxic_blocks_total = Counter(
            'hydra_toxic_blocks_total',
            'Total entry attempts blocked by ToxicFlowFilter',
            ['symbol', 'reason'],
        )
        self.toxic_active = Gauge(
            'hydra_toxic_active',
            'Whether the symbol is currently flagged as toxic (1=blocked)',
            ['symbol'],
        )

    def start_server(self, port: int = 9090) -> None:
        """Start the Prometheus HTTP scrape endpoint with /maintenance support."""
        try:
            server = HTTPServer(('', port), _make_handler(self.registry))
            threading.Thread(target=server.serve_forever, daemon=True).start()
        except OSError:
            pass

    def set_bot(self, bot) -> None:
        """Register bot instance for maintenance endpoint callbacks."""
        _BOT_REF['bot'] = weakref.ref(bot)


# Global weak reference storage for the HTTP handler
_BOT_REF: dict = {}


def _make_handler(registry):
    """Factory that returns a custom HTTPRequestHandler class."""
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # Suppress default HTTP logging noise

        def do_GET(self):
            if self.path == '/metrics':
                self.send_response(200)
                self.send_header('Content-Type', CONTENT_TYPE_LATEST)
                self.end_headers()
                self.wfile.write(generate_latest(registry))
            elif self.path == '/maintenance':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                bot_ref = _BOT_REF.get('bot')
                bot = bot_ref() if bot_ref else None
                if bot:
                    status = {
                        'maintenance_mode': getattr(bot, 'maintenance_mode', False),
                        'state': getattr(getattr(bot, 'state', None), 'name', 'unknown'),
                        'symbol': bot.state_data.get('symbol') if hasattr(bot, 'state_data') else None,
                    }
                else:
                    status = {'maintenance_mode': False, 'error': 'bot not registered'}
                self.wfile.write(json.dumps(status).encode())
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == '/maintenance':
                bot_ref = _BOT_REF.get('bot')
                bot = bot_ref() if bot_ref else None
                if bot and hasattr(bot, 'enter_maintenance_mode'):
                    bot.enter_maintenance_mode()
                    self.send_response(202)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'maintenance_started'}).encode())
                else:
                    self.send_response(503)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'bot_not_ready'}).encode())
            else:
                self.send_response(404)
                self.end_headers()

    return _Handler


# Module-level singleton
METRICS = _Metrics()
import json  # noqa: E402
