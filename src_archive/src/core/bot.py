"""
HYDRA Trading Bot v17.0 - Main Trading Loop
Production-ready state machine with WebSocket stream
"""

import time
import signal
import sys
import os
import csv
from enum import Enum, auto
from datetime import datetime
from typing import Optional, Dict

# Add paths
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared')))

from logger_setup import logger
from config import config
from utils import safe_float, format_currency, format_percentage
from paths import HOT_SYMBOLS_FILE

# New modular imports
from api.bybit_client import BybitClient
from indicators.matrix import analyzer, initialize_analyzer
from database.models import TradeDatabase
from core.scanner import ScannerIntegration, DynamicSymbolManager


class BotState(Enum):
    IDLE = auto()
    SCANNING = auto()
    BUYING = auto()
    IN_POSITION = auto()
    EXITING = auto()


class TradingBot:
    def __init__(self, tank_mode: bool = False):
        logger.info("@INIT@ Initializing HYDRA v17.0 (WebSockets)...")
        self.config = config
        self.tank_mode = tank_mode
        
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
            scanner_integration=self.scanner_integration
        )
        self.session_profit = 0.0
        
        # Initialize price history
        self.price_history = {}
        for symbol in self.config.get_symbols():
            mock_price = 0.015 if "NOT" in symbol else (7.0 if "TON" in symbol else (600.0 if "BNB" in symbol else 1.0))
            self.price_history[symbol] = [mock_price, time.time()]
        
        self.ws_tickers_cache = {}
        self.should_stop = False
        self.loop_counter = 0
        self.trading_config = self.config.get_trading_config()
        self.indicators_enabled = self.config.are_indicators_enabled()
        self.last_loss_time = 0.0
        
        self.state = BotState.IDLE
        self.state_data = {}
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("@SHUTDOWN_SIGNAL@ Shutdown signal received")
        self.should_stop = True

    def run(self) -> None:
        try:
            logger.info("@START_SUCCESS@ HYDRA v17.0 STARTED")
            self.exchange.load_markets()
            
            # Disable WebSocket - use REST polling only (ccxt.pro Bybit WebSocket not working)
            logger.info("@REST_MODE@ Using REST polling (WebSocket disabled)")
            
            # Initial ticker update
            self._update_websocket_stream()
            
            while not self.should_stop:
                try:
                    self.loop_counter += 1
                    self._update_websocket_stream()
                    
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
                        except:
                            pass
                        stats = self.trade_db.get_session_stats()
                        self._generate_daily_csv_report(stats)
                        logger.info(f"@STATS@ Session stats - Trades: {stats.get('total_trades', 0)}, Profit: ${stats.get('total_profit', 0.0):.2f}")
                    
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

    def _update_websocket_stream(self):
        try:
            symbols = self.symbol_manager.get_symbols(refresh_scanner=False)
            is_dry_run = self.trading_config.get('dry_run', False)
            
            if is_dry_run:
                import random
                for sym in symbols:
                    base_price = 0.015 if "NOT" in sym else (7.0 if "TON" in sym else (600.0 if "BNB" in sym else 0.077))
                    price_change = random.uniform(-0.02, 0.02)
                    mock_price = base_price * (1 + price_change)
                    self.ws_tickers_cache[sym] = {
                        'ask': mock_price, 'bid': mock_price * 0.999, 'last': mock_price, 'timestamp': time.time()
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
                        # Fallback to REST if WebSocket data missing or stale
                        logger.warning(f"@WS_FALLBACK@ WebSocket data stale for {sym}, using REST")
                        try:
                            ticker = self.exchange.fetch_ticker(sym)
                            self.ws_tickers_cache[sym] = {
                                'ask': safe_float(ticker['ask']),
                                'bid': safe_float(ticker['bid']),
                                'last': safe_float(ticker['last']),
                                'timestamp': time.time()
                            }
                        except Exception as e:
                            logger.error(f"REST fallback failed for {sym}: {e}")
            else:
                # Use REST polling if WebSocket not active
                raw_tickers = self.exchange.fetch_tickers(symbols)
                logger.info(f"@REST_POLL@ Fetched {len(raw_tickers)} tickers via REST")
                for sym in symbols:
                    if sym in raw_tickers:
                        self.ws_tickers_cache[sym] = {
                            'ask': safe_float(raw_tickers[sym]['ask']),
                            'bid': safe_float(raw_tickers[sym]['bid']),
                            'last': safe_float(raw_tickers[sym]['last']),
                            'timestamp': time.time()
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
                writer.writerow([current_date, stats.get('total_trades', 0), f"{win_rate:.1f}%", f"${stats.get('total_profit', 0.0):.2f}"])
        except Exception as e:
            logger.error(f"CSV report error: {e}")

    def _handle_idle_state(self):
        risk_ok = self._check_risk_limits()
        time_ok = self._check_time_session()
        balance_ok = self._check_balance()
        
        if not risk_ok:
            logger.info("@IDLE@ Risk limits check failed")
        if not time_ok:
            logger.info("@IDLE@ Time session check failed")
        if not balance_ok:
            logger.info("@IDLE@ Balance check failed")
            
        if not risk_ok or not time_ok or not balance_ok:
            time.sleep(5)
            return
        
        logger.info("@IDLE@ All checks passed, transitioning to SCANNING")
        self.state = BotState.SCANNING

    def _handle_scanning_state(self):
        self._scan_for_entries()
        if self.state == BotState.SCANNING:
            self.state = BotState.IDLE

    def _handle_in_position_state(self):
        try:
            symbol = self.state_data['symbol']
            is_dry_run = self.trading_config.get('dry_run', False)
            ws_data = self.ws_tickers_cache.get(symbol, {})
            current_price = ws_data.get('last') or safe_float(self.exchange.fetch_ticker(symbol)['last'])
            
            change_percent = ((current_price - self.state_data['buy_price']) / self.state_data['buy_price']) * 100
            elapsed = time.time() - self.state_data['buy_time']
            take_profit_pct = self.trading_config.get('take_profit', 1.5)
            
            # Partial TP settings
            partial_tp_enabled = self.trading_config.get('partial_tp_activation_pct', 1.0) > 0
            partial_tp_activation = self.trading_config.get('partial_tp_activation_pct', 1.0)
            partial_tp_size = self.trading_config.get('partial_tp_size_pct', 50.0)
            move_to_breakeven = self.trading_config.get('move_to_breakeven', True)
            trailing_callback = self.trading_config.get('trailing_callback_pct', 0.5)
            
            print(f"Position {symbol}: {change_percent:.2f}% | Time: {int(elapsed)}s @MONITOR_WS@", end='\r')
            is_tp_hit = change_percent >= take_profit_pct
            is_sl_hit = change_percent <= -self.trading_config['panic_stop']
            is_partial_tp_hit = partial_tp_enabled and change_percent >= partial_tp_activation
            
            # Initialize partial TP flag if not set
            if 'partial_tp_hit' not in self.state_data:
                self.state_data['partial_tp_hit'] = False
            if 'trailing_high' not in self.state_data:
                self.state_data['trailing_high'] = self.state_data['buy_price']
            
            # Update trailing high
            if current_price > self.state_data['trailing_high']:
                self.state_data['trailing_high'] = current_price
            
            # Check partial TP
            if is_partial_tp_hit and not self.state_data['partial_tp_hit']:
                logger.info(f"@PARTIAL_TP@ Partial TP hit for {symbol} (+{change_percent:.2f}%)")
                self._execute_partial_tp(symbol, current_price, partial_tp_size, is_dry_run)
                self.state_data['partial_tp_hit'] = True
                
                # Move to breakeven if enabled
                if move_to_breakeven and not self.state_data.get('is_breakeven', False):
                    self._set_breakeven()
            
            # Check trailing stop after partial TP
            if self.state_data['partial_tp_hit']:
                trailing_stop_price = self.state_data['trailing_high'] * (1 - trailing_callback / 100)
                if current_price <= trailing_stop_price:
                    logger.warning(f"@TRAILING_STOP@ Trailing stop hit for {symbol} at {current_price} (high: {self.state_data['trailing_high']})")
                    self._panic_sell()
                    return
            
            if is_dry_run:
                if is_tp_hit:
                    logger.info(f"@DRY_RUN_TP@ Virtual TP hit for {symbol} (+{change_percent:.2f}%)")
                    trade_profit = (self.state_data['target_sell_price'] - self.state_data['buy_price']) * self.state_data['amount']
                    self.session_profit += trade_profit
                    self.trade_db.log_trade(symbol, "sell", self.state_data['amount'], self.state_data['target_sell_price'], confidence=0.0)
                    self.state_data = {}
                    self.state = BotState.IDLE
                    return
                elif is_sl_hit:
                    logger.warning(f"@DRY_RUN_SL@ Virtual SL hit for {symbol} ({change_percent:.2f}%)")
                    self.last_loss_time = time.time()
                    self.state_data = {}
                    self.state = BotState.IDLE
                    return
            else:
                # Check balance before order operations
                coin_name = symbol.split('/')[0]
                try:
                    balance = self.exchange.fetch_balance()
                    uta_coins = balance.get('info', {}).get('result', {}).get('list', [{}])[0].get('coin', [])
                    coin_balance = 0.0
                    for c_data in uta_coins:
                        if c_data.get('coin') == coin_name:
                            coin_balance = safe_float(c_data.get('equity', 0))
                            break
                    if coin_balance <= 0.0001:
                        logger.warning(f"[@WARNING@] Zero balance detected for {coin_name}")
                        time.sleep(1.0)
                        balance_retry = self.exchange.fetch_balance()
                        uta_coins_retry = balance_retry.get('info', {}).get('result', {}).get('list', [{}])[0].get('coin', [])
                        retry_bal = 0.0
                        for cr_data in uta_coins_retry:
                            if cr_data.get('coin') == coin_name:
                                retry_bal = safe_float(cr_data.get('equity', 0))
                                break
                        if retry_bal <= 0.0001:
                            logger.info(f"@RESET@ Safe reset for {symbol}")
                            self.state_data = {}
                            self.state = BotState.IDLE
                            return
                except Exception as bal_err:
                    logger.error(f"Balance check error: {bal_err}")

                order_id = self.state_data['order_id']
                try:
                    order = self.exchange.fetch_order(order_id, symbol)
                except Exception as order_err:
                    if "last 500 orders" in str(order_err):
                        self.state_data = {}
                        self.state = BotState.IDLE
                        return
                    raise order_err

                if order['status'] == 'closed':
                    close_price = safe_float(order.get('price') or order.get('average', self.state_data['buy_price']))
                    trade_profit = (close_price - self.state_data['buy_price']) * self.state_data['amount']
                    self.session_profit += trade_profit
                    self.trade_db.log_trade(symbol, "sell", self.state_data['amount'], close_price, confidence=0.0)
                    logger.info(f"@PROFIT_TAKEN@ PROFIT! {symbol} +${trade_profit:.2f}")
                    self.state_data = {}
                    self.state = BotState.IDLE
                    return
                if is_sl_hit:
                    logger.warning(f"@STOP_LOSS_HIT@ SL hit for {symbol} ({change_percent:.2f}%)")
                    self.last_loss_time = time.time()
                    self._panic_sell()
                    return
            adaptive_timeout = self._calculate_adaptive_breakeven_timeout(symbol)
            if elapsed > adaptive_timeout and not self.state_data['is_breakeven']:
                logger.info(f"@BREAKEVEN_TIMEOUT@ Adaptive timeout reached: {elapsed}s (ATR-based: {adaptive_timeout}s)")
                self._set_breakeven()
        except Exception as e:
            logger.error(f"IN_POSITION state error: {e}")

    def _panic_sell(self) -> None:
        try:
            symbol = self.state_data['symbol']
            is_dry_run = self.trading_config.get('dry_run', False)
            
            # Cancel existing sell order if any
            try:
                self.exchange.cancel_order(self.state_data['order_id'], symbol)
                logger.info(f"@PANIC_CANCEL@ Canceled existing sell order: {self.state_data['order_id']}")
            except:
                pass
            
            time.sleep(0.5)
            amount = float(self.exchange.exchange.amount_to_precision(symbol, self.state_data['amount']))
            
            if is_dry_run:
                logger.info(f"@DRY_RUN_PANIC@ Virtual market sell: {amount} {symbol}")
                order_id = "virtual_panic_sell_12345"
            else:
                logger.info(f"@PANIC_SELL_SEND@ Market sell order: {amount} {symbol}")
                market_order = self.exchange.create_market_sell_order(symbol, amount)
                order_id = market_order.get('id')
            
            # Update state_data for EXITING state
            self.state_data['exit_order_id'] = order_id
            self.state_data['exit_time'] = time.time()
            self.state_data['exit_amount'] = amount
            self.state_data['exit_type'] = 'panic'
            
            self.state = BotState.EXITING
            logger.info(f"@STATE_CHANGED@ State -> EXITING for {symbol}, order_id: {order_id}")
        except Exception as e:
            logger.error(f"Panic sell error: {e}")
            self.state_data = {}
            self.state = BotState.IDLE

    def _handle_exiting_state(self):
        try:
            symbol = self.state_data['symbol']
            order_id = self.state_data['exit_order_id']
            exit_time = self.state_data['exit_time']
            exit_amount = self.state_data['exit_amount']
            exit_type = self.state_data.get('exit_type', 'panic')
            is_dry_run = self.trading_config.get('dry_run', False)
            timeout_sec = self.trading_config.get('order_execution_timeout_sec', 60)
            
            elapsed = time.time() - exit_time
            print(f"EXITING {symbol}: {elapsed:.1f}s / {timeout_sec}s @EXITING_MONITOR@", end='\r')
            
            if is_dry_run:
                # Dry run: simulate fill after 1 second
                if elapsed >= 1:
                    logger.info(f"@DRY_RUN_EXIT@ Virtual exit order filled for {symbol}")
                    self._on_exit_filled(symbol, exit_amount, self.state_data['buy_price'], exit_type, is_dry_run=True)
                return
            
            # Check order status
            try:
                order = self.exchange.fetch_order(order_id, symbol)
                status = order.get('status')
                
                if status in ['closed', 'filled']:
                    close_price = safe_float(order.get('average') or order.get('price'))
                    logger.info(f"@EXIT_FILLED@ Exit order filled: {symbol}, price: {close_price}")
                    self._on_exit_filled(symbol, exit_amount, self.state_data['buy_price'], exit_type, close_price, is_dry_run=False)
                elif status == 'canceled':
                    logger.warning(f"@EXIT_CANCELED@ Exit order was canceled: {symbol}")
                    self.state_data = {}
                    self.state = BotState.IDLE
                elif elapsed >= timeout_sec:
                    logger.warning(f"@EXIT_TIMEOUT@ Exit order timeout ({timeout_sec}s): {symbol}")
                    # Try to get actual fill from trades
                    try:
                        my_trades = self.exchange.exchange.fetch_my_trades(symbol, limit=10)
                        sell_trades = [t for t in my_trades if t.get('side') == 'sell' and (time.time() - (t.get('timestamp', 0) / 1000)) < 60]
                        if sell_trades:
                            close_price = safe_float(sell_trades[-1].get('price'))
                            logger.info(f"@EXIT_TRADE_FOUND@ Found recent sell trade: {close_price}")
                            self._on_exit_filled(symbol, exit_amount, self.state_data['buy_price'], exit_type, close_price, is_dry_run=False)
                            return
                    except:
                        pass
                    
                    # Fallback to market price
                    ws_data = self.ws_tickers_cache.get(symbol, {})
                    fallback_price = ws_data.get('bid') or safe_float(self.exchange.fetch_ticker(symbol)['bid'])
                    close_price = fallback_price if fallback_price > 0 else (self.state_data['buy_price'] * 0.98)
                    logger.warning(f"@EXIT_FALLBACK@ Using fallback price: {close_price}")
                    self._on_exit_filled(symbol, exit_amount, self.state_data['buy_price'], exit_type, close_price, is_dry_run=False)
            except Exception as e:
                logger.error(f"Error checking exit order: {e}")
                if elapsed >= timeout_sec:
                    self.state_data = {}
                    self.state = BotState.IDLE
        except Exception as e:
            logger.error(f"EXITING state error: {e}")
            self.state_data = {}
            self.state = BotState.IDLE

    def _on_exit_filled(self, symbol: str, amount: float, buy_price: float, exit_type: str, close_price: float = None, is_dry_run: bool = False):
        try:
            if close_price is None:
                if is_dry_run:
                    close_price = buy_price * 0.99  # Simulate 1% loss in dry run
                else:
                    ws_data = self.ws_tickers_cache.get(symbol, {})
                    close_price = ws_data.get('bid') or safe_float(self.exchange.fetch_ticker(symbol)['bid'])
                    close_price = close_price if close_price > 0 else (buy_price * 0.98)
            
            trade_profit = (close_price - buy_price) * amount
            self.session_profit += trade_profit
            
            if exit_type == 'panic':
                self.trade_db.log_trade(symbol, "sell_panic", amount, close_price, confidence=0.0)
                logger.warning(f"@PANIC_SELL_DONE@ Panic sell complete. Price: {close_price}, PnL: ${trade_profit:.2f}")
            else:
                self.trade_db.log_trade(symbol, "sell", amount, close_price, confidence=0.0)
                logger.info(f"@EXIT_DONE@ Exit complete. Price: {close_price}, PnL: ${trade_profit:.2f}")
            
            self.state_data = {}
            self.state = BotState.IDLE
        except Exception as e:
            logger.error(f"On exit filled error: {e}")
            self.state_data = {}
            self.state = BotState.IDLE

    def _execute_partial_tp(self, symbol: str, current_price: float, partial_tp_size_pct: float, is_dry_run: bool):
        """Execute partial take profit - sell portion of position"""
        try:
            original_amount = self.state_data['amount']
            partial_amount = original_amount * (partial_tp_size_pct / 100)
            remaining_amount = original_amount - partial_amount
            
            if is_dry_run:
                logger.info(f"@DRY_RUN_PARTIAL_TP@ Virtual partial TP: {symbol} sell {partial_amount} @ ${current_price}")
                self.state_data['amount'] = remaining_amount
                trade_profit = (current_price - self.state_data['buy_price']) * partial_amount
                self.session_profit += trade_profit
                self.trade_db.log_trade(symbol, "sell_partial", partial_amount, current_price, confidence=0.0)
                return
            
            # Cancel existing sell order
            try:
                self.exchange.cancel_order(self.state_data['order_id'], symbol)
                logger.info(f"@PARTIAL_CANCEL@ Canceled existing sell order for partial TP")
            except:
                pass
            
            # Place partial TP order
            partial_order = self.exchange.create_limit_sell_order(symbol, partial_amount, current_price)
            logger.info(f"@PARTIAL_TP_ORDER@ Partial TP order placed: {partial_order['id']} for {partial_amount} @ ${current_price}")
            
            # Update state with remaining amount
            self.state_data['amount'] = remaining_amount
            
            # Log partial TP profit
            trade_profit = (current_price - self.state_data['buy_price']) * partial_amount
            self.session_profit += trade_profit
            self.trade_db.log_trade(symbol, "sell_partial", partial_amount, current_price, confidence=0.0)
            logger.info(f"@PARTIAL_TP_DONE@ Partial TP complete. Profit: ${trade_profit:.2f}, Remaining: {remaining_amount}")
            
            # Re-create sell order for remaining position at breakeven or original TP
            if self.state_data.get('is_breakeven', False):
                breakeven_price = self.state_data['buy_price'] * 1.001
                new_order = self.exchange.create_limit_sell_order(symbol, remaining_amount, breakeven_price)
                self.state_data['order_id'] = new_order['id']
                logger.info(f"@PARTIAL_REORDER@ Breakeven order for remaining: {new_order['id']}")
            else:
                target_price = self.state_data['target_sell_price']
                new_order = self.exchange.create_limit_sell_order(symbol, remaining_amount, target_price)
                self.state_data['order_id'] = new_order['id']
                logger.info(f"@PARTIAL_REORDER@ TP order for remaining: {new_order['id']}")
                
        except Exception as e:
            logger.error(f"Partial TP error: {e}")

    def _set_breakeven(self):
        try:
            symbol = self.state_data['symbol']
            try:
                market_info = self.exchange.exchange.market(symbol)
                taker_fee = safe_float(market_info.get('taker', 0.001))
                maker_fee = safe_float(market_info.get('maker', 0.001))
            except:
                taker_fee, maker_fee = 0.001, 0.001
            
            breakeven_multiplier = 1.0 + (taker_fee + maker_fee) + 0.0002
            self.exchange.cancel_order(self.state_data['order_id'], symbol)
            time.sleep(0.5)
            
            buy_price = self.state_data['buy_price']
            raw_price = buy_price * breakeven_multiplier
            breakeven_price = float(self.exchange.exchange.price_to_precision(symbol, raw_price))
            
            breakeven_price = buy_price * 1.001  # Small profit to cover fees
            amount_str = self.exchange.amount_to_precision(symbol, amount)
            price_str = self.exchange.price_to_precision(symbol, breakeven_price)
            
            new_order = self.exchange.create_limit_sell_order(symbol, amount, breakeven_price)
            self.state_data['order_id'] = new_order['id']
            self.state_data['is_breakeven'] = True
            logger.info(f"@BREAKEVEN_SET@ Breakeven set for {symbol} at {breakeven_price}")
        except Exception as e:
            logger.error(f"Breakeven error: {e}")

    def _calculate_adaptive_breakeven_timeout(self, symbol: str) -> int:
        """
        Calculate adaptive breakeven timeout based on ATR
        Formula: timeout = (ATR / current_price) * 10000 * volatility_multiplier
        """
        try:
            # Get ATR for last 20 candles
            ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=20)
            if len(ohlcv) < 14:
                return 1200  # Fallback to 20 min
            
            # Calculate ATR
            atr = ATRAnalyzer.calculate_atr(ohlcv, period=14)
            current_price = safe_float(ohlcv[-1][4])
            
            if atr <= 0 or current_price <= 0:
                return 1200
            
            # Normalize ATR as % of price
            atr_pct = (atr / current_price) * 100
            
            # Base timeout: higher volatility (ATR) = faster exit needed
            # Low volatility (<0.5%) = longer wait (up to 40 min)
            # High volatility (>2%) = faster (up to 5 min)
            if atr_pct < 0.5:
                timeout_sec = 2400  # 40 minutes
            elif atr_pct < 1.0:
                timeout_sec = 1800  # 30 minutes
            elif atr_pct < 1.5:
                timeout_sec = 1200  # 20 minutes
            elif atr_pct < 2.0:
                timeout_sec = 900   # 15 minutes
            else:
                timeout_sec = 300   # 5 minutes
            
            # Can override via config
            config_timeout = self.trading_config.get('breakeven_timeout_sec', None)
            if config_timeout:
                timeout_sec = config_timeout
            
            logger.debug(f"@ATR_TIMEOUT@ ATR: {atr_pct:.2f}%, Breakeven timeout: {timeout_sec}s")
            return int(timeout_sec)
            
        except Exception as e:
            logger.error(f"ATR timeout calculation error: {e}")
            return 1200  # Fallback

    def _check_time_session(self) -> bool:
        if not self.trading_config.get('block_night_trading', False): return True
        current_hour = datetime.now().hour
        allowed_hours = self.trading_config.get('allowed_hours', list(range(24)))
        if current_hour not in allowed_hours:
            print(f"Time block: Hour {current_hour} not in allowed hours @TIME_BLOCK@ ", end='\r')
            return False
        return True

    def _calculate_btc_correlation(self, symbol: str) -> float:
        """Calculate Pearson correlation between symbol and BTC over specified period"""
        try:
            market_config = self.config.get_market_conditions_config()
            if not market_config.get('btc_correlation_filter', False):
                return 1.0  # Skip correlation check if disabled
            
            period = market_config.get('btc_correlation_period', 24)
            
            # Fetch OHLCV data for both symbol and BTC
            try:
                symbol_ohlcv = self.exchange.fetch_ohlcv(symbol, '1h', limit=period)
                btc_ohlcv = self.exchange.fetch_ohlcv('BTC/USDT', '1h', limit=period)
            except Exception as e:
                logger.warning(f"Failed to fetch OHLCV for correlation: {e}")
                return 1.0  # Assume good correlation if data fetch fails
            
            if len(symbol_ohlcv) < period or len(btc_ohlcv) < period:
                logger.warning(f"Insufficient data for correlation calculation")
                return 1.0
            
            # Extract close prices
            symbol_closes = [candle[4] for candle in symbol_ohlcv]
            btc_closes = [candle[4] for candle in btc_ohlcv]
            
            # Calculate percentage changes
            symbol_changes = [(symbol_closes[i] - symbol_closes[i-1]) / symbol_closes[i-1] * 100 for i in range(1, len(symbol_closes))]
            btc_changes = [(btc_closes[i] - btc_closes[i-1]) / btc_closes[i-1] * 100 for i in range(1, len(btc_closes))]
            
            # Calculate Pearson correlation
            import numpy as np
            correlation = np.corrcoef(symbol_changes, btc_changes)[0, 1]
            
            # Handle NaN
            if np.isnan(correlation):
                return 0.0
            
            return float(correlation)
            
        except Exception as e:
            logger.error(f"Correlation calculation error: {e}")
            return 1.0  # Assume good correlation on error

    def _check_btc_trend(self) -> bool:
        """Check if BTC trend is safe for trading based on recent price drop"""
        try:
            market_config = self.config.get_market_conditions_config()
            btc_filter = market_config.get('btc_filter', {})
            
            if not btc_filter.get('enabled', False):
                return True  # Skip check if disabled
            
            lookback_minutes = btc_filter.get('lookback_minutes', 5)
            max_drop_pct = btc_filter.get('max_drop_pct', 0.5)
            
            # Get BTC price from WebSocket cache or fetch from API
            btc_symbol = 'BTC/USDT'
            ws_data = self.ws_tickers_cache.get(btc_symbol, {})
            
            if not ws_data:
                # Fallback to REST API
                try:
                    btc_ticker = self.exchange.fetch_ticker(btc_symbol)
                    current_price = safe_float(btc_ticker['last'])
                except Exception as e:
                    logger.warning(f"Failed to fetch BTC price: {e}")
                    return True  # Allow trade if we can't get BTC price
            else:
                current_price = safe_float(ws_data.get('last'))
            
            # Fetch OHLCV data for the lookback period
            try:
                timeframe = '1m'
                limit = lookback_minutes
                btc_ohlcv = self.exchange.fetch_ohlcv(btc_symbol, timeframe, limit=limit)
                
                if len(btc_ohlcv) < 2:
                    logger.warning(f"Insufficient BTC OHLCV data")
                    return True
                
                # Get highest price in the lookback period
                high_price = max([candle[2] for candle in btc_ohlcv])
                
                # Calculate drop percentage
                drop_pct = ((high_price - current_price) / high_price) * 100
                
                if drop_pct > max_drop_pct:
                    logger.warning(f"@BTC_DROP_BLOCK@ BTC dropped {drop_pct:.2f}% in {lookback_minutes}m (limit: {max_drop_pct}%), blocking trades")
                    return False
                
                return True
                
            except Exception as e:
                logger.error(f"BTC trend check error: {e}")
                return True  # Allow trade on error
            
        except Exception as e:
            logger.error(f"BTC trend check error: {e}")
            return True  # Allow trade on error

    def _check_risk_limits(self) -> bool:
        try:
            cooldown_min = self.trading_config.get('cooldown_after_loss_minutes', 30)
            if time.time() - self.last_loss_time < (cooldown_min * 60):
                remaining = int((cooldown_min * 60) - (time.time() - self.last_loss_time))
                print(f"Cooldown after loss. Remaining: {remaining}s @COOLDOWN@ ", end='\r')
                return False
            daily_trades = self.trade_db.get_daily_trades_count()
            max_day_trades = self.trading_config.get('max_trades_per_day', 5)
            if daily_trades >= max_day_trades:
                print(f"Daily trade limit reached ({daily_trades}/{max_day_trades}) @DAY_LIMIT@ ", end='\r')
                return False
            return True
        except Exception: return True

    def _calculate_real_rvol(self, ohlcv) -> float:
        try:
            if len(ohlcv) < 20: return 1.0
            volumes = [safe_float(candle[5]) for candle in ohlcv]
            current_volume = volumes[-1]
            avg_volume = sum(volumes[-16:-1]) / 15
            return current_volume / avg_volume if avg_volume > 0 else 1.0
        except: return 1.0

    def _check_balance(self) -> bool:
        return True

    def _scan_for_entries(self) -> None:
        try:
            symbols = self.symbol_manager.get_symbols(refresh_scanner=True)
            tickers = self.ws_tickers_cache
            logger.info(f"@SCAN_START@ Scanning {len(symbols)} symbols, tickers cache: {len(tickers)}")
            if not tickers: 
                logger.warning("@SCAN_WARN@ No tickers in cache")
                return
            
            # Check if BTC trend detection is enabled in config
            market_config = self.config.get_market_conditions_config()
            btc_trend = "neutral"
            if not market_config.get('btc_trend_detection', True):
                logger.debug("BTC trend detection disabled in config")
            else:
                try:
                    btc_ohlcv_1h = self.exchange.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=2)
                    if len(btc_ohlcv_1h) >= 2:
                        btc_open = safe_float(btc_ohlcv_1h[-2][1])
                        btc_close = safe_float(btc_ohlcv_1h[-1][4])
                        btc_change_1h = ((btc_close - btc_open) / btc_open) * 100
                        if btc_change_1h < -0.8: btc_trend = "bearish"
                        elif btc_change_1h > 0.8: btc_trend = "bullish"
                except: pass
            try:
                btc_ohlcv_15m = self.exchange.fetch_ohlcv('BTC/USDT', timeframe='5m', limit=4)
                if len(btc_ohlcv_15m) >= 3:
                    btc_high_15m = max([safe_float(c[2]) for c in btc_ohlcv_15m])
                    btc_current = safe_float(tickers.get('BTC/USDT', {}).get('ask') or btc_ohlcv_15m[-1][4])
                    btc_drop_15m = ((btc_current - btc_high_15m) / btc_high_15m) * 100
                    crash_limit = self.trading_config.get('btc_crash_15m_limit', -2.0)
                    if btc_drop_15m <= crash_limit:
                        print(f"Crash block: BTC dropping {btc_drop_15m:.2f}% @BTC_CRASH_BLOCK@ ", end='\r')
                        return
            except: pass
            print(f"Scanning market... BTC Trend: [{btc_trend.upper()}] @SCAN_WS@ ", end='\r')
            
            # TANK MODE: Check if enabled
            tank_mode = self.trading_config.get('tank_mode', False)
            
            for symbol in symbols:
                if symbol not in tickers: continue
                try:
                    price_now = safe_float(tickers[symbol]['ask'])
                    if symbol not in self.price_history or not isinstance(self.price_history[symbol], list):
                        self.price_history[symbol] = [price_now, time.time()]
                        continue
                    if len(self.price_history[symbol]) < 2:
                        self.price_history[symbol] = [price_now, time.time()]
                        continue
                    if price_now > self.price_history[symbol][0]:
                        self.price_history[symbol] = [price_now, time.time()]
                        continue
                    if time.time() - self.price_history[symbol][1] > 900:
                        self.price_history[symbol] = [price_now, time.time()]
                        continue
                    drop = ((self.price_history[symbol][0] - price_now) / self.price_history[symbol][0]) * 100
                    if drop >= self.trading_config.get('drop_threshold', 0.65):
                        try: 
                            ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=60)
                        except: 
                            continue
                        real_rvol = self._calculate_real_rvol(ohlcv)
                        min_rvol = self.trading_config.get('min_rvol_threshold', 1.5)
                        
                        # TANK MODE: Stricter RVOL threshold
                        if tank_mode and real_rvol < 2.0:
                            logger.debug(f"@TANK_FILTER@ RVOL too low for tank mode: {real_rvol:.1f}x < 2.0x")
                            continue
                        
                        if real_rvol < min_rvol: continue
                        
                        if self.indicators_enabled:
                            analysis = analyzer.complete_analysis(
                                ohlcv_data=ohlcv,
                                current_price=price_now,
                                market_volatility=1.0,
                                btc_trend=btc_trend,
                                btc_trend_detection_enabled=market_config.get('btc_trend_detection', True)
                            )
                            if analysis['status'] != 'ok': continue
                            
                            # TANK MODE: Check for tank block reason
                            if tank_mode and 'tank_block_reason' in analysis:
                                logger.info(f"@TANK_BLOCK@ Signal blocked: {analysis['tank_block_reason']}")
                                continue
                            
                            base_threshold = self.trading_config.get('min_confidence_threshold', 60.0)
                            
                            # TANK MODE: Higher threshold
                            if tank_mode:
                                base_threshold = 85.0
                                if btc_trend == "bearish":
                                    logger.info(f"@TANK_BTC@ BTC bearish, blocking entry")
                                    continue
                            
                            if analysis['recommendation'] in ['STRONG_BUY', 'BUY'] and analysis.get('confidence_score', 0) >= base_threshold:
                                # Check BTC trend (drop filter)
                                if not self._check_btc_trend():
                                    continue
                                # Check BTC correlation if enabled
                                btc_correlation = self._calculate_btc_correlation(symbol)
                                correlation_threshold = market_config.get('btc_correlation_threshold', 0.5)
                                if btc_correlation < correlation_threshold:
                                    logger.info(f"@CORRELATION_FILTER@ {symbol} BTC correlation {btc_correlation:.2f} < {correlation_threshold}, skipping")
                                    continue
                                logger.info(f"@SIGNAL_APPROVED@ Signal approved for {symbol} (RVOL: {real_rvol:.1f}x, Confidence: {analysis.get('confidence_score', 0):.1f}%)")
                                self._enter_trade(symbol, price_now, tickers)
                                return
                        else:
                            self._enter_trade(symbol, price_now, tickers)
                            return
                except: continue
        except Exception as e: logger.error(f"Scan error: {e}")

    def _enter_trade(self, symbol: str, price: float, tickers: Dict) -> None:
        try:
            buy_price = safe_float(tickers[symbol]['ask'])
            slot_size = self.trading_config['slot_size']
            amount_target = float(self.exchange.exchange.amount_to_precision(symbol, slot_size / buy_price))
            is_dry_run = self.trading_config.get('dry_run', False)
            
            if is_dry_run:
                logger.info(f"@DRY_RUN_BUY@ Virtual buy: {symbol} ${slot_size} @ ${buy_price}")
                order_id = "virtual_buy_12345"
            else:
                logger.info(f"@BUY_ORDER_SEND@ Limit buy order: {symbol} ${slot_size} @ ${buy_price}")
                order = self.exchange.create_limit_buy_order(symbol, amount_target, buy_price)
                order_id = order['id']
            
            self.state_data = {
                'symbol': symbol,
                'buy_price': buy_price,
                'buy_time': time.time(),
                'order_id': order_id,
                'amount_target': amount_target,
                'is_dry_run': is_dry_run
            }
            
            self.state = BotState.BUYING
            logger.info(f"@STATE_CHANGED@ State -> BUYING for {symbol}, order_id: {order_id}")
        except Exception as e:
            logger.error(f"Entry error: {e}")
            self.state = BotState.IDLE

    def _handle_buying_state(self):
        try:
            symbol = self.state_data['symbol']
            order_id = self.state_data['order_id']
            buy_time = self.state_data['buy_time']
            is_dry_run = self.state_data['is_dry_run']
            timeout_sec = self.trading_config.get('order_execution_timeout_sec', 60)
            
            elapsed = time.time() - buy_time
            print(f"BUYING {symbol}: {elapsed:.1f}s / {timeout_sec}s @BUYING_MONITOR@", end='\r')
            
            if is_dry_run:
                # Dry run: simulate fill after 2 seconds
                if elapsed >= 2:
                    logger.info(f"@DRY_RUN_FILL@ Virtual buy order filled for {symbol}")
                    self._on_buy_filled(symbol, self.state_data['amount_target'], self.state_data['buy_price'], is_dry_run=True)
                return
            
            # Check order status
            try:
                order = self.exchange.fetch_order(order_id, symbol)
                status = order.get('status')
                filled = safe_float(order.get('filled', 0))
                
                if status == 'closed':
                    logger.info(f"@BUY_FILLED@ Buy order filled: {symbol}, amount: {filled}")
                    self._on_buy_filled(symbol, filled, self.state_data['buy_price'], is_dry_run=False)
                elif status == 'canceled':
                    logger.warning(f"@BUY_CANCELED@ Buy order was canceled: {symbol}")
                    self.state_data = {}
                    self.state = BotState.IDLE
                elif elapsed >= timeout_sec:
                    logger.warning(f"@BUY_TIMEOUT@ Buy order timeout ({timeout_sec}s): {symbol}")
                    try:
                        self.exchange.cancel_order(order_id, symbol)
                        logger.info(f"@BUY_CANCEL@ Canceled timeout order: {order_id}")
                    except Exception as cancel_err:
                        logger.error(f"Cancel order error: {cancel_err}")
                    self.state_data = {}
                    self.state = BotState.IDLE
            except Exception as e:
                logger.error(f"Error checking buy order: {e}")
                if elapsed >= timeout_sec:
                    self.state_data = {}
                    self.state = BotState.IDLE
        except Exception as e:
            logger.error(f"BUYING state error: {e}")
            self.state_data = {}
            self.state = BotState.IDLE

    def _on_buy_filled(self, symbol: str, filled: float, buy_price: float, is_dry_run: bool):
        try:
            filled_usd = filled * buy_price
            min_fill_usd = self.trading_config.get('min_exchange_limit', 5.2)
            
            if filled_usd < min_fill_usd and not is_dry_run:
                logger.warning(f"@BUY_PARTIAL@ Partial fill too small: ${filled_usd:.2f} < ${min_fill_usd}")
                self.state_data = {}
                self.state = BotState.IDLE
                return
            
            # Calculate take profit price
            take_profit_pct = self.trading_config.get('take_profit', 1.5)
            sell_price = float(self.exchange.exchange.price_to_precision(symbol, buy_price * (1 + (take_profit_pct / 100))))
            
            if sell_price <= buy_price:
                try:
                    market_info = self.exchange.exchange.market(symbol)
                    tick_size = safe_float(market_info.get('info', {}).get('priceFilter', {}).get('tickSize')) or 0.0001
                    if tick_size >= 1.0: tick_size = 10 ** -int(tick_size)
                    sell_price += tick_size
                    sell_price = float(self.exchange.exchange.price_to_precision(symbol, sell_price))
                except: pass
            
            # Create sell order
            if is_dry_run:
                logger.info(f"@DRY_RUN_SELL@ Virtual TP: Sell {filled} {symbol} @ ${sell_price}")
                sell_order_id = "virtual_sell_67890"
                safe_amount = filled
            else:
                time.sleep(2.5)
                balance = self.exchange.fetch_balance()
                actual_qty = safe_float(balance['free'].get(symbol.split('/')[0], 0))
                safe_amount = float(self.exchange.exchange.amount_to_precision(symbol, actual_qty if actual_qty > 0 else filled))
                logger.info(f"@TAKE_PROFIT_SEND@ Limit sell order: {safe_amount} {symbol} @ ${sell_price}")
                sell_order = self.exchange.create_limit_sell_order(symbol, safe_amount, sell_price)
                sell_order_id = sell_order['id']
            
            # Update state_data for IN_POSITION
            self.state_data = {
                'symbol': symbol,
                'buy_price': buy_price,
                'buy_time': time.time(),
                'order_id': sell_order_id,
                'amount': safe_amount,
                'target_sell_price': sell_price,
                'is_breakeven': False
            }
            
            self.trade_db.log_trade(symbol, "buy", safe_amount, buy_price, confidence=100.0)
            logger.info(f"@STATE_CHANGED@ State -> IN_POSITION for {symbol}")
            self.state = BotState.IN_POSITION
        except Exception as e:
            logger.error(f"On buy filled error: {e}")
            self.state_data = {}
            self.state = BotState.IDLE


def main():
    bot = TradingBot()
    bot.run()


if __name__ == "__main__":
    main()
