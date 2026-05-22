"""EXITING state handler."""
import time
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float

from ..state_enum import BotState


class ExitingStateMixin:
    def _handle_exiting_state(self):
        try:
            symbol = self.state_data['symbol']
            order_id = self.state_data['exit_order_id']
            exit_time = self.state_data['exit_time']
            exit_amount = self.state_data['exit_amount']
            exit_type = self.state_data.get('exit_type', 'panic')
            trading_config = self.config.get_trading_config()
            is_dry_run = trading_config.get('dry_run', False)
            timeout_sec = trading_config.get('order_execution_timeout_sec', 60)

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
                    except Exception as e:
                        logger.debug(f"@EXIT_TRADE_WARN@ Failed to fetch exit trades: {e}")

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
            # Update cooldown for this symbol
            self._update_symbol_cooldown(symbol)

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
