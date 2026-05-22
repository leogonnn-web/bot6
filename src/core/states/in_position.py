"""IN_POSITION state handler + panic sell + partial TP."""
import time
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float

from ..state_enum import BotState


class InPositionStateMixin:
    def _handle_in_position_state(self):
        try:
            # HYDRA-NET: Skip normal monitoring if grid is active
            if self.state_data.get('is_grid_active', False):
                return  # Grid synchronization happens in main loop

            symbol = self.state_data['symbol']
            trading_config = self.config.get_trading_config()
            is_dry_run = trading_config.get('dry_run', False)
            ws_data = self.ws_tickers_cache.get(symbol, {})
            current_price = ws_data.get('last') or safe_float(self.exchange.fetch_ticker(symbol)['last'])

            change_percent = ((current_price - self.state_data['buy_price']) / self.state_data['buy_price']) * 100
            elapsed = time.time() - self.state_data['buy_time']
            take_profit_pct = trading_config.get('take_profit', 1.5)

            # Partial TP settings
            position_value_usdt = self.state_data['amount'] * self.state_data['buy_price']
            partial_tp_enabled = trading_config.get('partial_tp_activation_pct', 1.0) > 0 and position_value_usdt >= 10.0
            partial_tp_activation = trading_config.get('partial_tp_activation_pct', 1.0)
            partial_tp_size = trading_config.get('partial_tp_size_pct', 50.0)
            move_to_breakeven = trading_config.get('move_to_breakeven', True)
            trailing_callback = trading_config.get('trailing_callback_pct', 0.5)

            print(f"Position {symbol}: {change_percent:.2f}% | Time: {int(elapsed)}s @MONITOR_WS@", end='\r')
            is_tp_hit = change_percent >= take_profit_pct
            is_sl_hit = change_percent <= -trading_config['panic_stop']
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
                    logger.error(f"@BALANCE_ERROR@ Balance check error for {symbol}: {bal_err}", exc_info=True)

                order_id = self.state_data['order_id']
                try:
                    order = self.exchange.fetch_order(order_id, symbol)
                except Exception as order_err:
                    if "last 500 orders" in str(order_err):
                        logger.warning(f"@ORDER_ERROR@ Order history limit reached for {symbol}, resetting state")
                        self.state_data = {}
                        self.state = BotState.IDLE
                        return
                    logger.error(f"@ORDER_ERROR@ Failed to fetch order {order_id} for {symbol}: {order_err}", exc_info=True)
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
            logger.error(f"@IN_POSITION_ERROR@ IN_POSITION state error for {self.state_data.get('symbol', 'unknown')}: {e}", exc_info=True)

    def _panic_sell(self) -> None:
        try:
            symbol = self.state_data['symbol']
            trading_config = self.config.get_trading_config()
            is_dry_run = trading_config.get('dry_run', False)

            # Cancel existing sell order if any
            try:
                self.exchange.cancel_order(self.state_data['order_id'], symbol)
                logger.info(f"@PANIC_CANCEL@ Canceled existing sell order: {self.state_data['order_id']}")
            except Exception as e:
                logger.debug(f"@PANIC_CANCEL_WARN@ Failed to cancel sell order: {e}")

            time.sleep(0.5)
            amount = float(self.exchange.exchange.amount_to_precision(symbol, self.state_data['amount']))

            if is_dry_run:
                logger.info(f"@DRY_RUN_PANIC@ Virtual market sell: {amount} {symbol}")
                order_id = "virtual_panic_sell_12345"
            else:
                logger.info(f"@PANIC_SELL_SEND@ Market sell order: {amount} {symbol}")
                market_order = self.order_manager.market_sell(symbol, amount)
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
            except Exception as e:
                logger.debug(f"@PARTIAL_CANCEL_WARN@ Failed to cancel order before partial TP: {e}")

            # Place partial TP order
            partial_order = self.order_manager.sell(symbol, partial_amount, current_price)
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
                new_order = self.order_manager.sell(symbol, remaining_amount, breakeven_price)
                self.state_data['order_id'] = new_order['id']
                logger.info(f"@PARTIAL_REORDER@ Breakeven order for remaining: {new_order['id']}")
            else:
                target_price = self.state_data['target_sell_price']
                new_order = self.order_manager.sell(symbol, remaining_amount, target_price)
                self.state_data['order_id'] = new_order['id']
                logger.info(f"@PARTIAL_REORDER@ TP order for remaining: {new_order['id']}")

        except Exception as e:
            logger.error(f"Partial TP error: {e}")
