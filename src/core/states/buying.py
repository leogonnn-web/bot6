"""BUYING state handler and trade entry."""
import time
import sys
import os
from typing import Dict

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float
from metrics import METRICS

from ..state_enum import BotState


class BuyingStateMixin:
    def _on_buy_filled(self, symbol: str, amount: float, buy_price: float, is_dry_run: bool = False):
        """Handle buy order fill and transition to IN_POSITION"""
        try:
            logger.info(f"@BUY_FILLED@ Buy filled: {symbol}, amount: {amount}, price: {buy_price}")
            
            # Log the trade and get real trade_id
            trade_id = self.trade_db.log_trade(symbol, "buy", amount, buy_price, confidence=0.0)
            
            # If we have dispatcher features from scan time, log them with real trade_id
            df = self.state_data.get('dispatcher_features', {})
            logger.info(f"@DISPATCHER_DEBUG@ df_empty={not df} df_keys={list(df.keys()) if df else []} trade_id={trade_id}")
            if df and trade_id > 0:
                try:
                    self.trade_db.log_dispatcher_features(
                        trade_id=trade_id,
                        symbol=symbol,
                        confidence=df.get('confidence', 0.0),
                        rvol_spike=df.get('rvol_spike', 0.0),
                        rvol_local=df.get('rvol_local', 0.0),
                        dump_depth=df.get('dump_depth', 0.0),
                        obi_skew=df.get('obi_skew', 0.0),
                        btc_1h=df.get('btc_1h', 0.0),
                        score=df.get('score', 0.0),
                        mode=df.get('mode', 'normal'),
                    )
                    logger.info(f"@DISPATCHER_LINK@ Features linked to trade_id={trade_id}")
                except Exception as df_err:
                    logger.error(f"@DISPATCHER_LINK_WARN@ {df_err}")
            
            # Update metrics
            METRICS.order_total.labels(side='buy', strategy='hydra_net').inc()
            METRICS.active_positions.set(1)
            
            # Calculate target sell price based on take profit config
            trading_config = self.config.get_trading_config()
            # If grid already set target_sell_price, preserve it (hydra_net TP)
            existing_tp = self.state_data.get('target_sell_price')
            if existing_tp:
                target_sell_price = existing_tp
            else:
                take_profit_pct = trading_config.get('take_profit', 1.5)
                target_sell_price = buy_price * (1 + take_profit_pct / 100)

            # Transition to IN_POSITION state, preserve grid state_data keys
            new_state = {
                'symbol': symbol,
                'buy_price': buy_price,
                'amount': amount,
                'buy_time': time.time(),
                'is_dry_run': is_dry_run,
                'target_sell_price': target_sell_price,
                'is_breakeven': False,
                'partial_tp_hit': False,
                'trailing_high': buy_price
            }
            # Preserve grid-specific keys if they exist
            for key in ['entry_price', 'current_level', 'total_cost', 'total_qty', 'order_id', 'is_grid_active', 'dispatcher_features']:
                if key in self.state_data:
                    new_state[key] = self.state_data[key]

            # Live single-shot (non-grid): place the resting take-profit limit SELL
            # now and track its id. Without this, order_id would remain the already
            # filled BUY order, and the IN_POSITION live handler would misread it as a
            # completed sell — instantly closing the position at entry price (fee bleed).
            if not is_dry_run and not new_state.get('is_grid_active', False):
                try:
                    # Fetch real available balance of the base coin (e.g. SHIB) to account
                    # for trading fees deducted in the base currency on buy. Bybit spot
                    # deducts 0.1% fee from the bought amount, so selling the full 'amount'
                    # would fail with retCode 170131 "Insufficient balance".
                    coin_name = symbol.split('/')[0]
                    try:
                        balance = self.exchange.fetch_balance()
                        # Bybit UTA format: result.list[0].coin[].equity
                        uta_coins = balance.get('info', {}).get('result', {}).get('list', [{}])[0].get('coin', [])
                        real_balance = 0.0
                        for c_data in uta_coins:
                            if c_data.get('coin') == coin_name:
                                real_balance = safe_float(c_data.get('equity', 0))
                                break
                        if real_balance > 0.0001:
                            sell_amount = float(self.exchange.exchange.amount_to_precision(symbol, real_balance))
                            logger.info(f"@REAL_BALANCE@ {coin_name} real balance: {real_balance}, sell_amount: {sell_amount}")
                        else:
                            # Fallback: deduct 0.15% fee margin from bought amount
                            sell_amount = float(self.exchange.exchange.amount_to_precision(symbol, amount * (1 - 0.0015)))
                            logger.warning(f"@BALANCE_FALLBACK@ Using fee-adjusted amount: {sell_amount}")
                    except Exception as bal_err:
                        logger.warning(f"@BALANCE_FETCH_ERROR@ Failed to fetch real balance: {bal_err}, using fee-adjusted fallback")
                        sell_amount = float(self.exchange.exchange.amount_to_precision(symbol, amount * (1 - 0.0015)))

                    sell_order = self.order_manager.sell(symbol, sell_amount, target_sell_price)
                    new_state['order_id'] = sell_order.get('id')
                    new_state['amount'] = sell_amount
                    logger.info(
                        f"@TP_ORDER@ Placed take-profit sell: {sell_amount} {symbol} "
                        f"@ ${target_sell_price} id={new_state['order_id']}"
                    )
                except Exception as tp_err:
                    logger.error(
                        f"@TP_ORDER_ERROR@ Failed to place take-profit sell for {symbol}: {tp_err}",
                        exc_info=True,
                    )
                    # Do NOT reset to IDLE on TP placement failure. Keep IN_POSITION
                    # with order_id=None so the IN_POSITION handler can retry with real balance.
                    new_state['order_id'] = None

            self.state_data = new_state
            self.state = BotState.IN_POSITION
            logger.info(f"@STATE_CHANGED@ State -> IN_POSITION for {symbol}")
        except Exception as e:
            logger.error(f"@ON_BUY_FILL_ERROR@ Error handling buy fill: {e}", exc_info=True)
            # Do NOT reset to IDLE on error — we hold the asset and must manage it.
            # Transition to IN_POSITION with minimal state so the handler can retry.
            self.state_data = {
                'symbol': symbol,
                'buy_price': buy_price,
                'amount': amount,
                'buy_time': time.time(),
                'is_dry_run': is_dry_run,
                'target_sell_price': target_sell_price,
                'order_id': None,  # No sell order placed yet
            }
            self.state = BotState.IN_POSITION

    def _enter_trade(self, symbol: str, price: float, tickers: Dict, dispatcher_features: Dict = None) -> None:
        try:
            logger.info(f"@ENTER_TRADE_DEBUG@ {symbol} df_empty={not dispatcher_features} keys={list(dispatcher_features.keys()) if dispatcher_features else []}")
            trading_config = self.config.get_trading_config()
            is_dry_run = trading_config.get('dry_run', False)

            # Single Position Invariant: prevent multiple concurrent positions
            # If we hold any non-USDT coin above dust amount, do NOT buy a new coin.
            # This prevents the cascade of multiple buys when sell orders fail.
            if not is_dry_run:
                try:
                    balance = self.exchange.fetch_balance()
                    uta_coins = balance.get('info', {}).get('result', {}).get('list', [{}])[0].get('coin', [])
                    dust_threshold = 0.0001  # Minimum amount to consider as a position
                    for c_data in uta_coins:
                        coin = c_data.get('coin', '')
                        equity = safe_float(c_data.get('equity', 0))
                        if coin != 'USDT' and equity > dust_threshold:
                            logger.warning(
                                f"@SINGLE_POSITION_BLOCK@ Already holding {equity} {coin} (above dust {dust_threshold}). "
                                f"Refusing to buy {symbol} to prevent multiple positions."
                            )
                            return  # Block new buy
                except Exception as bal_err:
                    logger.warning(f"@SINGLE_POSITION_CHECK_ERROR@ Failed to check balance: {bal_err}. Proceeding with caution.")

            buy_price = safe_float(tickers[symbol]['ask'])
            slot_size = trading_config['slot_size']
            amount_target = float(self.exchange.exchange.amount_to_precision(symbol, slot_size / buy_price))

            if is_dry_run:
                logger.info(f"@DRY_RUN_BUY@ Virtual buy: {symbol} ${slot_size} @ ${buy_price}")
                order_id = "virtual_buy_12345"
            else:
                logger.info(f"@BUY_ORDER_SEND@ Limit buy order: {symbol} ${slot_size} @ ${buy_price}")
                order = self.order_manager.buy(symbol, amount_target, buy_price)
                order_id = order['id']

            self.state_data = {
                'symbol': symbol,
                'buy_price': buy_price,
                'buy_time': time.time(),
                'order_id': order_id,
                'amount_target': amount_target,
                'is_dry_run': is_dry_run,
                'dispatcher_features': dispatcher_features or {},
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
            trading_config = self.config.get_trading_config()
            timeout_sec = trading_config.get('order_execution_timeout_sec', 60)

            elapsed = time.time() - buy_time
            print(f"BUYING {symbol}: {elapsed:.1f}s / {timeout_sec}s @BUYING_MONITOR@", end='\r')

            if is_dry_run:
                # Check if this is a grid order
                is_grid = self.state_data.get('is_grid_active', False)

                if is_grid:
                    # For grid orders: synchronize grid movement
                    if self.state_data.get('is_grid_active', False):
                        self._synchronize_grid_network()
                    # Simulate fill after 2 seconds for grid
                    if elapsed >= 2:
                        logger.info(f"@DRY_RUN_GRID_FILL@ Virtual grid order filled for {symbol}")
                        self._on_grid_level_filled({'id': order_id, 'status': 'closed', 'filled': self.state_data['amount'], 'average': self.state_data['buy_price']})
                else:
                    # Normal order: simulate fill after 1 second
                    if elapsed >= 1:
                        logger.info(f"@DRY_RUN_BUY_FILL@ Virtual buy order filled for {symbol}")
                        self._on_buy_filled(symbol, self.state_data['amount'], self.state_data['buy_price'], is_dry_run=True)
                return

            # Check order status
            try:
                order = self.exchange.fetch_order(order_id, symbol)
                status = order.get('status')

                if status in ['closed', 'filled']:
                    filled = safe_float(order.get('filled') or order.get('amount'))
                    avg_price = safe_float(order.get('average') or order.get('price'))
                    logger.info(f"@BUY_FILLED@ Buy order filled: {symbol}, filled: {filled}, avg_price: {avg_price}")

                    # Check if this is a grid order
                    is_grid = self.state_data.get('is_grid_active', False)
                    if is_grid:
                        self._on_grid_level_filled(order)
                    else:
                        self._on_buy_filled(symbol, filled, avg_price, is_dry_run=False)
                elif status == 'canceled':
                    logger.warning(f"@BUY_CANCELED@ Buy order was canceled: {symbol}")
                    self.state_data = {}
                    self.state = BotState.IDLE
                elif elapsed >= timeout_sec:
                    logger.warning(f"@BUY_TIMEOUT@ Buy order timeout ({timeout_sec}s): {symbol}")
                    # Try to get actual fill from trades
                    try:
                        my_trades = self.exchange.exchange.fetch_my_trades(symbol, limit=10)
                        buy_trades = [t for t in my_trades if t.get('side') == 'buy' and (time.time() - (t.get('timestamp', 0) / 1000)) < 60]
                        if buy_trades:
                            filled = safe_float(buy_trades[-1].get('amount'))
                            avg_price = safe_float(buy_trades[-1].get('price'))
                            logger.info(f"@BUY_TRADE_FOUND@ Found recent buy trade: {avg_price}")
                            is_grid = self.state_data.get('is_grid_active', False)
                            if is_grid:
                                self._on_grid_level_filled({'id': order_id, 'status': 'closed', 'filled': filled, 'average': avg_price})
                            else:
                                self._on_buy_filled(symbol, filled, avg_price, is_dry_run=False)
                            return
                    except Exception as trade_err:
                        logger.error(f"@BUY_TRADE_ERROR@ Failed to fetch trades for {symbol}: {trade_err}")

                    # Fallback: cancel order and reset
                    try:
                        self.exchange.cancel_order(order_id, symbol)
                        logger.info(f"@BUY_CANCEL@ Canceled timed out order: {order_id}")
                    except Exception as cancel_err:
                        logger.error(f"@BUY_CANCEL_ERROR@ Failed to cancel order {order_id}: {cancel_err}")
                    self.state_data = {}
                    self.state = BotState.IDLE
            except Exception as e:
                logger.error(f"@BUY_ORDER_ERROR@ Error checking buy order {order_id} for {symbol}: {e}", exc_info=True)
                if elapsed >= timeout_sec:
                    logger.warning(f"@BUY_TIMEOUT_RESET@ Resetting state after timeout and error")
                    self.state_data = {}
                    self.state = BotState.IDLE
        except Exception as e:
            logger.error(f"@BUYING_STATE_ERROR@ BUYING state error for {self.state_data.get('symbol', 'unknown')}: {e}", exc_info=True)
            self.state_data = {}
            self.state = BotState.IDLE
