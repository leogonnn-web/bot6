"""
HYDRA Trading Bot v17.0 - Main Trading Loop (decomposed)
Production-ready state machine. Logic is split into mixins:
  - core/states/  : IdleStateMixin, ScanningStateMixin, BuyingStateMixin,
                    InPositionStateMixin, ExitingStateMixin
  - core/risk/    : RiskLimitsMixin, SafetyMixin, BreakevenMixin
  - core/grid/    : HydraNetMixin (+ get_next_grid_level helper)
"""

import time
import signal
import sys
import os
import csv
import json
import random
import threading
from datetime import datetime
from typing import Dict

# Add shared/ to path BEFORE importing shared modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared')))

from logger_setup import logger
from config import config
from utils import safe_float, realized_pnl, circuit_breaker_tripped
from paths import HOT_SYMBOLS_FILE
from capital_router import CapitalRouter
from order_manager import OrderManager, SimpleLimitStrategy
from metrics import METRICS
from config_models import validate_config, ConfigValidationError

# New modular imports
from api.bybit_client import BybitClient
from indicators.matrix import initialize_analyzer
from database.models import TradeDatabase
from core.scanner import ScannerIntegration, DynamicSymbolManager
from core.health import HealthChecker
from core.toxic_flow import ToxicFlowFilter
from core.dispatcher import HydraDispatcher

# Shared enum + mixins
from core.state_enum import BotState
from core.states import (
    IdleStateMixin,
    ScanningStateMixin,
    BuyingStateMixin,
    InPositionStateMixin,
    ExitingStateMixin,
)
from core.risk import RiskLimitsMixin, SafetyMixin, BreakevenMixin
from core.grid import HydraNetMixin, get_next_grid_level  # noqa: F401 (re-export for backward compat)


class TradingBot(
    IdleStateMixin,
    ScanningStateMixin,
    BuyingStateMixin,
    InPositionStateMixin,
    ExitingStateMixin,
    RiskLimitsMixin,
    SafetyMixin,
    BreakevenMixin,
    HydraNetMixin,
):
    """Main TradingBot orchestrator.

    Holds shared state (self.exchange, self.config, self.state_data, ...) and
    drives the state machine via the run loop. Per-state logic lives in mixins.
    """

    def __init__(self, tank_mode: bool = False):
        logger.info("@INIT@ Initializing HYDRA v17.0 (WebSockets)...")
        self.config = config
        self.tank_mode = tank_mode

        # Fail-fast: validate config before anything else
        try:
            validate_config(self.config.config)
            logger.info("@CONFIG_OK@ Pydantic validation passed")
        except ConfigValidationError as e:
            logger.critical(f"@CONFIG_FATAL@ Invalid config — cannot start:\n{e}")
            raise SystemExit(1) from e

        # Initialize analyzer with config and tank_mode
        initialize_analyzer(self.config, tank_mode=tank_mode)

        self.exchange = BybitClient()
        self.trade_db = TradeDatabase()

        self.hot_symbols_file = HOT_SYMBOLS_FILE
        root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        self.csv_report_file = os.path.join(root_path, "daily_report.csv")

        self.scanner_integration = ScannerIntegration(self.hot_symbols_file)
        self.symbol_manager = DynamicSymbolManager(
            base_symbols=self.config.get_symbols(),
            scanner_integration=self.scanner_integration,
        )
        # Restore session profit from DB so Grafana doesn't show 0 after restart
        stats = self.trade_db.get_session_stats()
        self.session_profit = float(stats.get('session_profit', 0.0))
        METRICS.session_profit.set(self.session_profit)
        if self.session_profit != 0.0:
            logger.info(f"@RESTORE@ Session profit restored from DB: ${self.session_profit:.4f}")

        # Initialize price history - will be populated dynamically during scanning
        self.price_history = {}

        self.ws_tickers_cache = {}
        # Turnover history for lightweight proxy-RVOL (delta over last N seconds)
        self._turnover_history: Dict[str, list] = {}
        # Initialize to 0 so HealthChecker correctly reports stale on cold start
        # (it will be refreshed by the first successful WS or REST tick).
        self.last_rest_poll_time = 0.0
        # Throttle REST fallback inside _update_websocket_stream to avoid
        # hammering Bybit (HTTP 429) when WS is briefly stale.
        self._last_rest_fallback_time = 0.0
        self._rest_fallback_min_interval = 5.0  # seconds
        self.should_stop = False
        self.loop_counter = 0
        # Don't cache trading_config - always read fresh from config
        self.indicators_enabled = self.config.are_indicators_enabled()
        self.last_loss_time = 0.0

        # State persistence for graceful shutdown (production-critical)
        # Use Docker volume /app/shared/state so state survives container recreation
        state_dir = os.environ.get('STATE_DIR', os.path.join(root_path, "shared", "state"))
        os.makedirs(state_dir, exist_ok=True)
        self.state_file = os.path.join(state_dir, "hydra_state.json")
        self._load_state()
        if self.state != BotState.IDLE and self.state_data.get('symbol'):
            logger.info(f"@STATE_RESTORED@ Restored {self.state.name} for {self.state_data['symbol']} from state file")
        else:
            self.state = BotState.IDLE
            self.state_data = {}
            self.state_entry_time = time.time()
        self._last_state = BotState.IDLE
        self.health_checker = HealthChecker(self)

        # ToxicFlowFilter: blocks primary entries on adversarial flow
        # (aggressive sweeps, large sell prints). Config is read from
        # config.json -> "toxic_flow" section, with sensible defaults.
        toxic_cfg = self.config.config.get('toxic_flow', {}) or {}
        self.toxic_filter = ToxicFlowFilter(self.exchange.ws_listener, toxic_cfg)
        self.toxic_enabled = bool(toxic_cfg.get('enabled', True))

        # HYDRA-NET: Grid synchronization settings
        self.hydra_net_config = self.config.config.get('hydra_net', {})
        self.last_grid_update = 0.0
        self.grid_update_interval = self.hydra_net_config.get('grid_update_interval_sec', 3.0)

        # Cooldown between trades on same symbol
        self.symbol_cooldown = {}
        self.cooldown_duration = 90   # 1.5 minutes for aggressive trading

        # Circuit-breaker: rolling panic-exit events [(ts, signed_pnl), ...]
        self._panic_events = []
        self._breaker_until = 0.0

        # Capital Router: balance → allocation decision
        # In Docker, state file goes to /app/shared/state/ volume for Go arb bridge
        capital_state_path = os.environ.get('CAPITAL_STATE_FILE', None)
        self.capital_router = CapitalRouter(state_file=capital_state_path)
        self._capital_eval_interval = 30  # seconds
        self._last_capital_eval = 0.0

        # OrderManager: unified order execution (swap strategy later)
        self.order_manager = OrderManager(strategy=SimpleLimitStrategy(self.exchange))

        # HYDRA Dispatcher: score-based symbol selection and grid tuning
        # Phase 1: observation only (feedback_loop = OFF). Data logged to
        # dispatcher_features table for 24-48h analysis before enabling
        # adaptive weight updates.
        self.dispatcher = HydraDispatcher()
        self.dispatcher_enabled = self.config.config.get('dispatcher', {}).get('enabled', True)
        self.dispatcher_feedback = self.config.config.get('dispatcher', {}).get('feedback_loop', False)
        self.dispatcher_lr = self.config.config.get('dispatcher', {}).get('learning_rate', 0.02)
        self.dispatcher_weights_file = os.path.join(state_dir, 'dispatcher_weights.json')
        self._load_dispatcher_weights()

        # Background scanner thread: continuously evaluates all symbols
        # and maintains a ranked candidate queue for the dispatcher.
        self.dispatcher_candidates = []
        self._candidates_lock = threading.Lock()
        self._scan_thread_stop = threading.Event()
        self._scan_thread = None
        self._scan_interval = 5.0  # seconds between background scans

        # P.6: Rejected candidate cache for "second chance" — retry soft rejections
        self.rejected_cache = {}  # symbol -> {'candidate': dict, 'reason': str, 'retry_at': float}
        self.rejected_cache_ttl = 90  # max seconds to keep a rejected candidate
        self.rejected_retry_delay = 45  # seconds before retry

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Maintenance mode: emergency graceful exit (close positions, block new)
        self.maintenance_mode = False
        self._maintenance_start_time = 0.0
        self._maintenance_limit_timeout = 300  # seconds to wait limit sell
        self._maintenance_exit_complete = False

    # ------------------------------------------------------------------
    # Maintenance mode (emergency graceful exit)
    # ------------------------------------------------------------------
    def enter_maintenance_mode(self):
        """Trigger maintenance: block new entries, close open positions."""
        if self.maintenance_mode:
            logger.info("@MAINTENANCE@ Already in maintenance mode")
            return
        self.maintenance_mode = True
        self._maintenance_start_time = time.time()
        pos_count = 1 if self.state == BotState.IN_POSITION and self.state_data.get('symbol') else 0
        logger.info(f"@MAINTENANCE_ENTER@ Maintenance mode ON. Open positions: {pos_count}. New signals BLOCKED.")

    def _check_maintenance_exit(self) -> bool:
        """Returns True if bot should exit loop (all positions closed)."""
        if not self.maintenance_mode:
            return False
        # If IDLE with no positions — we are done
        if self.state == BotState.IDLE and not self.state_data.get('symbol'):
            logger.info("@MAINTENANCE_COMPLETE@ All positions closed. Safe to stop.")
            self._maintenance_exit_complete = True
            self.should_stop = True
            return True
        return False

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------
    def _signal_handler(self, signum, frame):
        logger.info("@SHUTDOWN_SIGNAL@ Shutdown signal received")
        self._save_state()
        self.should_stop = True

    def _save_state(self):
        """Persist state_data to disk so open positions survive restarts."""
        try:
            data = {
                'state': self.state.name if hasattr(self.state, 'name') else str(self.state),
                'state_data': self.state_data,
                'state_entry_time': self.state_entry_time,
                'session_profit': self.session_profit,
                'saved_at': time.time()
            }
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            logger.info(f"@STATE_SAVED@ State persisted to {self.state_file}")
        except Exception as e:
            logger.error(f"@STATE_SAVE_ERROR@ Failed to save state: {e}")

    def _load_state(self):
        """Restore state_data from disk after container restart."""
        self.state = BotState.IDLE
        self.state_data = {}
        self.state_entry_time = time.time()
        self.session_profit = 0.0
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                saved_state = data.get('state', 'IDLE')
                self.state = getattr(BotState, saved_state, BotState.IDLE)
                self.state_data = data.get('state_data', {})
                self.state_entry_time = data.get('state_entry_time', time.time())
                self.session_profit = float(data.get('session_profit', 0.0))
                # Clean up stale state if older than 24h
                saved_at = data.get('saved_at', 0)
                if time.time() - saved_at > 86400:
                    logger.warning("@STATE_STALE@ State file older than 24h, ignoring")
                    self.state = BotState.IDLE
                    self.state_data = {}
                else:
                    logger.info(f"@STATE_LOADED@ Recovered state {saved_state} from {self.state_file}")
                    # Reset stale session_profit if no active position to avoid double accounting
                    if self.state == BotState.IDLE:
                        self.session_profit = 0.0
                        self._save_state()
                        logger.info("@STATE_CLEAN@ Session profit reset to 0 for fresh IDLE session")
                    # Validate: if symbol in state, verify we still hold position
                    if self.state_data.get('symbol'):
                        self._recover_from_exchange()
        except Exception as e:
            logger.error(f"@STATE_LOAD_ERROR@ Failed to load state: {e}")
            self.state = BotState.IDLE
            self.state_data = {}

    def _recover_from_exchange(self):
        """On startup, verify saved state matches reality. Prevents ghost positions."""
        try:
            symbol = self.state_data.get('symbol')
            if not symbol:
                return
            # In dry_run mode we trust the state file (no real orders/holdings on exchange)
            if self.state_data.get('is_dry_run', False):
                logger.info(f"@RECOVER@ Dry-run mode: trusting saved IN_POSITION state for {symbol}")
                return
            # Check open orders for this symbol (ccxt is nested inside BybitClient)
            open_orders = []
            try:
                if hasattr(self.exchange, 'exchange'):
                    open_orders = self.exchange.exchange.fetch_open_orders(symbol)
                else:
                    open_orders = self.exchange.fetch_open_orders(symbol)
            except Exception:
                pass  # API may fail, continue to balance check
            if open_orders:
                logger.info(f"@RECOVER@ Found {len(open_orders)} open orders for {symbol}")
                return  # State is valid, position exists
            # Check if we hold the base currency (e.g. SHIB)
            base = symbol.split('/')[0]
            bal = self.exchange.fetch_balance()
            base_hold = bal.get(base, {}).get('free', 0.0)
            if float(base_hold) > 0:
                logger.info(f"@RECOVER@ Holding {base_hold} {base}, state valid")
                return  # We hold the asset, state is valid
            # No open orders, no asset → position was closed or never existed
            logger.warning(f"@RECOVER_GHOST@ No open orders or {base} holdings for {symbol}. Resetting to IDLE.")
            self.state = BotState.IDLE
            self.state_data = {}
            self.session_profit = 0.0
            self._save_state()
            logger.info("@STATE_CLEAN@ Session profit reset to 0 after ghost recovery")
        except Exception as e:
            logger.error(f"@RECOVER_ERROR@ Exchange recovery failed: {e}")

    # ------------------------------------------------------------------
    # Cooldown helpers (per-symbol)
    # ------------------------------------------------------------------
    def _check_symbol_cooldown(self, symbol: str) -> bool:
        """Check if symbol is in cooldown period."""
        if symbol not in self.symbol_cooldown:
            return False

        last_trade_time = self.symbol_cooldown[symbol]
        elapsed = time.time() - last_trade_time

        if elapsed < self.cooldown_duration:
            remaining = int(self.cooldown_duration - elapsed)
            logger.info(f"@COOLDOWN@ {symbol} in cooldown: {remaining}s remaining")
            return True
        return False

    def _update_symbol_cooldown(self, symbol: str):
        """Update cooldown timestamp for symbol."""
        self.symbol_cooldown[symbol] = time.time()
        logger.info(f"@COOLDOWN@ Set cooldown for {symbol}: {self.cooldown_duration}s")

    # ------------------------------------------------------------------
    # Circuit-breaker: halt new entries after a cluster of panic exits
    # ------------------------------------------------------------------
    def _record_panic_exit(self, pnl: float):
        """Register a panic/backstop exit for the circuit-breaker window."""
        try:
            now = time.time()
            self._panic_events.append((now, float(pnl)))
            # Keep only the last hour to bound memory
            self._panic_events = [(t, p) for (t, p) in self._panic_events if now - t <= 3600]
        except Exception as e:
            logger.debug(f"@CB_RECORD_WARN@ {e}")

    # ------------------------------------------------------------------
    # Dispatcher: weight persistence + online feedback
    # ------------------------------------------------------------------
    def _load_dispatcher_weights(self):
        """Load previously saved dispatcher weights if they exist."""
        try:
            if os.path.exists(self.dispatcher_weights_file):
                with open(self.dispatcher_weights_file, 'r') as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    self.dispatcher.weights.update(saved)
                    logger.info(f"@DISPATCHER_WEIGHTS@ Loaded from {self.dispatcher_weights_file}")
        except Exception as e:
            logger.warning(f"@DISPATCHER_WEIGHTS_WARN@ Load failed: {e}")

    def _save_dispatcher_weights(self):
        """Persist current dispatcher weights to disk."""
        try:
            with open(self.dispatcher_weights_file, 'w') as f:
                json.dump(self.dispatcher.weights, f, indent=2)
        except Exception as e:
            logger.debug(f"@DISPATCHER_WEIGHTS_WARN@ Save failed: {e}")

    def _apply_dispatcher_feedback(self, profit: float):
        """Log outcome + optionally update dispatcher weights after a closed trade.
        Skips partial fills. Reads dispatcher_features from state_data.
        """
        try:
            df = self.state_data.get('dispatcher_features')
            if not df:
                return
            # Determine take_profit_pct used for this trade
            tp_pct = self.state_data.get('take_profit_pct')
            if tp_pct is None:
                tp_pct = self.hydra_net_config.get('take_profit_pct',
                        self.config.get_trading_config().get('take_profit', 1.5))
            # Log outcome row (features + realized profit = clean training sample)
            self.trade_db.log_dispatcher_features(
                trade_id=0, symbol=df.get('symbol', ''),
                confidence=df.get('confidence', 0.0),
                rvol_spike=df.get('rvol_spike', 0.0),
                rvol_local=df.get('rvol_local', 0.0),
                dump_depth=df.get('dump_depth', 0.0),
                obi_skew=df.get('obi_skew', 0.0),
                btc_1h=df.get('btc_1h', 0.0),
                score=df.get('score', 0.0),
                mode=df.get('mode', 'normal'),
                profit=profit, take_profit_pct=tp_pct,
            )
            # Online weight update if enabled
            if self.dispatcher_feedback:
                feat = {k: df.get(k, 0.0) for k in self.dispatcher.weights}
                self.dispatcher.update_weights(feat, profit, tp_pct, self.dispatcher_lr)
                self._save_dispatcher_weights()
                logger.info(f"@DISPATCHER_FEEDBACK@ updated weights profit={profit:.4f}")
        except Exception as e:
            logger.debug(f"@DISPATCHER_FEEDBACK_WARN@ {e}")

    def _circuit_breaker_blocks(self) -> bool:
        """True if a recent cluster of panics should halt new entries."""
        try:
            cb = self.config.get_trading_config().get('circuit_breaker', {})
            if not cb.get('enabled', False):
                return False
            now = time.time()
            # Still inside an active cooldown from a prior trip
            if now < self._breaker_until:
                remaining = int(self._breaker_until - now)
                logger.warning(f"@CIRCUIT_BREAKER@ active, {remaining}s cooldown remaining")
                return True
            tripped, reason = circuit_breaker_tripped(
                self._panic_events, now,
                window_sec=cb.get('window_sec', 600),
                max_panics=cb.get('max_panics', 6),
                max_loss_usd=cb.get('max_loss_usd', 3.0),
            )
            if tripped:
                self._breaker_until = now + cb.get('cooldown_sec', 600)
                logger.critical(
                    f"@CIRCUIT_BREAKER@ TRIPPED ({reason}) -> halting new entries for "
                    f"{int(cb.get('cooldown_sec', 600))}s"
                )
                return True
            return False
        except Exception as e:
            logger.error(f"@CB_ERROR@ circuit-breaker check failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Main run loop + state dispatch
    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            logger.info("@START_SUCCESS@ HYDRA v17.0 STARTED")
            self.exchange.load_markets()

            # WebSocket streaming with auto-reconnect
            ws_cfg = self.config.get_websocket_config()
            if ws_cfg.get('enabled', True):
                ws_listener = self.exchange.ws_listener
                ws_listener.reconnect_interval_sec = ws_cfg.get('reconnect_interval_sec', 5)
                ws_listener.max_reconnect_attempts = ws_cfg.get('max_reconnect_attempts', 10)
                if ws_listener.start(self.config.get_symbols()):
                    logger.info("@WS_MODE@ Using WebSocket real-time streaming")
                else:
                    logger.warning("@WS_FALLBACK@ WebSocket init failed, using REST polling")
            else:
                logger.info("@REST_MODE@ WebSocket disabled in config, using REST polling")

            # Start Prometheus metrics HTTP endpoint
            metrics_port = self.config.config.get('metrics', {}).get('port', 9090)
            METRICS.start_server(metrics_port)
            METRICS.set_bot(self)  # Register for /maintenance endpoint
            logger.info(f"@METRICS@ Prometheus endpoint on :{metrics_port}/metrics and :{metrics_port}/maintenance")

            # Initial ticker update
            self._update_websocket_stream()

            # Start background scanner thread (daemon so it dies with main)
            self._scan_thread = threading.Thread(target=self._background_scan_loop, daemon=True)
            self._scan_thread.start()

            while not self.should_stop:
                try:
                    METRICS.heartbeat_timestamp.set(time.time())
                    self.loop_counter += 1
                    self._update_websocket_stream()

                    # Track state changes for stuck detection
                    if self.state != self._last_state:
                        self.state_entry_time = time.time()
                        self._last_state = self.state
                        self._save_state()

                    # Self-diagnostic health check
                    self.health_checker.check()

                    # Capital Router: periodic balance evaluation
                    if time.time() - self._last_capital_eval > self._capital_eval_interval:
                        self._evaluate_capital()

                    # HYDRA-NET: Synchronize grid if active
                    if self.state_data.get('is_grid_active', False):
                        self._synchronize_grid_network()

                    # Maintenance mode: check if all positions closed → exit
                    if self._check_maintenance_exit():
                        break

                    # State dispatch
                    if self.state == BotState.IDLE:
                        self._handle_idle_state()
                    elif self.state == BotState.SCANNING:
                        self._handle_scanning_state()
                    elif self.state == BotState.BUYING:
                        self._handle_buying_state()
                    elif self.state == BotState.IN_POSITION:
                        self._handle_in_position_state()
                    elif self.state == BotState.EXITING:
                        self._handle_exiting_state()

                    if self.loop_counter % 300 == 0:
                        try:
                            if hasattr(self.exchange, 'exchange'):
                                self.exchange.exchange.clear_caches()
                        except Exception as e:
                            logger.debug(f"@CACHE_CLEAR_WARN@ Failed to clear ccxt caches: {e}")
                        stats = self.trade_db.get_session_stats()
                        self._generate_daily_csv_report(stats)
                        logger.info(
                            f"@STATS@ Session stats - Trades: {stats.get('total_trades', 0)}, "
                            f"Profit: ${stats.get('total_profit', 0.0):.2f}"
                        )

                    time.sleep(1)
                except Exception as e:
                    logger.error(f"@LOOP_ERROR@ State machine error: {e}", exc_info=True)
                    time.sleep(10)
        finally:
            logger.info("@STOP@ Bot stopped")
            if self._scan_thread:
                self._scan_thread_stop.set()
                self._scan_thread.join(timeout=5)
                logger.info("@BG_SCAN_STOP@ Background scanner stopped")
            try:
                self.exchange.ws_listener.stop()
            except Exception as e:
                logger.error(f"Error stopping WebSocket: {e}")

    # ------------------------------------------------------------------
    # Capital Router
    # ------------------------------------------------------------------
    def _evaluate_capital(self) -> None:
        """Fetch balance and update CapitalRouter allocation."""
        try:
            self._last_capital_eval = time.time()
            trading_config = self.config.get_trading_config()
            if trading_config.get('dry_run', False):
                # In dry-run use a virtual balance
                balance_usdt = 1000.0
            else:
                balance = self.exchange.fetch_balance()
                free_section = balance.get('free') if isinstance(balance, dict) else None
                balance_usdt = safe_float(free_section.get('USDT', 0)) if isinstance(free_section, dict) else 0.0

            self.balance = balance_usdt
            base_slot = trading_config.get('slot_size', 12.0)
            state = self.capital_router.evaluate(balance_usdt, base_slot)

            # Push max_grid_levels into hydra_net runtime config
            self.hydra_net_config['max_grid_levels'] = state.max_grid_levels

            # Prometheus gauges
            METRICS.balance_usdt.set(balance_usdt)
            METRICS.grid_max_levels.set(state.max_grid_levels)
            METRICS.capital_mode.info({'mode': state.mode})
        except Exception as e:
            logger.error(f"@CAPITAL_EVAL_ERROR@ {e}")

    # ------------------------------------------------------------------
    # Ticker stream (REST polling with WS fallback) + reporting + helpers
    # ------------------------------------------------------------------
    def _update_websocket_stream(self):
        try:
            symbols = self.symbol_manager.get_symbols(refresh_scanner=False)
            trading_config = self.config.get_trading_config()
            is_dry_run = trading_config.get('dry_run', False)

            # Prefer WebSocket data in all modes (dry_run and live)
            ws_active = self.exchange.ws_listener.is_active()
            if ws_active:
                ws_fresh_count = 0
                for sym in symbols:
                    ws_price = self.exchange.ws_listener.get_price(sym)
                    if ws_price and self.exchange.ws_listener.is_data_fresh(sym):
                        # WS listener stores timestamps in a separate dict
                        # (last_update_time) and does NOT include 'timestamp'
                        # in the returned price dict. We attach it here so
                        # HealthChecker._check_tickers_cache (which requires
                        # >=95% fresh-within-10s entries) does not misfire.
                        # Copy first to avoid mutating the listener's internal
                        # dict, which is shared under price_lock.
                        self.ws_tickers_cache[sym] = {**ws_price, 'timestamp': time.time()}
                        # Track turnover for proxy-RVOL
                        tov = ws_price.get('turnover24h', 0)
                        if tov > 0:
                            self._turnover_history.setdefault(sym, []).append((time.time(), tov))
                            # Keep only last 30 seconds
                            self._turnover_history[sym] = [
                                (t, v) for t, v in self._turnover_history[sym] if time.time() - t <= 30
                            ]
                        ws_fresh_count += 1
                if ws_fresh_count > 0:
                    logger.debug(f"@WS_TICK@ Using {ws_fresh_count}/{len(symbols)} WebSocket tickers")
                    # Update last_rest_poll_time to reflect successful data source
                    self.last_rest_poll_time = time.time()
                    return
                else:
                    logger.debug("@WS_STALE@ WebSocket active but no fresh data, falling back to REST")

            # REST fallback (used when WS is inactive or data is stale).
            # Throttle to avoid hammering the API on every loop tick (~1s).
            now = time.time()
            if now - self._last_rest_fallback_time < self._rest_fallback_min_interval:
                return
            self._last_rest_fallback_time = now
            if is_dry_run:
                try:
                    raw_tickers = self.exchange.fetch_tickers(symbols)
                    self.last_rest_poll_time = time.time()
                    logger.info(f"@REST_POLL@ Fetched {len(raw_tickers)} tickers via REST (dry_run)")
                    for sym in symbols:
                        if sym in raw_tickers:
                            tov = safe_float(raw_tickers[sym].get('quoteVolume', raw_tickers[sym].get('turnover24h', 0)))
                            self.ws_tickers_cache[sym] = {
                                'ask': safe_float(raw_tickers[sym]['ask']),
                                'bid': safe_float(raw_tickers[sym]['bid']),
                                'last': safe_float(raw_tickers[sym]['last']),
                                'timestamp': time.time(),
                                'turnover24h': tov,
                                'bidVolume': safe_float(raw_tickers[sym].get('bidVolume', 0)),
                                'askVolume': safe_float(raw_tickers[sym].get('askVolume', 0)),
                            }
                            if tov > 0:
                                self._turnover_history.setdefault(sym, []).append((time.time(), tov))
                                self._turnover_history[sym] = [
                                    (t, v) for t, v in self._turnover_history[sym] if time.time() - t <= 30
                                ]
                except Exception as e:
                    logger.error(f"@DRY_RUN_PRICE_ERROR@ Failed to fetch real prices: {e}")
                    for sym in symbols:
                        base_price = 0.015 if "NOT" in sym else (
                            7.0 if "TON" in sym else (600.0 if "BNB" in sym else 0.077)
                        )
                        price_change = random.uniform(-0.02, 0.02)
                        mock_price = base_price * (1 + price_change)
                        self.ws_tickers_cache[sym] = {
                            'ask': mock_price,
                            'bid': mock_price * 0.999,
                            'last': mock_price,
                            'timestamp': time.time(),
                            'bidVolume': 1.0,
                            'askVolume': 1.0,
                        }
            else:
                raw_tickers = self.exchange.fetch_tickers(symbols)
                self.last_rest_poll_time = time.time()
                logger.info(f"@REST_POLL@ Fetched {len(raw_tickers)} tickers via REST")
                for sym in symbols:
                    if sym in raw_tickers:
                        tov = safe_float(raw_tickers[sym].get('quoteVolume', raw_tickers[sym].get('turnover24h', 0)))
                        self.ws_tickers_cache[sym] = {
                            'ask': safe_float(raw_tickers[sym]['ask']),
                            'bid': safe_float(raw_tickers[sym]['bid']),
                            'last': safe_float(raw_tickers[sym]['last']),
                            'timestamp': time.time(),
                            'turnover24h': tov,
                            'bidVolume': safe_float(raw_tickers[sym].get('bidVolume', 0)),
                            'askVolume': safe_float(raw_tickers[sym].get('askVolume', 0)),
                        }
                        if tov > 0:
                            self._turnover_history.setdefault(sym, []).append((time.time(), tov))
                            self._turnover_history[sym] = [
                                (t, v) for t, v in self._turnover_history[sym] if time.time() - t <= 30
                            ]
        except Exception as e:
            logger.debug(f"WebSocket stream update error: {e}")

    def _generate_daily_csv_report(self, stats: dict):
        try:
            file_exists = os.path.isfile(self.csv_report_file)
            current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.csv_report_file, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Date Time", "Total Trades", "Win Rate (%)", "Net Profit (USDT)"])
                win_rate = stats.get('win_rate', 0.0)
                writer.writerow([
                    current_date,
                    stats.get('total_trades', 0),
                    f"{win_rate:.1f}%",
                    f"${stats.get('total_profit', 0.0):.2f}",
                ])
        except Exception as e:
            logger.error(f"CSV report error: {e}")

    # ------------------------------------------------------------------
    # Background scanner thread (runs continuously, even in position)
    # ------------------------------------------------------------------
    def _background_scan_loop(self):
        """Daemon thread: scan all symbols every N seconds and update dispatcher queue."""
        logger.info("@BG_SCAN_THREAD@ Background scanner started")
        while not self._scan_thread_stop.is_set():
            try:
                if getattr(self, 'maintenance_mode', False):
                    time.sleep(self._scan_interval)
                    continue
                # Only scan if we have ticker data
                if not self.ws_tickers_cache:
                    time.sleep(1)
                    continue
                candidates = self._scan_and_collect_candidates()
                with self._candidates_lock:
                    self.dispatcher_candidates = candidates
                if candidates:
                    logger.info(
                        f"@BG_SCAN@ {len(candidates)} candidates. Top: {candidates[0]['symbol']} "
                        f"drop={candidates[0].get('drop', 0):.2f}% proxy_rvol=${candidates[0].get('proxy_rvol', 0):.0f}"
                    )
                else:
                    logger.debug("@BG_SCAN@ 0 candidates")
            except Exception as e:
                import traceback
                logger.error(f"@BG_SCAN_ERROR@ {e}")
                logger.error(traceback.format_exc())
            time.sleep(self._scan_interval)
        logger.info("@BG_SCAN_THREAD@ Background scanner stopped")

    # ------------------------------------------------------------------
    # Lightweight helpers used by mixins (RVOL, balance, time, BTC trend/corr)
    # ------------------------------------------------------------------
    def _calc_pnl(self, buy_price: float, sell_price: float, amount: float,
                  is_market_exit: bool = False) -> float:
        """Net realized PnL with exchange fees and (for market/panic exits) slippage.

        Single source of truth for all exit PnL across state handlers so that
        dry-run results reflect real trading costs.
        """
        tc = self.config.get_trading_config()
        fee_pct = tc.get('fee_pct', 0.1)
        slip = tc.get('panic_slippage_pct', 0.1) if is_market_exit else 0.0
        return realized_pnl(buy_price, sell_price, amount, fee_pct=fee_pct, slippage_pct=slip)

    def _calculate_real_rvol(self, ohlcv) -> float:
        try:
            # Exclude the last (incomplete) candle; use last completed as current
            if len(ohlcv) < 21:
                return 1.0
            volumes = [safe_float(candle[5]) for candle in ohlcv]
            current_volume = volumes[-2]
            avg_volume = sum(volumes[-17:-2]) / 15
            return current_volume / avg_volume if avg_volume > 0 else 1.0
        except Exception as e:
            logger.debug(f"@RVOL_WARN@ Failed to calculate RVOL: {e}")
            return 1.0

    def _check_balance(self) -> bool:
        """Проверяет наличие свободных USDT на спотовом балансе Bybit."""
        try:
            trading_config = self.config.get_trading_config()
            if trading_config.get('dry_run', False):
                return True
            slot_size = trading_config.get('slot_size', 18.0)
            min_required = trading_config.get('min_exchange_limit', 5.2)
            threshold = max(slot_size, min_required)
            balance = self.exchange.fetch_balance()
            free_usdt = 0.0
            free_section = balance.get('free') if isinstance(balance, dict) else None
            if isinstance(free_section, dict):
                free_usdt = safe_float(free_section.get('USDT', 0))
            if free_usdt <= 0:
                try:
                    coins = balance.get('info', {}).get('result', {}).get('list', [{}])[0].get('coin', [])
                    for c in coins:
                        if c.get('coin') == 'USDT':
                            free_usdt = safe_float(
                                c.get('availableToWithdraw') or c.get('walletBalance') or c.get('equity', 0)
                            )
                            break
                except Exception as parse_err:
                    logger.debug(f"@BALANCE_PARSE_WARN@ Bybit UTA balance parse failed: {parse_err}")
            if free_usdt < threshold:
                logger.warning(f"@BALANCE_LOW@ Free USDT: ${free_usdt:.2f} < required ${threshold:.2f}")
                return False
            logger.debug(f"@BALANCE_OK@ Free USDT: ${free_usdt:.2f} >= ${threshold:.2f}")
            return True
        except Exception as e:
            logger.error(f"@BALANCE_ERROR@ Failed to fetch balance: {e}", exc_info=True)
            return False

    def _check_time_session(self) -> bool:
        trading_config = self.config.get_trading_config()
        if not trading_config.get('block_night_trading', False):
            return True
        current_hour = datetime.now().hour
        allowed_hours = trading_config.get('allowed_hours', list(range(24)))
        if current_hour not in allowed_hours:
            print(f"Time block: Hour {current_hour} not in allowed hours @TIME_BLOCK@ ", end='\r')
            return False
        return True

    def _calculate_btc_correlation(self, symbol: str) -> float:
        """Calculate Pearson correlation between symbol and BTC over specified period."""
        try:
            market_config = self.config.get_market_conditions_config()
            if not market_config.get('btc_correlation_filter', False):
                return 1.0  # Skip correlation check if disabled

            period = market_config.get('btc_correlation_period', 24)

            try:
                symbol_ohlcv = self.exchange.fetch_ohlcv(symbol, '1h', limit=period)
                btc_ohlcv = self.exchange.fetch_ohlcv('BTC/USDT', '1h', limit=period)
            except Exception as e:
                logger.warning(f"Failed to fetch OHLCV for correlation: {e}")
                return 1.0

            if len(symbol_ohlcv) < period or len(btc_ohlcv) < period:
                logger.warning("Insufficient data for correlation calculation")
                return 1.0

            symbol_closes = [candle[4] for candle in symbol_ohlcv]
            btc_closes = [candle[4] for candle in btc_ohlcv]

            symbol_changes = [
                (symbol_closes[i] - symbol_closes[i - 1]) / symbol_closes[i - 1] * 100
                for i in range(1, len(symbol_closes))
            ]
            btc_changes = [
                (btc_closes[i] - btc_closes[i - 1]) / btc_closes[i - 1] * 100
                for i in range(1, len(btc_closes))
            ]

            import numpy as np
            correlation = np.corrcoef(symbol_changes, btc_changes)[0, 1]
            if np.isnan(correlation):
                return 0.0
            return float(correlation)
        except Exception as e:
            logger.error(f"Correlation calculation error: {e}")
            return 1.0

    def _check_btc_trend(self) -> bool:
        """Check if BTC trend is safe for trading based on recent price drop."""
        try:
            market_config = self.config.get_market_conditions_config()
            btc_filter = market_config.get('btc_filter', {})

            if not btc_filter.get('enabled', False):
                return True

            lookback_minutes = btc_filter.get('lookback_minutes', 5)
            max_drop_pct = btc_filter.get('max_drop_pct', 0.5)

            btc_symbol = 'BTC/USDT'
            ws_data = self.ws_tickers_cache.get(btc_symbol, {})

            if not ws_data:
                try:
                    btc_ticker = self.exchange.fetch_ticker(btc_symbol)
                    current_price = safe_float(btc_ticker['last'])
                except Exception as e:
                    logger.warning(f"Failed to fetch BTC price: {e}")
                    return True
            else:
                current_price = safe_float(ws_data.get('last'))

            try:
                btc_ohlcv = self.exchange.fetch_ohlcv(btc_symbol, '1m', limit=lookback_minutes)
                if len(btc_ohlcv) < 2:
                    logger.warning("Insufficient BTC OHLCV data")
                    return True
                high_price = max([candle[2] for candle in btc_ohlcv])
                drop_pct = ((high_price - current_price) / high_price) * 100
                if drop_pct > max_drop_pct:
                    logger.warning(
                        f"@BTC_DROP_BLOCK@ BTC dropped {drop_pct:.2f}% in {lookback_minutes}m "
                        f"(limit: {max_drop_pct}%), blocking trades"
                    )
                    return False
                return True
            except Exception as e:
                logger.error(f"BTC trend check error: {e}")
                return True
        except Exception as e:
            logger.error(f"BTC trend check error: {e}")
            return True


def main():
    bot = TradingBot()
    bot.run()


if __name__ == "__main__":
    main()
