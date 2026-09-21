"""IN_POSITION state handler + panic sell + partial TP."""
import time
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float, chase_deadline, exit_backstop_decision
from metrics import METRICS

from ..state_enum import BotState


class InPositionStateMixin:
    def _handle_in_position_state(self):
        try:
            symbol = self.state_data.get('symbol', 'unknown')
            logger.info(f"@IN_POSITION_LOOP@ Monitoring {symbol}, grid_active={self.state_data.get('is_grid_active', False)}")
            # HYDRA-NET: Skip normal monitoring if grid is active
            if self.state_data.get('is_grid_active', False):
                return  # Grid synchronization happens in main loop

            trading_config = self.config.get_trading_config()
            is_dry_run = trading_config.get('dry_run', False)

            # Price must be fresh and real. A missing/stale price used to fall back to
            # fetch_ticker()['last'] == 0.0 on error -> "-100%" -> market stop-loss.
            price_data = self._get_fresh_price(symbol)
            if price_data is None:
                stale = int(self.state_data.get('price_stale_count', 0)) + 1
                self.state_data['price_stale_count'] = stale
                if stale == 1 or stale % 30 == 0:
                    logger.warning(f"@PRICE_STALE@ {symbol}: no fresh price (x{stale}); stops/targets not evaluated this tick")
                return
            self.state_data['price_stale_count'] = 0
            current_price = safe_float(price_data.get('last'))

            # Maintenance mode: emergency close position
            if getattr(self, 'maintenance_mode', False):
                self._handle_maintenance_exit(symbol, current_price, is_dry_run)
                return

            if safe_float(self.state_data.get('buy_price')) <= 0:
                # Entry price unknown (adopted position). PnL from here is relative to
                # the adoption price; log loudly so the operator can correct records.
                logger.critical(f"@ENTRY_PRICE_UNKNOWN@ {symbol}: buy_price missing, using current {current_price}")
                self.state_data['buy_price'] = current_price
                self.state_data.setdefault('trailing_high', current_price)

            change_percent = ((current_price - self.state_data['buy_price']) / self.state_data['buy_price']) * 100
            elapsed = time.time() - self.state_data['buy_time']
            target_sell_price = self.state_data.get('target_sell_price')
            if target_sell_price:
                # Grid mode: use exact TP price set by hydra_net
                is_tp_hit = current_price >= target_sell_price
            else:
                take_profit_pct = trading_config.get('take_profit', 1.5)
                is_tp_hit = change_percent >= take_profit_pct

            # Partial TP settings
            position_value_usdt = self.state_data['amount'] * self.state_data['buy_price']
            partial_tp_enabled = trading_config.get('partial_tp_activation_pct', 1.0) > 0 and position_value_usdt >= 10.0
            partial_tp_activation = trading_config.get('partial_tp_activation_pct', 1.0)
            partial_tp_size = trading_config.get('partial_tp_size_pct', 50.0)
            move_to_breakeven = trading_config.get('move_to_breakeven', True)
            trailing_callback = trading_config.get('trailing_callback_pct', 0.5)

            print(f"Position {symbol}: {change_percent:.2f}% | Time: {int(elapsed)}s @MONITOR_WS@", end='\r')
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
                if hasattr(self, '_save_state'):
                    self._save_state()

            # Check partial TP
            if is_partial_tp_hit and not self.state_data['partial_tp_hit']:
                logger.info(f"@PARTIAL_TP@ Partial TP hit for {symbol} (+{change_percent:.2f}%)")
                self._execute_partial_tp(symbol, current_price, partial_tp_size, is_dry_run)
                self.state_data['partial_tp_hit'] = True
                if hasattr(self, '_save_state'):
                    self._save_state()

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
                    trade_profit = self._calc_pnl(self.state_data['buy_price'], self.state_data['target_sell_price'], self.state_data['amount'])
                    self.session_profit += trade_profit
                    METRICS.session_profit.set(self.session_profit)
                    self.trade_db.log_trade(symbol, "sell", self.state_data['amount'], self.state_data['target_sell_price'], confidence=0.0, profit=trade_profit)
                    self._apply_dispatcher_feedback(trade_profit)
                    self.state_data = {}
                    self.state = BotState.IDLE
                    return
                elif is_sl_hit:
                    logger.warning(f"@DRY_RUN_SL@ Virtual SL hit for {symbol} ({change_percent:.2f}%)")
                    self.last_loss_time = time.time()
                    self._panic_sell(urgent=True, reason='sl')
                    return
            else:
                # Settle a resting partial-TP order before anything else can end
                # the position, so session_profit reflects fills, not intents.
                self._reconcile_partial_tp(symbol)

                # Balance check: only a CONFIRMED empty balance may end the position.
                # None (unknown) skips the check — the old code parsed the error stub
                # as "0 coins" and reset a live position to IDLE.
                coin_name = symbol.split('/')[0]
                coin_balance = self.exchange.get_coin_balance(coin_name)
                if coin_balance is not None and coin_balance <= self._dust_threshold(symbol):
                    logger.warning(f"@COIN_BALANCE_ZERO@ {coin_name}={coin_balance}, re-checking")
                    time.sleep(1.0)
                    retry_bal = self.exchange.get_coin_balance(coin_name)
                    if retry_bal is not None and retry_bal <= self._dust_threshold(symbol):
                        # Sold outside the bot (manual sale, TP filled while we were down...).
                        self._transition_to_idle('coin_balance_zero')
                        return

                order_id = self.state_data.get('order_id')
                order = None
                order_unknown = False
                if order_id:
                    order = self.exchange.fetch_order(order_id, symbol)
                    if order is None:
                        order_unknown = True
                        unk = int(self.state_data.get('status_unknown_count', 0)) + 1
                        self.state_data['status_unknown_count'] = unk
                        if unk == 1 or unk % 30 == 0:
                            logger.warning(f"@TP_STATUS_UNKNOWN@ {symbol} order {order_id} unknown x{unk}; holding")
                    else:
                        self.state_data['status_unknown_count'] = 0

                # A completed take-profit requires a *filled SELL* order. Guard against
                # misreading the (already closed) BUY order as a sell — that bug caused
                # instant exits at entry price (fee bleed).
                if order is not None and order.get('side') == 'sell' and order.get('status') in ('closed', 'filled'):
                    if not self.state_data.get('exit_logged'):
                        close_price = safe_float(order.get('average') or order.get('price') or self.state_data['buy_price'])
                        sold_qty = safe_float(order.get('filled')) or self.state_data['amount']
                        trade_profit = self._calc_pnl(self.state_data['buy_price'], close_price, sold_qty)
                        self.session_profit += trade_profit
                        METRICS.session_profit.set(self.session_profit)
                        self.trade_db.log_trade(symbol, "sell", sold_qty, close_price, confidence=0.0, profit=trade_profit)
                        self._apply_dispatcher_feedback(trade_profit)
                        tag = "PROFIT_TAKEN" if trade_profit >= 0 else "LOSS_TAKEN"
                        logger.info(f"@{tag}@ {symbol} ${trade_profit:+.2f} @ {close_price}")
                        self.state_data['exit_logged'] = True
                    # IDLE only if the exchange confirms we no longer hold the coin.
                    self._transition_to_idle('tp_filled')
                    return

                # No resting SELL order at all (TP placement failed, or order_id points at
                # a non-sell order): exit via limit-chase/market rather than hold unmanaged.
                # An UNKNOWN status is not "no order" — we keep waiting, but still run the
                # stop-loss check below against the fresh price.
                if order_id is None or (order is not None and order.get('side') != 'sell'):
                    logger.warning(
                        f"@NO_TP_ORDER@ {symbol} has no resting sell order "
                        f"(order_id={order_id}) -> exit"
                    )
                    self._panic_sell(urgent=False, reason='no_tp_order')
                    return
                if order is not None and order.get('status') in ('canceled', 'rejected', 'expired') \
                        and not self.state_data.get('tp_canceled_logged'):
                    logger.warning(f"@TP_ORDER_CANCELED@ {symbol} sell {order_id} is {order.get('status')}; position has no resting TP")
                    self.state_data['tp_canceled_logged'] = True

                if is_sl_hit:
                    logger.warning(f"@STOP_LOSS_HIT@ SL hit for {symbol} ({change_percent:.2f}%)")
                    self.last_loss_time = time.time()
                    self._panic_sell(urgent=True, reason='sl')
                    return
            adaptive_timeout = self._calculate_adaptive_breakeven_timeout(symbol)
            if elapsed > adaptive_timeout and not self.state_data.get('is_breakeven', False):
                logger.info(f"@BREAKEVEN_TIMEOUT@ Adaptive timeout reached: {elapsed}s (ATR-based: {adaptive_timeout}s)")
                self._set_breakeven()

            # Hard exit: max position hold time (30 min default)
            hard_exit_sec = trading_config.get('hard_exit_timeout_sec', 1800)
            if elapsed > hard_exit_sec:
                logger.warning(f"@HARD_EXIT_TIMEOUT@ Position held for {elapsed:.0f}s >= {hard_exit_sec}s, forcing exit")
                self._panic_sell(urgent=False, reason='hard_exit')
                return
        except Exception as e:
            logger.error(f"@IN_POSITION_ERROR@ IN_POSITION state error for {self.state_data.get('symbol', 'unknown')}: {e}", exc_info=True)

    def _panic_sell(self, urgent: bool = True, reason: str = 'panic') -> None:
        """Exit the position. Tries an aggressive limit-chase (maker, no slippage)
        first when enabled, falling back to a market backstop on timeout or a
        hard adverse move. `urgent=True` shrinks the chase window for stop-loss /
        trailing exits; `urgent=False` (e.g. stale-position hard-exit) allows a
        longer passive chase.
        """
        try:
            symbol = self.state_data['symbol']
            trading_config = self.config.get_trading_config()
            is_dry_run = trading_config.get('dry_run', False)
            chaser = trading_config.get('limit_chaser', {})
            buy_price = self.state_data['buy_price']

            # Cancel existing sell order if any
            try:
                self.exchange.cancel_order(self.state_data['order_id'], symbol)
                logger.info(f"@PANIC_CANCEL@ Canceled existing sell order: {self.state_data['order_id']}")
            except Exception as e:
                logger.debug(f"@PANIC_CANCEL_WARN@ Failed to cancel sell order: {e}")

            # Use real available balance instead of saved amount to account for fees
            coin_name = symbol.split('/')[0]
            real_balance = None if is_dry_run else self.exchange.get_coin_balance(coin_name)
            if real_balance is not None and real_balance > self._dust_threshold(symbol):
                amount = float(self.exchange.exchange.amount_to_precision(symbol, real_balance))
                logger.info(f"@PANIC_REAL_BALANCE@ {coin_name} real balance: {real_balance}, sell_amount: {amount}")
            else:
                # Balance unknown (None) or dry-run: fall back to the saved amount
                amount = float(self.exchange.exchange.amount_to_precision(symbol, self.state_data['amount']))
                if not is_dry_run:
                    logger.warning(f"@PANIC_BALANCE_FALLBACK@ {coin_name} balance={real_balance}; using saved amount: {amount}")

            # Live market snapshot (fresh only; stale bid/ask must not price the exit)
            ws = self._get_fresh_price(symbol) or {}
            best_bid = safe_float(ws.get('bid'))
            best_ask = safe_float(ws.get('ask'))
            cur_price = best_bid or best_ask or buy_price

            # Hard-adverse safeguard: urgent + already far below entry -> straight to market
            backstop_now, bs_reason = exit_backstop_decision(
                buy_price, cur_price, urgent,
                chaser.get('urgent_skip_below_pct', 1.5), deadline_passed=False
            )
            use_chase = chaser.get('enabled', True) and best_ask > 0 and not backstop_now

            if use_chase:
                if is_dry_run:
                    exit_order_id = 'virtual_chase'
                else:
                    time.sleep(0.3)
                    limit_order = self.order_manager.sell(symbol, amount, best_ask)
                    exit_order_id = limit_order.get('id')
                deadline = chase_deadline(
                    time.time(), urgent,
                    chaser.get('chase_sec_urgent', 3.0),
                    chaser.get('chase_sec_normal', 12.0)
                )
                self.state_data.update({
                    'exit_order_id': exit_order_id,
                    'exit_time': time.time(),
                    'exit_amount': amount,
                    'exit_type': 'chase',
                    'exit_mode': 'chase',
                    'exit_urgent': urgent,
                    'exit_reason': reason,
                    'chase_deadline': deadline,
                    'chase_limit_price': best_ask,
                })
                self.state = BotState.EXITING
                logger.info(
                    f"@CHASE_START@ {symbol} limit-chase @ {best_ask} urgent={urgent} "
                    f"window={deadline - time.time():.0f}s reason={reason}"
                )
                return

            # Immediate market exit (chaser disabled, no book, or hard-adverse)
            time.sleep(0.5)
            if is_dry_run:
                logger.info(f"@DRY_RUN_PANIC@ Virtual market sell: {amount} {symbol}")
                exit_order_id = "virtual_panic_sell_12345"
            else:
                logger.info(f"@PANIC_SELL_SEND@ Market sell order: {amount} {symbol}")
                market_order = self.order_manager.market_sell(symbol, amount)
                exit_order_id = market_order.get('id')

            self.state_data.update({
                'exit_order_id': exit_order_id,
                'exit_time': time.time(),
                'exit_amount': amount,
                'exit_type': 'panic',
                'exit_mode': 'market',
                'exit_reason': reason,
            })
            self.state = BotState.EXITING
            logger.info(f"@PANIC_MARKET@ {symbol} market exit reason={reason} bs={bs_reason} -> EXITING")
        except Exception as e:
            logger.error(f"Panic sell error: {e}", exc_info=True)
            # Do NOT reset to IDLE on panic sell failure. We still hold the asset
            # and must retry. Only reset to IDLE if balance is below dust (handled
            # in _handle_in_position_state balance check).

    def _reconcile_partial_tp(self, symbol: str):
        """Book a partial take-profit only once the exchange confirms the fill.

        Live-only. `_execute_partial_tp` parks the order id in state_data; here
        we poll it and either log the `sell_partial` trade with realised PnL, or
        drop the claim if the order was canceled. An UNKNOWN status (fetch_order
        returns None) is NOT treated as "not filled" — we keep waiting.
        """
        order_id = self.state_data.get('partial_tp_order_id')
        if not order_id:
            return

        order = self.exchange.fetch_order(order_id, symbol)
        if order is None:
            unk = int(self.state_data.get('partial_tp_unknown_count', 0)) + 1
            self.state_data['partial_tp_unknown_count'] = unk
            if unk == 1 or unk % 30 == 0:
                logger.warning(
                    f"@PARTIAL_TP_STATUS_UNKNOWN@ {symbol} order {order_id} unknown x{unk}; holding"
                )
            return
        self.state_data['partial_tp_unknown_count'] = 0

        status = order.get('status')
        if status in ('closed', 'filled'):
            filled = safe_float(order.get('filled')) or safe_float(self.state_data.get('partial_tp_amount'))
            close_price = safe_float(order.get('average') or order.get('price') or self.state_data['buy_price'])
            trade_profit = self._calc_pnl(self.state_data['buy_price'], close_price, filled)
            self.session_profit += trade_profit
            METRICS.session_profit.set(self.session_profit)
            self.trade_db.log_trade(symbol, "sell_partial", filled, close_price, confidence=0.0, profit=trade_profit)
            logger.info(
                f"@PARTIAL_TP_DONE@ {symbol} partial TP filled {filled} @ {close_price}, "
                f"PnL: ${trade_profit:+.2f}"
            )
        elif status in ('canceled', 'rejected', 'expired'):
            logger.warning(
                f"@PARTIAL_TP_CANCELED@ {symbol} partial TP order {order_id} is {status}; "
                f"no PnL booked"
            )
        else:
            return  # still open — wait

        self.state_data.pop('partial_tp_order_id', None)
        self.state_data.pop('partial_tp_amount', None)
        self.state_data.pop('partial_tp_unknown_count', None)
        if hasattr(self, '_save_state'):
            self._save_state()

    def _execute_partial_tp(self, symbol: str, current_price: float, partial_tp_size_pct: float, is_dry_run: bool):
        """Execute partial take profit - sell portion of position"""
        try:
            original_amount = self.state_data['amount']
            partial_amount = original_amount * (partial_tp_size_pct / 100)
            remaining_amount = original_amount - partial_amount

            if is_dry_run:
                logger.info(f"@DRY_RUN_PARTIAL_TP@ Virtual partial TP: {symbol} sell {partial_amount} @ ${current_price}")
                self.state_data['amount'] = remaining_amount
                trade_profit = self._calc_pnl(self.state_data['buy_price'], current_price, partial_amount)
                self.session_profit += trade_profit
                METRICS.session_profit.set(self.session_profit)
                self.trade_db.log_trade(symbol, "sell_partial", partial_amount, current_price, confidence=0.0, profit=trade_profit)
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

            # A limit order is only an *intent*. Booking PnL here credited the
            # session with profit from an order that may never fill (or fill at
            # another price). Record it as pending; _reconcile_partial_tp() logs
            # the trade once the exchange confirms the fill.
            self.state_data['partial_tp_order_id'] = partial_order['id']
            self.state_data['partial_tp_amount'] = partial_amount
            logger.info(
                f"@PARTIAL_TP_PENDING@ Partial TP order {partial_order['id']} resting for "
                f"{partial_amount} @ ${current_price}; Remaining: {remaining_amount}"
            )

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

            if hasattr(self, '_save_state'):
                self._save_state()

        except Exception as e:
            logger.error(f"Partial TP error: {e}")

    def _handle_maintenance_exit(self, symbol: str, current_price: float, is_dry_run: bool):
        """Emergency close position during maintenance mode."""
        try:
            buy_price = self.state_data['buy_price']
            amount = self.state_data['amount']
            elapsed_since_maintenance = time.time() - self._maintenance_start_time

            # Phase 1: Try limit sell at breakeven (buy_price)
            if elapsed_since_maintenance < self._maintenance_limit_timeout:
                if not self.state_data.get('_maintenance_limit_placed', False):
                    self.state_data['_maintenance_limit_placed'] = True
                    if is_dry_run:
                        # In dry-run, if price >= buy_price, simulate fill
                        if current_price >= buy_price:
                            logger.info(f"@MAINTENANCE_CLOSE@ Dry-run limit sell filled @ {current_price} (breakeven)")
                            trade_profit = self._calc_pnl(buy_price, current_price, amount)
                            self.session_profit += trade_profit
                            METRICS.session_profit.set(self.session_profit)
                            self.trade_db.log_trade(symbol, "sell", amount, current_price, confidence=0.0, profit=trade_profit)
                            self.state_data = {}
                            self.state = BotState.IDLE
                            return
                        else:
                            logger.info(f"@MAINTENANCE_WAIT@ Dry-run waiting for price >= {buy_price} (current: {current_price})")
                            return
                    else:
                        # Real mode: place limit sell at breakeven
                        try:
                            sell_order = self.order_manager.sell(symbol, amount, buy_price)
                            self.state_data['maintenance_order_id'] = sell_order['id']
                            logger.info(f"@MAINTENANCE_ORDER@ Limit sell placed @ {buy_price}: {sell_order['id']}")
                            return
                        except Exception as e:
                            logger.error(f"@MAINTENANCE_ORDER_ERROR@ Failed to place limit sell: {e}")
                else:
                    # Limit already placed, check if filled (real mode)
                    if not is_dry_run and self.state_data.get('maintenance_order_id'):
                        try:
                            order = self.exchange.fetch_order(self.state_data['maintenance_order_id'], symbol)
                            if order is not None and order.get('status') in ['closed', 'filled']:
                                if not self.state_data.get('exit_logged'):
                                    close_price = safe_float(order.get('average') or order.get('price') or buy_price)
                                    trade_profit = self._calc_pnl(buy_price, close_price, amount)
                                    self.session_profit += trade_profit
                                    METRICS.session_profit.set(self.session_profit)
                                    self.trade_db.log_trade(symbol, "sell", amount, close_price, confidence=0.0, profit=trade_profit)
                                    logger.info(f"@MAINTENANCE_CLOSE@ Limit sell filled @ {close_price}")
                                    self.state_data['exit_logged'] = True
                                self._transition_to_idle('maintenance_limit_filled')
                                return
                        except Exception as e:
                            logger.debug(f"@MAINTENANCE_CHECK_WARN@ {e}")
                    return

            # Phase 2: Timeout exceeded — market sell
            logger.warning(f"@MAINTENANCE_TIMEOUT@ Limit sell timeout ({self._maintenance_limit_timeout}s). Market selling {symbol}")
            if is_dry_run:
                trade_profit = self._calc_pnl(buy_price, current_price, amount, is_market_exit=True)
                self.session_profit += trade_profit
                METRICS.session_profit.set(self.session_profit)
                self._record_panic_exit(trade_profit)
                self.trade_db.log_trade(symbol, "sell_panic", amount, current_price, confidence=0.0, profit=trade_profit)
                logger.info(f"@MAINTENANCE_MARKET@ Dry-run market sell @ {current_price}, PnL: ${trade_profit:.2f}")
                self.state_data = {}
                self.state = BotState.IDLE
            else:
                # Cancel limit order if exists
                if self.state_data.get('maintenance_order_id'):
                    try:
                        self.exchange.cancel_order(self.state_data['maintenance_order_id'], symbol)
                    except Exception:
                        pass
                self._panic_sell()
        except Exception as e:
            logger.error(f"@MAINTENANCE_ERROR@ Maintenance exit failed: {e}")
            # Last resort: IDLE only if the exchange confirms the coin is gone.
            self._transition_to_idle('maintenance_error')
