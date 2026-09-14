"""EXITING state handler."""
import time
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float, exit_backstop_decision
from metrics import METRICS

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

            # Halted after too many failed exits: hold, no more orders, no API polling.
            if self.state_data.get('exit_halted'):
                last = self.state_data.get('halt_log_ts', 0.0)
                if time.time() - last >= 60:
                    self.state_data['halt_log_ts'] = time.time()
                    logger.critical(f"@EXIT_HALTED@ {symbol}: waiting for manual intervention (sell manually, then restart the bot)")
                return

            # Exit already booked but IDLE was deferred (balance unknown): only retry
            # the reconciliation, never re-log the trade.
            if self.state_data.get('exit_logged'):
                self._transition_to_idle('exit_logged_retry')
                return

            # Limit-chaser: manage an in-flight passive exit (re-peg / backstop)
            if self.state_data.get('exit_mode') == 'chase':
                self._handle_chase_exit(symbol, is_dry_run)
                return

            elapsed = time.time() - exit_time
            print(f"EXITING {symbol}: {elapsed:.1f}s / {timeout_sec}s @EXITING_MONITOR@", end='\r')

            if is_dry_run:
                # Dry run: simulate fill after 1 second
                if elapsed >= 1:
                    logger.info(f"@DRY_RUN_EXIT@ Virtual exit order filled for {symbol}")
                    self._on_exit_filled(symbol, exit_amount, self.state_data['buy_price'], exit_type, is_dry_run=True)
                return

            # ---- Live: exchange truth or "unknown"; a sale is never assumed ----
            order = self.exchange.fetch_order(order_id, symbol)
            if order is None:
                unk = int(self.state_data.get('status_unknown_count', 0)) + 1
                self.state_data['status_unknown_count'] = unk
                logger.warning(f"@EXIT_STATUS_UNKNOWN@ {symbol} {order_id}: unknown x{unk}, {elapsed:.0f}s in EXITING")
                if elapsed >= timeout_sec:
                    self._resolve_exit_by_balance(symbol, order_id, exit_type, 'status_unknown')
                return
            self.state_data['status_unknown_count'] = 0

            status = order.get('status')
            filled = safe_float(order.get('filled'))
            avg_price = safe_float(order.get('average') or order.get('price'))

            if status in ('closed', 'filled'):
                if avg_price <= 0:
                    logger.error(f"@EXIT_CLOSED_NO_PRICE@ {symbol} {order_id}: closed without price data")
                    self._resolve_exit_by_balance(symbol, order_id, exit_type, 'closed_without_price')
                    return
                logger.info(f"@EXIT_FILLED@ {symbol}: filled={filled or exit_amount} @ {avg_price}")
                self._on_exit_filled(symbol, filled or exit_amount, self.state_data['buy_price'], exit_type, avg_price, is_dry_run=False)
            elif status in ('canceled', 'rejected', 'expired'):
                # Canceled (possibly after a partial fill): we may still hold coins.
                logger.warning(f"@EXIT_CANCELED@ {symbol} exit order {status} (filled={filled})")
                self._resolve_exit_by_balance(symbol, order_id, exit_type, f'order_{status}')
            elif elapsed >= timeout_sec:
                logger.warning(f"@EXIT_TIMEOUT@ {symbol} exit order open for {elapsed:.0f}s >= {timeout_sec}s")
                self._resolve_exit_by_balance(symbol, order_id, exit_type, 'timeout')
        except Exception as e:
            # Stay in EXITING: an exception is not evidence that the coin was sold.
            logger.error(f"@EXITING_STATE_ERROR@ {self.state_data.get('symbol', 'unknown')}: {e}", exc_info=True)

    def _resolve_exit_by_balance(self, symbol: str, order_id: str, exit_type: str, reason: str) -> None:
        """The exit order's fate is unclear: cancel whatever may rest, then let the
        balance decide. Sold -> book the exit; still held -> re-issue the exit with a
        retry budget; unknown -> wait."""
        try:
            self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            logger.debug(f"@EXIT_RESOLVE_CANCEL_WARN@ {order_id}: {e}")
        coin = symbol.split('/')[0]
        held = self.exchange.get_coin_balance(coin)
        if held is None:
            logger.warning(f"@EXIT_RESOLVE_DEFERRED@ {symbol} ({reason}): balance unknown, staying in EXITING")
            return
        buy_price = self.state_data['buy_price']
        exit_amount = safe_float(self.state_data.get('exit_amount'))
        if held <= self._dust_threshold(symbol):
            close_price = self._recent_sell_price(symbol, self.state_data.get('exit_time', 0.0))
            if close_price <= 0:
                px = self._get_fresh_price(symbol) or {}
                close_price = safe_float(px.get('bid')) or safe_float(px.get('last'))
            if close_price <= 0:
                logger.error(f"@EXIT_PRICE_UNKNOWN@ {symbol} sold but no price available; booking at entry (PnL understated)")
                close_price = buy_price
            logger.info(f"@EXIT_RESOLVED_SOLD@ {symbol} ({reason}): balance {held} {coin}, close @ {close_price}")
            self._on_exit_filled(symbol, exit_amount, buy_price, exit_type, close_price, is_dry_run=False)
            return

        # Still holding: retry the exit, but never forever.
        attempts = int(self.state_data.get('exit_attempts', 0)) + 1
        self.state_data['exit_attempts'] = attempts
        max_attempts = int(self.config.get_trading_config().get('max_exit_attempts', 5))
        if attempts > max_attempts:
            if not self.state_data.get('exit_halted'):
                logger.critical(
                    f"@EXIT_HALTED@ {symbol}: {attempts - 1} exit attempts failed, still holding {held} {coin}. "
                    f"Manual intervention required; no further orders will be sent."
                )
                self.state_data['exit_halted'] = True
            return
        logger.warning(f"@EXIT_RETRY@ {symbol} ({reason}): still holding {held} {coin}, attempt {attempts}/{max_attempts}")
        self.state_data['amount'] = held
        self.state_data['order_id'] = None
        # _panic_sell only reads state_data and moves us to EXITING with a new
        # exit_order_id; if it fails we remain in EXITING with the canceled id and
        # the next tick lands back here, consuming another attempt.
        self._panic_sell(urgent=True, reason=f'exit_retry_{reason}')

    def _recent_sell_price(self, symbol: str, since_ts: float) -> float:
        """Volume-weighted price of our SELL trades on `symbol` since `since_ts`, or 0.0."""
        try:
            trades = self.exchange.exchange.fetch_my_trades(symbol, limit=50)
        except Exception as e:
            logger.debug(f"@EXIT_TRADES_WARN@ {symbol}: {e}")
            return 0.0
        qty = 0.0
        cost = 0.0
        for t in trades:
            if t.get('side') != 'sell' or safe_float(t.get('timestamp')) / 1000.0 < since_ts - 5:
                continue
            a = safe_float(t.get('amount'))
            p = safe_float(t.get('price'))
            if a > 0 and p > 0:
                qty += a
                cost += a * p
        return cost / qty if qty > 0 else 0.0

    def _on_exit_filled(self, symbol: str, amount: float, buy_price: float, exit_type: str, close_price: float = None, is_dry_run: bool = False):
        try:
            # Update cooldown for this symbol
            self._update_symbol_cooldown(symbol)

            if close_price is None:
                if is_dry_run:
                    close_price = buy_price * 0.99  # Simulate 1% loss in dry run
                else:
                    px = self._get_fresh_price(symbol) or {}
                    close_price = safe_float(px.get('bid')) or safe_float(px.get('last'))
                    close_price = close_price if close_price > 0 else (buy_price * 0.98)

            trade_profit = self._calc_pnl(buy_price, close_price, amount, is_market_exit=(exit_type == 'panic'))
            self.session_profit += trade_profit

            # Update metrics
            METRICS.order_total.labels(side='sell', strategy='hydra_net').inc()
            METRICS.active_positions.set(0)
            METRICS.session_profit.set(self.session_profit)

            if exit_type == 'panic':
                self._record_panic_exit(trade_profit)
                self.trade_db.log_trade(symbol, "sell_panic", amount, close_price, confidence=0.0, profit=trade_profit)
                logger.warning(f"@PANIC_SELL_DONE@ Panic sell complete. Price: {close_price}, PnL: ${trade_profit:.2f}")
            else:
                self.trade_db.log_trade(symbol, "sell", amount, close_price, confidence=0.0, profit=trade_profit)
                logger.info(f"@EXIT_DONE@ Exit complete. Price: {close_price}, PnL: ${trade_profit:.2f}")
            self._apply_dispatcher_feedback(trade_profit)
            self.state_data['exit_logged'] = True
            if is_dry_run:
                self.state_data = {}
                self.state = BotState.IDLE
            else:
                # Exchange must confirm the coin is gone; otherwise the position is adopted.
                self._transition_to_idle(f'exit_{exit_type}')
        except Exception as e:
            logger.error(f"@ON_EXIT_FILLED_ERROR@ {symbol}: {e}", exc_info=True)
            self._transition_to_idle('exit_filled_error')

    def _handle_chase_exit(self, symbol: str, is_dry_run: bool):
        """Manage an in-flight limit-chase exit: fill, re-peg, or market backstop."""
        sd = self.state_data
        buy_price = sd['buy_price']
        amount = sd['exit_amount']
        urgent = sd.get('exit_urgent', True)
        chaser = self.config.get_trading_config().get('limit_chaser', {})
        ws = self._get_fresh_price(symbol) or {}
        best_bid = safe_float(ws.get('bid'))
        best_ask = safe_float(ws.get('ask'))
        now = time.time()
        deadline_passed = now >= sd.get('chase_deadline', now)
        cur_price = best_bid or best_ask or buy_price
        limit_price = sd.get('chase_limit_price') or best_ask or buy_price

        backstop, reason = exit_backstop_decision(
            buy_price, cur_price, urgent,
            chaser.get('urgent_skip_below_pct', 1.5), deadline_passed
        )

        if is_dry_run:
            # Maker fill modeled when the market trades up to our resting ask:
            # either the bid reaches our price, or the ask ticks above it (lifted).
            if (best_bid > 0 and best_bid >= limit_price) or (best_ask > 0 and best_ask > limit_price):
                logger.info(f"@CHASE_FILL@ {symbol} maker fill @ {limit_price}")
                self._on_exit_filled(symbol, amount, buy_price, 'chase', close_price=limit_price, is_dry_run=True)
                return
            if backstop:
                logger.warning(f"@CHASE_BACKSTOP@ {symbol} -> market ({reason}) @ {cur_price}")
                self._on_exit_filled(symbol, amount, buy_price, 'panic', close_price=cur_price, is_dry_run=True)
                return
            # Re-peg downward to follow a falling ask (still maker)
            if chaser.get('repeg', True) and best_ask > 0 and best_ask < limit_price:
                sd['chase_limit_price'] = best_ask
                logger.debug(f"@CHASE_REPEG@ {symbol} -> {best_ask}")
            return

        # Real mode: poll the resting limit order (None = unknown)
        order_id = sd.get('exit_order_id')
        order = self.exchange.fetch_order(order_id, symbol)
        if order is not None:
            status = order.get('status')
            if status in ('closed', 'filled'):
                close_price = safe_float(order.get('average') or order.get('price') or limit_price)
                logger.info(f"@CHASE_FILL@ {symbol} limit filled @ {close_price}")
                self._on_exit_filled(symbol, amount, buy_price, 'chase', close_price=close_price, is_dry_run=False)
                return
            if status in ('canceled', 'rejected', 'expired'):
                logger.warning(f"@CHASE_ORDER_GONE@ {symbol} limit {order_id} is {status}")
                self._resolve_exit_by_balance(symbol, order_id, 'chase', f'chase_{status}')
                return
        else:
            unk = int(sd.get('status_unknown_count', 0)) + 1
            sd['status_unknown_count'] = unk
            logger.warning(f"@CHASE_STATUS_UNKNOWN@ {symbol} {order_id}: unknown x{unk}")

        if backstop:
            if order is None:
                # Status unknown: a blind market order could double-sell a limit that
                # just filled. Let the balance decide what (if anything) is left.
                self._resolve_exit_by_balance(symbol, order_id, 'panic', 'chase_backstop_unknown')
                return
            try:
                self.exchange.cancel_order(order_id, symbol)
            except Exception:
                pass
            try:
                market_order = self.order_manager.market_sell(symbol, amount)
                sd['exit_order_id'] = market_order.get('id')
            except Exception as e:
                logger.error(f"@CHASE_BACKSTOP_ERR@ {e}")
            sd['exit_type'] = 'panic'
            sd['exit_mode'] = 'market'
            sd['exit_time'] = time.time()
            logger.warning(f"@CHASE_BACKSTOP@ {symbol} -> market ({reason}); next tick resolves fill")
            return

        # Re-peg: if the ask dropped below our resting price, cancel & re-post lower
        if chaser.get('repeg', True) and best_ask > 0 and best_ask < limit_price:
            try:
                amended = self.order_manager.amend(order_id, symbol, amount, best_ask)
                if amended and amended.get('id'):
                    sd['exit_order_id'] = amended.get('id')
                sd['chase_limit_price'] = best_ask
                logger.debug(f"@CHASE_REPEG@ {symbol} -> {best_ask}")
            except Exception as e:
                logger.debug(f"@CHASE_REPEG_WARN@ {e}")
