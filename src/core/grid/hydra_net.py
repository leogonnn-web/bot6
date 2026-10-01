"""HYDRA-NET: Martingale grid logic.

Lifecycle (Phase 3 redesign, audit C2):

    _launch_grid_network        level 0 = entry limit BUY at ask         -> BUYING
    _handle_grid_buying (tick)  polls TWO orders: grid_buy_order_id, tp_order_id
        buy filled  -> _on_grid_level_filled: accumulate, refresh TP, place next level
        tp  filled  -> _on_grid_tp_filled:   cancel resting buy, book exit, IDLE
        guards      -> _end_grid_to_position: knife / max hold / level rejected
    all levels done -> _end_grid_to_position('grid_complete')            -> IN_POSITION

Two separate order ids. The old code kept ONE `order_id`, so placing the next
buy and then "refreshing the TP" cancelled the buy it had just created, and the
TP sell's fill was later read as a buy fill. Dry-run never saw it because
virtual_* ids skipped the cancel path; the dry-run now goes through the same
code via a virtual order book (`_grid_*` wrappers).
"""
import time
import sys
import os
from typing import Dict, Optional, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float
from metrics import METRICS

from ..state_enum import BotState
from indicators.matrix import ATRAnalyzer


def get_next_grid_level(entry_price, total_amount, current_level, base_order_size_usdt, atr=None,
                        min_distance_pct: float = 0.4):
    """
    Рассчитывает параметры следующего лимитного ордера в сетке Мартингейла.

    Args:
        entry_price: Цена входа в позицию (фиксированная база для расчета сетки)
        total_amount: Общая сумма в USDT (не используется в расчете)
        current_level: Уровень усреднения (1, 2 или 3); уровень 0 = сам вход
        base_order_size_usdt: Базовый размер ордера в USDT
        atr: ATR value for adaptive spacing (optional)
        min_distance_pct: минимальный шаг сетки в % (из конфига/диспетчера)

    Returns:
        dict: {'next_price': float, 'next_amount_usdt': float}
    """
    if atr and atr > 0:
        atr_pct = (atr / entry_price) * 100
        grid_distance_pct = max(min_distance_pct, atr_pct * 0.5) / 100
    else:
        grid_distance_pct = min_distance_pct / 100

    martingale_multiplier = 1.5

    next_price = entry_price * ((1 - grid_distance_pct) ** current_level)

    if current_level == 1:
        next_amount_usdt = base_order_size_usdt * martingale_multiplier
    elif current_level == 2:
        next_amount_usdt = base_order_size_usdt * martingale_multiplier * martingale_multiplier
    elif current_level == 3:
        next_amount_usdt = base_order_size_usdt * martingale_multiplier * martingale_multiplier * martingale_multiplier
    else:
        raise ValueError("current_level должен быть 1, 2 или 3")

    return {
        'next_price': next_price,
        'next_amount_usdt': next_amount_usdt
    }


class HydraNetMixin:
    # ------------------------------------------------------------------
    # Order IO: real exchange, or a virtual book in dry-run.
    # Both paths return the same shapes so the lifecycle code is identical.
    # ------------------------------------------------------------------
    def _grid_is_dry(self) -> bool:
        return bool(self.state_data.get('is_dry_run', False))

    def _grid_place(self, side: str, symbol: str, amount: float, price: float, tag: str) -> Optional[str]:
        """Place a limit order. Returns order id or None (rejected / no id)."""
        if self._grid_is_dry():
            book = self.state_data.setdefault('virtual_orders', {})
            oid = f"virtual_{side}_{tag}_{len(book) + 1}"
            book[oid] = {'id': oid, 'side': side, 'price': price, 'amount': amount,
                         'status': 'open', 'filled': 0.0, 'average': None}
            logger.info(f"@DRY_RUN_GRID@ {symbol} virtual {side} {amount} @ {price} ({tag}) id={oid}")
            return oid
        fn = self.order_manager.buy if side == 'buy' else self.order_manager.sell
        order = fn(symbol, amount, price)
        oid = order.get('id') if isinstance(order, dict) else None
        if not oid:
            logger.error(f"@GRID_NO_ORDER_ID@ {symbol} {side} ({tag}): response without id: {order}")
        return oid

    def _grid_cancel(self, order_id: Optional[str], symbol: str) -> None:
        if not order_id:
            return
        if self._grid_is_dry():
            o = self.state_data.get('virtual_orders', {}).get(order_id)
            if o and o['status'] == 'open':
                o['status'] = 'canceled'
            return
        try:
            self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            logger.debug(f"@GRID_CANCEL_WARN@ {order_id}: {e}")

    def _grid_amend(self, order_id: str, symbol: str, amount: float, price: float, side: str) -> Optional[str]:
        """Re-price a resting order. Returns the (possibly new) id, or None on failure."""
        if self._grid_is_dry():
            o = self.state_data.get('virtual_orders', {}).get(order_id)
            if not o or o['status'] != 'open':
                return None
            o['price'], o['amount'] = price, amount
            return order_id
        res = self.exchange.amend_order(order_id, symbol, amount, price, side=side)
        if not res:
            return None
        return res.get('id') or order_id

    def _grid_fetch(self, order_id: Optional[str], symbol: str) -> Optional[Dict]:
        """Order status: exchange truth, None = unknown. Dry-run: simulate fills
        against the fresh price (buy fills when last <= price, sell when last >= price)."""
        if not order_id:
            return None
        if not self._grid_is_dry():
            return self.exchange.fetch_order(order_id, symbol)
        o = self.state_data.get('virtual_orders', {}).get(order_id)
        if not o:
            return None
        if o['status'] == 'open':
            px = self._get_fresh_price(symbol)
            last = safe_float(px.get('last')) if px else 0.0
            if last > 0 and ((o['side'] == 'buy' and last <= o['price']) or
                             (o['side'] == 'sell' and last >= o['price'])):
                o['status'] = 'closed'
                o['filled'] = o['amount']
                o['average'] = o['price']
                logger.info(f"@DRY_RUN_FILL@ {symbol} virtual {o['side']} {o['amount']} @ {o['price']} id={order_id}")
        return dict(o)

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------
    def _grid_max_levels(self) -> int:
        """Averaging levels beyond the entry (entry itself is level 0)."""
        cfg = int(self.hydra_net_config.get('max_grid_levels', 3))
        cap = cfg
        if hasattr(self, 'capital_router'):
            cap = int(self.capital_router.state.max_grid_levels)
        disp = self.state_data.get('max_grids')
        levels = min(cfg, cap)
        if disp:
            levels = min(levels, int(disp))
        return max(0, min(levels, 3))

    def _grid_tp_pct(self) -> float:
        return float(self.state_data.get('take_profit_pct') or self.hydra_net_config.get('take_profit_pct', 0.8))

    def _grid_distance_pct(self) -> float:
        return float(self.state_data.get('grid_distance') or self.hydra_net_config.get('grid_distance_pct', 0.4))

    def _grid_atr(self, symbol: str) -> Optional[float]:
        """ATR(14) on 1m, cached for 60 s in state_data to avoid a REST call per tick."""
        now = time.time()
        if now - safe_float(self.state_data.get('atr_cached_at')) < 60 and 'atr_cached' in self.state_data:
            return self.state_data.get('atr_cached')
        atr = None
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=20)
            if len(ohlcv) >= 14:
                atr = ATRAnalyzer.calculate(ohlcv, period=14)
        except Exception as e:
            logger.debug(f"@ATR_WARN@ {symbol}: {e}")
        self.state_data['atr_cached'] = atr
        self.state_data['atr_cached_at'] = now
        return atr

    def _grid_level_params(self, symbol: str, level: int) -> Optional[Tuple[float, float]]:
        """(price, amount) for averaging level >= 1, quantized to exchange limits."""
        entry_price = safe_float(self.state_data.get('entry_price'))
        if entry_price <= 0:
            return None
        base = float(self.config.get_trading_config().get('base_order_size_usdt', 5.0))
        base *= float(self.state_data.get('slot_multiplier') or 1.0)
        params = get_next_grid_level(entry_price, 0, level, base, self._grid_atr(symbol),
                                     min_distance_pct=self._grid_distance_pct())
        price = params['next_price']
        amount = params['next_amount_usdt'] / price
        return self.exchange.quantize_order(symbol, amount, price)

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------
    def _launch_grid_network(self, symbol: str, price: float, tickers: Dict, mode_override: Optional[str] = None,
                             dispatcher_features: Optional[Dict] = None) -> None:
        """Level 0: limit BUY at ask for slot_size. The grid's averaging levels are
        placed one at a time as each previous level fills."""
        try:
            if not self.hydra_net_config.get('enabled', False):
                logger.info("@GRID_DISABLED@ HYDRA-NET disabled, using normal entry")
                self._enter_trade(symbol, price, tickers, dispatcher_features=dispatcher_features)
                return
            if hasattr(self, 'capital_router') and not self.capital_router.state.grid_allowed:
                logger.info(f"@GRID_BLOCKED@ Capital Router: grid disabled (mode={self.capital_router.state.mode}), single-shot entry")
                self._enter_trade(symbol, price, tickers, dispatcher_features=dispatcher_features)
                return

            trading_config = self.config.get_trading_config()
            is_dry_run = bool(trading_config.get('dry_run', False))
            if not is_dry_run and not self._single_position_ok(symbol):
                return

            buy_price = safe_float(tickers[symbol]['ask'])
            slot_size = float(trading_config.get('slot_size', 18.0))
            min_order_size = self.hydra_net_config.get('min_order_size_usdt', 5.0)
            if slot_size < min_order_size:
                logger.warning(f"@GRID_WARN@ Slot size ${slot_size} below minimum ${min_order_size}")
                return
            q = self.exchange.quantize_order(symbol, slot_size / buy_price, buy_price)
            if q is None:
                logger.warning(f"@GRID_SKIP@ {symbol}: entry below exchange limits, not entering")
                return
            amount, buy_price = q

            # State first (so _grid_place can see is_dry_run / virtual book), then the order.
            self.state_data = {
                'symbol': symbol,
                'is_dry_run': is_dry_run,
                'is_grid_active': True,
                'entry_price': buy_price,
                'current_level': 0,
                'filled_levels': 0,
                'total_cost': 0.0,
                'total_qty': 0.0,
                'avg_price': 0.0,
                'grid_buy_order_id': None,
                'tp_order_id': None,
                'order_id': None,
                'buy_price': buy_price,
                'amount': amount,
                'amount_target': amount,
                'buy_time': time.time(),
                'grid_started_at': time.time(),
                'level_placed_at': time.time(),
                'is_breakeven': False,
                'partial_tp_hit': False,
                'trailing_high': buy_price,
                'dispatcher_features': dispatcher_features or {},
            }
            if mode_override and getattr(self, 'dispatcher_enabled', False):
                self.state_data['dispatcher_mode'] = mode_override
                try:
                    dp = self.dispatcher.get_grid_params(mode_override)
                    self.state_data['grid_distance'] = dp.grid_distance_pct
                    self.state_data['take_profit_pct'] = dp.take_profit_pct
                    self.state_data['slot_multiplier'] = dp.slot_multiplier
                    self.state_data['max_grids'] = dp.max_grid_levels
                    logger.info(f"@DISPATCHER_GRID@ {symbol} using {mode_override}: distance={dp.grid_distance_pct}% tp={dp.take_profit_pct}%")
                except Exception as dp_err:
                    logger.warning(f"@DISPATCHER_GRID_WARN@ Failed to apply {mode_override}: {dp_err}")

            logger.info(f"@GRID_LAUNCH@ {symbol} level0 BUY {amount} @ {buy_price} (dry_run={is_dry_run})")
            oid = self._grid_place('buy', symbol, amount, buy_price, 'L0')
            if not oid:
                self.state_data = {}
                self.state = BotState.IDLE
                return
            self.state_data['grid_buy_order_id'] = oid
            self.last_grid_update = time.time()
            self.state = BotState.BUYING
            logger.info(f"@GRID_ACTIVE@ {symbol} | entry order {oid} | max averaging levels {self._grid_max_levels()}")
        except Exception as e:
            # order_manager.buy raises before any state change -> nothing rests on the exchange
            logger.error(f"@GRID_ERROR@ Failed to launch grid network: {e}", exc_info=True)
            self.price_history[symbol] = [price, time.time()]
            self.state_data = {}
            self.state = BotState.IDLE

    # ------------------------------------------------------------------
    # BUYING tick while the grid is active
    # ------------------------------------------------------------------
    def _handle_grid_buying(self, elapsed: float, timeout_sec: float) -> None:
        sd = self.state_data
        symbol = sd['symbol']

        # 1. Take-profit first: if the position already closed, nothing else matters.
        tp_id = sd.get('tp_order_id')
        if tp_id:
            tp = self._grid_fetch(tp_id, symbol)
            if tp is None:
                sd['tp_unknown_count'] = int(sd.get('tp_unknown_count', 0)) + 1
            else:
                sd['tp_unknown_count'] = 0
                st = tp.get('status')
                if st in ('closed', 'filled'):
                    self._on_grid_tp_filled(tp)
                    return
                if st in ('canceled', 'rejected', 'expired'):
                    logger.warning(f"@GRID_TP_GONE@ {symbol} TP {tp_id} is {st}; re-placing")
                    sd['tp_order_id'] = None
                    sd['order_id'] = None
                    if sd.get('total_qty', 0) > 0:
                        self._refresh_grid_tp(symbol)

        # 2. The resting buy level.
        buy_id = sd.get('grid_buy_order_id')
        if buy_id:
            order = self._grid_fetch(buy_id, symbol)
            if order is None:
                unk = int(sd.get('status_unknown_count', 0)) + 1
                sd['status_unknown_count'] = unk
                if unk == 1 or unk % 30 == 0:
                    logger.warning(f"@GRID_STATUS_UNKNOWN@ {symbol} buy {buy_id} unknown x{unk}")
                if sd.get('current_level', 0) == 0 and elapsed >= timeout_sec * 2:
                    sd['is_grid_active'] = False
                    self._resolve_buy_by_balance(symbol, buy_id, 'grid_entry_status_unknown')
                return
            sd['status_unknown_count'] = 0
            st = order.get('status')
            filled = safe_float(order.get('filled'))
            avg = safe_float(order.get('average') or order.get('price'))
            if st in ('closed', 'filled'):
                if filled <= 0:
                    filled = safe_float(order.get('amount'))
                if filled <= 0 or avg <= 0:
                    logger.error(f"@GRID_CLOSED_NO_FILL_DATA@ {symbol} {buy_id}")
                    sd['is_grid_active'] = False
                    self._resolve_buy_by_balance(symbol, buy_id, 'grid_closed_without_fill_data')
                    return
                self._on_grid_level_filled({**order, 'filled': filled, 'average': avg})
                return
            if st in ('canceled', 'rejected', 'expired'):
                if filled > 0 and avg > 0:
                    logger.warning(f"@GRID_PARTIAL@ {symbol} level {sd.get('current_level')} {st} after partial fill {filled} @ {avg}")
                    self._on_grid_level_filled({**order, 'filled': filled, 'average': avg})
                    return
                sd['grid_buy_order_id'] = None
                if sd.get('total_qty', 0) <= 0:
                    logger.warning(f"@GRID_ENTRY_CANCELED@ {symbol} entry {st}, nothing held")
                    self._transition_to_idle(f'grid_entry_{st}')
                else:
                    self._end_grid_to_position(f'level_{st}')
                return
            # Level 0 is a marketable limit at ask: it must fill quickly or be re-evaluated.
            if sd.get('current_level', 0) == 0 and elapsed >= timeout_sec:
                logger.warning(f"@GRID_ENTRY_TIMEOUT@ {symbol} entry open for {elapsed:.0f}s -> cancel")
                self._grid_cancel(buy_id, symbol)
                post = self._grid_fetch(buy_id, symbol)
                pf = safe_float(post.get('filled')) if post else 0.0
                pp = safe_float(post.get('average') or post.get('price')) if post else 0.0
                if post is None:
                    sd['is_grid_active'] = False
                    self._resolve_buy_by_balance(symbol, buy_id, 'grid_entry_timeout_unknown')
                elif pf > 0 and pp > 0:
                    self._on_grid_level_filled({**post, 'filled': pf, 'average': pp})
                else:
                    sd['grid_buy_order_id'] = None
                    self._transition_to_idle('grid_entry_timeout')
                return

        # 3. Guards while averaging (only once something is held).
        if sd.get('total_qty', 0) > 0:
            px = self._get_fresh_price(symbol)
            last = safe_float(px.get('last')) if px else 0.0
            knife = float(self.hydra_net_config.get('failing_knife_threshold', -3.0))
            entry = safe_float(sd.get('entry_price'))
            if last > 0 and entry > 0 and knife < 0 and last <= entry * (1 + knife / 100):
                logger.warning(f"@GRID_KNIFE@ {symbol} price {last} <= entry {entry} {knife}% -> stop averaging")
                self._end_grid_to_position('falling_knife')
                return
            max_hold = float(self.config.get_trading_config().get('hard_exit_timeout_sec', 1800))
            if time.time() - safe_float(sd.get('grid_started_at')) > max_hold:
                logger.warning(f"@GRID_MAX_HOLD@ {symbol} grid open > {max_hold:.0f}s -> stop averaging")
                self._end_grid_to_position('grid_max_hold')
                return

    # ------------------------------------------------------------------
    # Fills
    # ------------------------------------------------------------------
    def _on_grid_level_filled(self, order: Dict) -> None:
        """A buy level filled: accumulate REAL fill data, protect the position with a
        fresh TP, then place the next level (or finish the grid)."""
        sd = self.state_data
        symbol = sd.get('symbol')
        try:
            executed_price = safe_float(order.get('average') or order.get('price'))
            executed_qty = safe_float(order.get('filled') or order.get('amount'))
            if executed_price <= 0 or executed_qty <= 0:
                logger.error(f"@GRID_ERROR@ bad fill data p={executed_price} q={executed_qty}")
                return

            level = int(sd.get('current_level', 0))
            sd['total_cost'] = safe_float(sd.get('total_cost')) + executed_price * executed_qty
            sd['total_qty'] = safe_float(sd.get('total_qty')) + executed_qty
            sd['avg_price'] = sd['total_cost'] / sd['total_qty']
            sd['filled_levels'] = int(sd.get('filled_levels', 0)) + 1
            sd['grid_buy_order_id'] = None
            # mirrors used by generic code paths (in_position, panic, reconcile)
            sd['buy_price'] = sd['avg_price']
            sd['amount'] = sd['total_qty']
            if not sd.get('first_fill_at'):
                sd['first_fill_at'] = time.time()
            logger.info(f"@GRID_RECALC@ {symbol} level {level} filled {executed_qty} @ {executed_price}; "
                        f"avg={sd['avg_price']:.6f} qty={sd['total_qty']} levels={sd['filled_levels']}")
            METRICS.grid_level.labels(symbol=symbol).set(level)
            METRICS.grid_avg_price.labels(symbol=symbol).set(sd['avg_price'])

            # Protect what we hold before adding to it.
            self._refresh_grid_tp(symbol)

            next_level = level + 1
            if next_level > self._grid_max_levels():
                self._end_grid_to_position('grid_complete')
                return
            if not self._place_grid_level(symbol, next_level):
                self._end_grid_to_position('level_rejected')
        except Exception as e:
            logger.error(f"@GRID_ERROR@ fill handling failed: {e}", exc_info=True)
            self._end_grid_to_position('fill_error')

    def _place_grid_level(self, symbol: str, level: int) -> bool:
        """Place averaging level `level` (>=1). False if it could not be placed."""
        sd = self.state_data
        q = self._grid_level_params(symbol, level)
        if q is None:
            logger.warning(f"@GRID_LEVEL_SKIP@ {symbol} level {level}: below exchange limits")
            return False
        amount, price = q
        try:
            oid = self._grid_place('buy', symbol, amount, price, f'L{level}')
        except Exception as e:
            # e.g. Insufficient balance for the Martingale size — the position stays, grid stops
            logger.error(f"@GRID_LEVEL_ERROR@ {symbol} level {level}: {e}")
            return False
        if not oid:
            return False
        sd['grid_buy_order_id'] = oid
        sd['current_level'] = level
        sd['level_placed_at'] = time.time()
        sd['level_price'] = price
        sd['level_amount'] = amount
        logger.info(f"@GRID_LEVEL@ {symbol} level {level} BUY {amount} @ {price} id={oid}")
        return True

    def _refresh_grid_tp(self, symbol: str) -> None:
        """Cancel the previous TP (and ONLY the TP) and place a new one for the whole
        accumulated quantity at avg_price * (1 + tp%)."""
        sd = self.state_data
        total_qty = safe_float(sd.get('total_qty'))
        avg_price = safe_float(sd.get('avg_price'))
        if total_qty <= 0 or avg_price <= 0:
            return
        old_tp = sd.get('tp_order_id')
        if old_tp:
            self._grid_cancel(old_tp, symbol)
            sd['tp_order_id'] = None
            sd['order_id'] = None

        qty = total_qty
        if not self._grid_is_dry():
            held = self.exchange.get_coin_balance(symbol.split('/')[0])
            if held is not None and held > self._dust_threshold(symbol):
                # sell what we actually hold, never more than the grid accounted for
                qty = min(held, total_qty)
            else:
                qty = total_qty * (1 - 0.0015)   # fee taken in base coin, balance not yet readable
        tp_price = avg_price * (1 + self._grid_tp_pct() / 100)
        q = self.exchange.quantize_order(symbol, qty, tp_price)
        if q is None:
            logger.critical(f"@GRID_TP_IMPOSSIBLE@ {symbol}: qty {qty} @ {tp_price} below exchange limits; position unprotected")
            return
        qty, tp_price = q
        try:
            oid = self._grid_place('sell', symbol, qty, tp_price, 'TP')
        except Exception as e:
            logger.error(f"@GRID_TP_ERROR@ {symbol}: {e}")
            return
        sd['tp_order_id'] = oid
        sd['order_id'] = oid
        sd['target_sell_price'] = tp_price
        sd['tp_amount'] = qty
        logger.info(f"@GRID_TP_SET@ {symbol} SELL {qty} @ {tp_price} (+{self._grid_tp_pct()}% of avg {avg_price:.6f}) id={oid}")

    def _on_grid_tp_filled(self, tp_order: Dict) -> None:
        """TP sold the whole position while the grid was still averaging."""
        sd = self.state_data
        symbol = sd['symbol']
        self._grid_cancel(sd.get('grid_buy_order_id'), symbol)
        sd['grid_buy_order_id'] = None
        if not sd.get('exit_logged'):
            close_price = safe_float(tp_order.get('average') or tp_order.get('price') or sd.get('target_sell_price'))
            sold_qty = safe_float(tp_order.get('filled')) or safe_float(sd.get('tp_amount')) or safe_float(sd.get('total_qty'))
            avg = safe_float(sd.get('avg_price'))
            profit = self._calc_pnl(avg, close_price, sold_qty)
            self.session_profit += profit
            METRICS.session_profit.set(self.session_profit)
            METRICS.active_positions.set(0)
            self.trade_db.log_trade(symbol, "buy", sold_qty, avg, confidence=100.0)
            self.trade_db.log_trade(symbol, "sell", sold_qty, close_price, confidence=0.0, profit=profit)
            self._apply_dispatcher_feedback(profit)
            self._update_symbol_cooldown(symbol)
            tag = "PROFIT_TAKEN" if profit >= 0 else "LOSS_TAKEN"
            logger.info(f"@{tag}@ {symbol} grid TP ${profit:+.2f} @ {close_price} (levels={sd.get('filled_levels')})")
            sd['exit_logged'] = True
        sd['is_grid_active'] = False
        self._transition_to_idle('grid_tp_filled')

    def _end_grid_to_position(self, reason: str) -> None:
        """Stop averaging: cancel the resting buy, make sure a TP rests, hand the
        accumulated position to the normal IN_POSITION management."""
        sd = self.state_data
        symbol = sd.get('symbol')
        self._grid_cancel(sd.get('grid_buy_order_id'), symbol)
        sd['grid_buy_order_id'] = None
        if safe_float(sd.get('total_qty')) <= 0:
            self._transition_to_idle(f'grid_end_{reason}_nothing_held')
            return
        if not sd.get('tp_order_id'):
            self._refresh_grid_tp(symbol)
        avg = safe_float(sd.get('avg_price'))
        qty = safe_float(sd.get('total_qty'))
        if not sd.get('entry_logged'):
            trade_id = self.trade_db.log_trade(symbol, "buy", qty, avg, confidence=100.0)
            df = sd.get('dispatcher_features')
            if df and trade_id and trade_id > 0:
                try:
                    self.trade_db.log_dispatcher_features(
                        trade_id=trade_id, symbol=symbol,
                        confidence=df.get('confidence', 0.0), rvol_spike=df.get('rvol_spike', 0.0),
                        rvol_local=df.get('rvol_local', 0.0), dump_depth=df.get('dump_depth', 0.0),
                        obi_skew=df.get('obi_skew', 0.0), btc_1h=df.get('btc_1h', 0.0),
                        score=df.get('score', 0.0), mode=df.get('mode', 'normal'),
                    )
                except Exception as link_err:
                    logger.debug(f"@DISPATCHER_LINK_WARN@ {link_err}")
            sd['entry_logged'] = True
        METRICS.active_positions.set(1)
        sd.update({
            'is_grid_active': False,
            'buy_price': avg,
            'amount': qty,
            'buy_time': safe_float(sd.get('first_fill_at')) or time.time(),
            'order_id': sd.get('tp_order_id'),
            'is_breakeven': False,
            'partial_tp_hit': False,
            'trailing_high': avg,
            'grid_end_reason': reason,
        })
        self.state = BotState.IN_POSITION
        logger.info(f"@GRID_END@ {symbol} ({reason}): {qty} @ avg {avg:.6f}, TP {sd.get('tp_order_id')} -> IN_POSITION")

    # ------------------------------------------------------------------
    # Periodic re-pricing of the resting averaging level (ATR follows the market)
    # ------------------------------------------------------------------
    def _synchronize_grid_network(self) -> None:
        try:
            now = time.time()
            if now - self.last_grid_update < self.grid_update_interval:
                return
            self.last_grid_update = now
            sd = self.state_data
            symbol = sd.get('symbol')
            if not symbol or not sd.get('is_grid_active') or not self.hydra_net_config.get('enabled', False):
                return
            buy_id = sd.get('grid_buy_order_id')
            level = int(sd.get('current_level', 0))
            if not buy_id or level < 1:
                return  # level 0 (entry at ask) is never re-priced
            q = self._grid_level_params(symbol, level)
            if q is None:
                return
            new_amount, new_price = q
            cur_price = safe_float(sd.get('level_price'))
            if cur_price > 0 and abs(new_price - cur_price) / cur_price < 0.0005:
                return
            new_id = self._grid_amend(buy_id, symbol, new_amount, new_price, side='buy')
            if new_id:
                sd['grid_buy_order_id'] = new_id
                sd['level_price'] = new_price
                sd['level_amount'] = new_amount
                logger.info(f"@GRID_REPRICE@ {symbol} level {level}: {cur_price} -> {new_price}")
        except Exception as e:
            logger.error(f"@GRID_ERROR@ sync failed: {e}")
