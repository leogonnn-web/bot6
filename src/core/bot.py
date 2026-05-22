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
import random
from datetime import datetime
from typing import Dict

# Add shared/ to path BEFORE importing shared modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared')))

from logger_setup import logger
from config import config
from utils import safe_float
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
        self.session_profit = 0.0

        # Initialize price history - will be populated dynamically during scanning
        self.price_history = {}

        self.ws_tickers_cache = {}
        self.should_stop = False
        self.loop_counter = 0
        # Don't cache trading_config - always read fresh from config
        self.indicators_enabled = self.config.are_indicators_enabled()
        self.last_loss_time = 0.0

        self.state = BotState.IDLE
        self.state_data = {}

        # HYDRA-NET: Grid synchronization settings
        self.hydra_net_config = self.config.config.get('hydra_net', {})
        self.last_grid_update = 0.0
        self.grid_update_interval = self.hydra_net_config.get('grid_update_interval_sec', 3.0)

        # Cooldown between trades on same symbol
        self.symbol_cooldown = {}
        self.cooldown_duration = 600  # 10 minutes in seconds

        # Capital Router: balance → allocation decision
        # In Docker, state file goes to /app/shared/state/ volume for Go arb bridge
        capital_state_path = os.environ.get('CAPITAL_STATE_FILE', None)
        self.capital_router = CapitalRouter(state_file=capital_state_path)
        self._capital_eval_interval = 30  # seconds
        self._last_capital_eval = 0.0

        # OrderManager: unified order execution (swap strategy later)
        self.order_manager = OrderManager(strategy=SimpleLimitStrategy(self.exchange))

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------
    def _signal_handler(self, signum, frame):
        logger.info("@SHUTDOWN_SIGNAL@ Shutdown signal received")
        self.should_stop = True

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
    # Main run loop + state dispatch
    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            logger.info("@START_SUCCESS@ HYDRA v17.0 STARTED")
            self.exchange.load_markets()

            # Disable WebSocket - use REST polling only (ccxt.pro Bybit WS unstable)
            logger.info("@REST_MODE@ Using REST polling (WebSocket disabled)")

            # Start Prometheus metrics HTTP endpoint
            metrics_port = self.config.config.get('metrics', {}).get('port', 9090)
            METRICS.start_server(metrics_port)
            logger.info(f"@METRICS@ Prometheus endpoint on :{metrics_port}/metrics")

            # Initial ticker update
            self._update_websocket_stream()

            while not self.should_stop:
                try:
                    self.loop_counter += 1
                    self._update_websocket_stream()

                    # Capital Router: periodic balance evaluation
                    if time.time() - self._last_capital_eval > self._capital_eval_interval:
                        self._evaluate_capital()

                    # HYDRA-NET: Synchronize grid if active
                    if self.state_data.get('is_grid_active', False):
                        self._synchronize_grid_network()

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

            if is_dry_run:
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
                    }
                return

            # Try to get prices from WebSocket
            ws_active = self.exchange.ws_listener.is_active()
            if ws_active:
                for sym in symbols:
                    ws_price = self.exchange.ws_listener.get_price(sym)
                    if ws_price and self.exchange.ws_listener.is_data_fresh(sym):
                        self.ws_tickers_cache[sym] = ws_price
                    else:
                        logger.warning(f"@WS_FALLBACK@ WebSocket data stale for {sym}, using REST")
                        try:
                            ticker = self.exchange.fetch_ticker(sym)
                            self.ws_tickers_cache[sym] = {
                                'ask': safe_float(ticker['ask']),
                                'bid': safe_float(ticker['bid']),
                                'last': safe_float(ticker['last']),
                                'timestamp': time.time(),
                            }
                        except Exception as e:
                            logger.error(f"REST fallback failed for {sym}: {e}")
            else:
                raw_tickers = self.exchange.fetch_tickers(symbols)
                logger.info(f"@REST_POLL@ Fetched {len(raw_tickers)} tickers via REST")
                for sym in symbols:
                    if sym in raw_tickers:
                        self.ws_tickers_cache[sym] = {
                            'ask': safe_float(raw_tickers[sym]['ask']),
                            'bid': safe_float(raw_tickers[sym]['bid']),
                            'last': safe_float(raw_tickers[sym]['last']),
                            'timestamp': time.time(),
                        }
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
    # Lightweight helpers used by mixins (RVOL, balance, time, BTC trend/corr)
    # ------------------------------------------------------------------
    def _calculate_real_rvol(self, ohlcv) -> float:
        try:
            if len(ohlcv) < 20:
                return 1.0
            volumes = [safe_float(candle[5]) for candle in ohlcv]
            current_volume = volumes[-1]
            avg_volume = sum(volumes[-16:-1]) / 15
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
