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
                    real_balance = self.exchange.get_coin_balance(coin_name)
                    if real_balance is not None and real_balance > self._dust_threshold(symbol):
                        sell_amount = float(self.exchange.exchange.amount_to_precision(symbol, real_balance))
                        logger.info(f"@REAL_BALANCE@ {coin_name} real balance: {real_balance}, sell_amount: {sell_amount}")
                    else:
                        # Balance unknown (None) or not yet settled: deduct 0.15% fee margin
                        # from the bought amount so the sell is not rejected with 170131.
                        sell_amount = float(self.exchange.exchange.amount_to_precision(symbol, amount * (1 - 0.0015)))
                        logger.warning(f"@BALANCE_FALLBACK@ {coin_name} balance={real_balance}; using fee-adjusted amount: {sell_amount}")

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
            # target_sell_price may not have been computed yet if the failure was early.
            tp_fallback = self.state_data.get('target_sell_price')
            if not tp_fallback:
                try:
                    tp_pct = float(self.config.get_trading_config().get('take_profit', 1.5))
                except Exception:
                    tp_pct = 1.5
                tp_fallback = buy_price * (1 + tp_pct / 100) if buy_price > 0 else None
            self.state_data = {
                'symbol': symbol,
                'buy_price': buy_price,
                'amount': amount,
                'buy_time': time.time(),
                'is_dry_run': is_dry_run,
                'target_sell_price': tp_fallback,
                'order_id': None,  # No sell order placed yet
                'dispatcher_features': self.state_data.get('dispatcher_features', {}),
            }
            self.state = BotState.IN_POSITION

    def _single_position_ok(self, symbol: str) -> bool:
        """True only when the exchange CONFIRMS we hold no non-USDT coin above dust."""
        holdings = self.exchange.get_non_usdt_holdings()
        if holdings is None:
            logger.warning(f"@SINGLE_POSITION_UNKNOWN@ Balance unavailable -> refusing to buy {symbol}")
            return False
        for coin, amount in holdings.items():
            if amount > self._dust_threshold(f"{coin}/USDT"):
                logger.warning(
                    f"@SINGLE_POSITION_BLOCK@ Already holding {amount} {coin}. "
                    f"Refusing to buy {symbol} to prevent multiple positions."
                )
                return False
        return True

    def _recent_buy_fill(self, symbol: str, since_ts: float):
        """(qty, avg_price) of our BUY trades on `symbol` since `since_ts`, or (0, 0).

        Sums ALL matching trades (a single order can fill in several prints).
        Returns (None, None) when the trade history could not be read.
        """
        try:
            trades = self.exchange.exchange.fetch_my_trades(symbol, limit=50)
        except Exception as e:
            logger.warning(f"@MY_TRADES_UNKNOWN@ {symbol}: {e}")
            return None, None
        qty = 0.0
        cost = 0.0
        for t in trades:
            if t.get('side') != 'buy':
                continue
            if safe_float(t.get('timestamp')) / 1000.0 < since_ts - 5:
                continue
            a = safe_float(t.get('amount'))
            p = safe_float(t.get('price'))
            if a > 0 and p > 0:
                qty += a
                cost += a * p
        return (qty, cost / qty) if qty > 0 else (0.0, 0.0)

    def _resolve_buy_by_balance(self, symbol: str, order_id: str, reason: str) -> None:
        """Order status could not be trusted: cancel what may still rest, then let the
        exchange balance decide whether we are in a position."""
        try:
            self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            logger.debug(f"@RESOLVE_CANCEL_WARN@ {order_id}: {e}")
        coin = symbol.split('/')[0]
        held = self.exchange.get_coin_balance(coin)
        if held is None:
            logger.warning(f"@BUY_RESOLVE_DEFERRED@ {symbol} ({reason}): balance unknown, staying in BUYING")
            return
        if held <= self._dust_threshold(symbol):
            self._transition_to_idle(f'buy_{reason}_not_held')
            return
        qty, avg = self._recent_buy_fill(symbol, self.state_data.get('buy_time', 0.0))
        price = avg if (avg or 0) > 0 else safe_float(self.state_data.get('buy_price'))
        logger.warning(f"@BUY_RESOLVED_BY_BALANCE@ {symbol} ({reason}): holding {held} {coin}, price={price}")
        # A grid that lost track of its orders is closed out as a plain position.
        self.state_data['is_grid_active'] = False
        self._on_buy_filled(symbol, held, price, is_dry_run=False)

    def _enter_trade(self, symbol: str, price: float, tickers: Dict, dispatcher_features: Dict = None) -> None:
        try:
            logger.info(f"@ENTER_TRADE_DEBUG@ {symbol} df_empty={not dispatcher_features} keys={list(dispatcher_features.keys()) if dispatcher_features else []}")
            trading_config = self.config.get_trading_config()
            is_dry_run = trading_config.get('dry_run', False)

            # Single Position Invariant: prevent multiple concurrent positions.
            # FAIL-CLOSED: if the balance cannot be read we do NOT buy. The previous
            # "proceed with caution" made a transient API error bypass the check.
            if not is_dry_run and not self._single_position_ok(symbol):
                return

            buy_price = safe_float(tickers[symbol]['ask'])
            slot_size = trading_config['slot_size']
            amount_target = float(self.exchange.exchange.amount_to_precision(symbol, slot_size / buy_price))

            if is_dry_run:
                logger.info(f"@DRY_RUN_BUY@ Virtual buy: {symbol} ${slot_size} @ ${buy_price}")
                order_id = "virtual_buy_12345"
            else:
                logger.info(f"@BUY_ORDER_SEND@ Limit buy order: {symbol} ${slot_size} @ ${buy_price}")
                order = self.order_manager.buy(symbol, amount_target, buy_price)
                order_id = order.get('id') if isinstance(order, dict) else None
                if not order_id:
                    # Order request returned without an id: we cannot track it. Refuse to
                    # continue blind; the balance check on the next entry catches any fill.
                    logger.error(f"@BUY_NO_ORDER_ID@ {symbol}: exchange response without id: {order}")
                    return

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
            # Nothing was placed (order_manager.buy raises before any state change),
            # so staying/returning to IDLE is correct here.
            logger.error(f"@ENTRY_ERROR@ {symbol}: {e}")
            self.state = BotState.IDLE

    def _handle_buying_state(self):
        try:
            if not all(k in self.state_data for k in ('symbol', 'order_id', 'buy_time')):
                logger.error(f"@BUYING_STATE_CORRUPT@ state_data={self.state_data}")
                self._transition_to_idle('buying_state_corrupt')
                return
            symbol = self.state_data['symbol']
            order_id = self.state_data['order_id']
            buy_time = self.state_data['buy_time']
            is_dry_run = self.state_data.get('is_dry_run', False)
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

            # ---- Live: order status is exchange truth or "unknown", never guessed ----
            order = self.exchange.fetch_order(order_id, symbol)
            if order is None:
                unknown = int(self.state_data.get('status_unknown_count', 0)) + 1
                self.state_data['status_unknown_count'] = unknown
                logger.warning(f"@BUY_STATUS_UNKNOWN@ {symbol} {order_id}: unknown x{unknown}, {elapsed:.0f}s in BUYING")
                if elapsed >= timeout_sec * 2:
                    # Long enough without a trustworthy status: stop polling, ask the balance.
                    self._resolve_buy_by_balance(symbol, order_id, 'status_unknown')
                return
            self.state_data['status_unknown_count'] = 0

            status = order.get('status')
            filled = safe_float(order.get('filled'))
            avg_price = safe_float(order.get('average') or order.get('price'))
            is_grid = self.state_data.get('is_grid_active', False)

            def _accept_fill(qty: float, price: float, tag: str) -> None:
                logger.info(f"@{tag}@ {symbol}: filled={qty} avg_price={price}")
                if is_grid:
                    self._on_grid_level_filled({**order, 'filled': qty, 'average': price})
                else:
                    self._on_buy_filled(symbol, qty, price, is_dry_run=False)

            if status in ('closed', 'filled'):
                if filled <= 0:
                    filled = safe_float(order.get('amount'))
                if filled <= 0 or avg_price <= 0:
                    # "closed" but the exchange gave no fill data: do not invent it.
                    logger.error(f"@BUY_CLOSED_NO_FILL_DATA@ {symbol} {order_id}: filled={filled} avg={avg_price}")
                    self._resolve_buy_by_balance(symbol, order_id, 'closed_without_fill_data')
                    return
                _accept_fill(filled, avg_price, 'BUY_FILLED')
            elif status in ('canceled', 'rejected', 'expired'):
                if filled > 0 and avg_price > 0:
                    # Canceled AFTER a partial fill: we hold coins, this is a position.
                    _accept_fill(filled, avg_price, 'BUY_PARTIAL_FILL')
                else:
                    logger.warning(f"@BUY_CANCELED@ {symbol} order {status}, no fill")
                    self._transition_to_idle(f'buy_{status}')
            elif elapsed >= timeout_sec:
                logger.warning(f"@BUY_TIMEOUT@ {symbol} order open for {elapsed:.0f}s >= {timeout_sec}s -> cancel")
                try:
                    self.exchange.cancel_order(order_id, symbol)
                except Exception as cancel_err:
                    logger.error(f"@BUY_CANCEL_ERROR@ {order_id}: {cancel_err}")
                # The order may have filled between our poll and the cancel: re-read it.
                post = self.exchange.fetch_order(order_id, symbol)
                if post is None:
                    self._resolve_buy_by_balance(symbol, order_id, 'timeout_status_unknown')
                    return
                pf = safe_float(post.get('filled'))
                pp = safe_float(post.get('average') or post.get('price'))
                if pf > 0 and pp > 0:
                    _accept_fill(pf, pp, 'BUY_FILLED_ON_CANCEL')
                else:
                    self._transition_to_idle('buy_timeout')
        except Exception as e:
            # Do NOT reset to IDLE here: an exception says nothing about whether the
            # buy filled. Stay in BUYING; the next tick re-polls the exchange.
            logger.error(f"@BUYING_STATE_ERROR@ {self.state_data.get('symbol', 'unknown')}: {e}", exc_info=True)
