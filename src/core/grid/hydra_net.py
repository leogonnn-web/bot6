"""HYDRA-NET: Martingale grid logic."""
import time
import sys
import os
from typing import Dict, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float
from metrics import METRICS

from ..state_enum import BotState
from indicators.matrix import ATRAnalyzer


def get_next_grid_level(entry_price, total_amount, current_level, base_order_size_usdt, atr=None):
    """
    Рассчитывает параметры следующего лимитного ордера в сетке Мартингейла.

    Args:
        entry_price: Цена входа в позицию (фиксированная база для расчета сетки)
        total_amount: Общая сумма в USDT (не используется в расчете)
        current_level: Текущий уровень (1, 2 или 3)
        base_order_size_usdt: Базовый размер ордера в USDT
        atr: ATR value for adaptive spacing (optional)

    Returns:
        dict: {'next_price': float, 'next_amount_usdt': float}
    """
    # ATR-based spacing: minimum 0.4%, ATR-based otherwise
    if atr and atr > 0:
        atr_pct = (atr / entry_price) * 100
        grid_distance_pct = max(0.4, atr_pct * 0.5) / 100  # Convert to decimal
    else:
        grid_distance_pct = 0.004  # Fallback to 0.4%

    martingale_multiplier = 1.5

    # Расчет цены от фиксированной entry_price
    next_price = entry_price * ((1 - grid_distance_pct) ** current_level)

    # Расчет объема в USDT с множителем Мартингейла
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
    def _synchronize_grid_network(self) -> None:
        """
        HYDRA-NET: Синхронизация сетки Мартингейла с правильной математикой
        Управляет коленами сетки: entry_price фиксируется при входе, уровни рассчитываются от него
        """
        try:
            now = time.time()
            if now - self.last_grid_update < self.grid_update_interval:
                return

            self.last_grid_update = now
            symbol = self.state_data.get('symbol')
            if not symbol:
                return

            if not self.hydra_net_config.get('enabled', False):
                return

            order_id = self.state_data.get('order_id')
            is_dry_run = self.state_data.get('is_dry_run', False)
            if not order_id:
                return

            # Проверка статуса текущего ордера
            if is_dry_run and order_id.startswith('virtual_'):
                pass  # Виртуальные ордера пропускаем
            else:
                try:
                    order = self.exchange.fetch_order(order_id, symbol)
                    if order['status'] in ['closed', 'filled']:
                        logger.info(f"@GRID_FILLED@ Колено исполнено для {symbol}")
                        self._on_grid_level_filled(order)
                        return
                except Exception as e:
                    logger.error(f"@GRID_ERROR@ Ошибка получения ордера: {e}")
                    return

            # Инициализация entry_price при первом входе
            if 'entry_price' not in self.state_data:
                tickers = self.ws_tickers_cache
                if symbol not in tickers:
                    return
                price_now = safe_float(tickers[symbol]['ask'])
                self.state_data['entry_price'] = price_now
                self.state_data['current_level'] = 1
                logger.info(f"@GRID_INIT@ entry_price зафиксирован: {price_now:.6f}, уровень: 1")

            # Получаем параметры следующего колена
            entry_price = self.state_data['entry_price']
            current_level = self.state_data['current_level']
            trading_config = self.config.get_trading_config()
            base_order_size_usdt = trading_config.get('base_order_size_usdt', 5.0)

            # Get ATR for adaptive spacing
            atr = None
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=20)
                if len(ohlcv) >= 14:
                    atr = ATRAnalyzer.calculate(ohlcv, period=14)
            except Exception as e:
                logger.debug(f"@ATR_SYNC_WARN@ Failed to fetch ATR for grid sync ({symbol}): {e}")

            grid_params = get_next_grid_level(entry_price, 0, current_level, base_order_size_usdt, atr)
            next_price = grid_params['next_price']
            next_amount_usdt = grid_params['next_amount_usdt']

            # Конвертируем USDT в количество актива
            tickers = self.ws_tickers_cache
            price_now = safe_float(tickers[symbol]['ask'])
            next_amount = next_amount_usdt / price_now
            next_amount = float(self.exchange.exchange.amount_to_precision(symbol, next_amount))
            next_price = float(self.exchange.exchange.price_to_precision(symbol, next_price))

            # Обновляем ордер только если цена изменилась
            current_buy_price = self.state_data.get('buy_price', 0)
            if abs(next_price - current_buy_price) > 0.000001:
                if is_dry_run and order_id.startswith('virtual_'):
                    updated = True
                else:
                    updated = self.exchange.amend_order(
                        order_id=order_id,
                        symbol=symbol,
                        amount=next_amount,
                        price=next_price
                    )

                if updated:
                    self.state_data['buy_price'] = next_price
                    self.state_data['amount'] = next_amount
                    logger.info(f"@GRID_LEVEL@ Уровень {current_level}: цена={next_price:.6f}, объем={next_amount_usdt:.2f} USDT")

        except Exception as e:
            logger.error(f"@GRID_ERROR@ Ошибка синхронизации сетки: {e}")

    def _on_grid_level_filled(self, order: Dict) -> None:
        """
        Обработка исполнения колена сетки на основе РЕАЛЬНЫХ данных биржи Bybit
        Накапливает фактический объем монет и затраты, исключая проскальзывания.
        """
        try:
            symbol = self.state_data.get('symbol')
            current_level = self.state_data.get('current_level', 1)
            config_max = self.hydra_net_config.get('max_grid_levels', 3)
            # CapitalRouter may have lowered max_grid_levels at runtime
            capital_max = config_max
            if hasattr(self, 'capital_router'):
                capital_max = self.capital_router.state.max_grid_levels
            max_levels = min(config_max, capital_max)

            # Инициализация накопителей при первом входе, если их еще нет
            if 'total_cost' not in self.state_data or 'total_qty' not in self.state_data:
                entry_price = self.state_data.get('entry_price', self.state_data.get('buy_price'))
                trading_config = self.config.get_trading_config()
                base_size = trading_config.get('base_order_size_usdt', 5.0)
                self.state_data['total_cost'] = base_size
                self.state_data['total_qty'] = base_size / entry_price

            # Достаем из ответа Bybit реальные данные по только что сработавшему колену
            executed_price = safe_float(order.get('average') or order.get('price'))
            executed_qty = safe_float(order.get('filled') or order.get('amount'))

            if executed_price <= 0 or executed_qty <= 0:
                logger.error(f"@GRID_ERROR@ Некорректные данные ордера: p={executed_price}, q={executed_qty}")
                return

            # Накапливаем реальные затраты и монеты
            self.state_data['total_cost'] += (executed_price * executed_qty)
            self.state_data['total_qty'] += executed_qty

            # Считаем истинную среднюю цену позиции
            avg_price = self.state_data['total_cost'] / self.state_data['total_qty']
            actual_qty = self.state_data['total_qty']

            logger.info(f"@GRID_RECALC@ Колено {current_level} учтено. Новая средняя цена: {avg_price:.6f}, Всего монет: {actual_qty}")
            
            # Update metrics
            METRICS.grid_level.labels(symbol=symbol).set(current_level)
            METRICS.grid_avg_price.labels(symbol=symbol).set(avg_price)

            # Проверяем, есть ли следующее колено усреднения
            if current_level < max_levels:
                next_level = current_level + 1
                self.state_data['current_level'] = next_level

                # Рассчитываем параметры для следующего колена (Мартингейл)
                entry_price = self.state_data['entry_price']
                trading_config = self.config.get_trading_config()
                base_order_size_usdt = trading_config.get('base_order_size_usdt', 5.0)

                # Get ATR for adaptive spacing
                atr = None
                try:
                    ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=20)
                    if len(ohlcv) >= 14:
                        atr = ATRAnalyzer.calculate(ohlcv, period=14)
                except Exception as e:
                    logger.debug(f"@ATR_FILLED_WARN@ Failed to fetch ATR after grid fill ({symbol}): {e}")

                grid_params = get_next_grid_level(entry_price, 0, next_level, base_order_size_usdt, atr)

                next_price = grid_params['next_price']
                next_amount_usdt = grid_params['next_amount_usdt']

                # Переводим доллары в монеты по текущему рыночному аску
                tickers = self.ws_tickers_cache
                price_now = safe_float(tickers[symbol]['ask'])
                next_amount = next_amount_usdt / price_now

                next_amount = float(self.exchange.exchange.amount_to_precision(symbol, next_amount))
                next_price = float(self.exchange.exchange.price_to_precision(symbol, next_price))

                # Выставляем следующее лимитное колено усреднения
                is_dry_run = trading_config.get('dry_run', False)
                if is_dry_run:
                    order_id = f"virtual_grid_{next_level}_{int(time.time())}"
                    logger.info(f"@DRY_RUN_GRID@ Создано виртуальное колено №{next_level}: {next_amount} по {next_price:.6f}")
                else:
                    buy_order = self.order_manager.buy(symbol, next_amount, next_price)
                    order_id = buy_order['id']
                    logger.info(f"@GRID_ORDER@ Реальное колено №{next_level} создано на Bybit: {order_id}")

                # Обновляем данные ордера в стейте
                self.state_data['order_id'] = order_id
                self.state_data['buy_price'] = next_price
                self.state_data['amount'] = next_amount
                self.state_data['is_grid_active'] = True

                # Сдвигаем Take Profit пониже вслед за упавшей средней ценой позиции!
                self._update_take_profit_for_grid(symbol, actual_qty, avg_price)

            else:
                # Все доступные 3 колена сетки заполнились
                self.state_data['is_grid_active'] = False
                self.state_data['amount'] = actual_qty
                self.state_data['buy_price'] = avg_price
                self.state_data['buy_time'] = time.time()
                self.state_data['is_breakeven'] = False
                self.state_data['partial_tp_hit'] = False
                self.state_data['trailing_high'] = avg_price

                # Выставляем окончательный Тейк-Профит
                self._update_take_profit_for_grid(symbol, actual_qty, avg_price)

                self.trade_db.log_trade(symbol, "buy_grid_complete", actual_qty, avg_price, confidence=100.0)
                logger.info(f"@GRID_COMPLETE@ Все колена сетки заполнены! Ждем отскока к TP: {actual_qty} монеты по средней цене {avg_price:.6f}")

        except Exception as e:
            logger.error(f"@GRID_ERROR@ Критическая ошибка при обработке колена: {e}")
            self.state_data = {}
            self.state = BotState.IDLE

    def _update_take_profit_for_grid(self, symbol: str, amount: float, avg_price: float) -> None:
        """
        Обновление Take Profit на +0.8% от средневзвешенной цены
        """
        try:
            # Отменяем существующий TP ордер если есть
            existing_order_id = self.state_data.get('order_id')
            if existing_order_id and not existing_order_id.startswith('virtual_'):
                try:
                    self.exchange.cancel_order(existing_order_id, symbol)
                    logger.info(f"@TP_CANCEL@ Отменен старый TP ордер: {existing_order_id}")
                except Exception as e:
                    logger.debug(f"@TP_CANCEL_WARN@ Failed to cancel old TP order: {e}")

            # Расчет TP цены: +0.8% от avg_price
            tp_pct = self.hydra_net_config.get('take_profit_pct', 0.8)
            tp_price = avg_price * (1 + (tp_pct / 100))
            tp_price = float(self.exchange.exchange.price_to_precision(symbol, tp_price))

            amount = float(self.exchange.exchange.amount_to_precision(symbol, amount))

            # Выставляем TP ордер
            trading_config = self.config.get_trading_config()
            is_dry_run = trading_config.get('dry_run', False)
            if is_dry_run:
                order_id = f"virtual_tp_{int(time.time())}"
                logger.info(f"@DRY_RUN_TP@ Виртуальный TP: {amount} @ {tp_price} (+{tp_pct}% от avg={avg_price:.6f})")
            else:
                sell_order = self.order_manager.sell(symbol, amount, tp_price)
                order_id = sell_order['id']
                logger.info(f"@TP_SET@ TP ордер создан: {order_id} | {amount} @ {tp_price} (+{tp_pct}% от avg={avg_price:.6f})")

            self.state_data['order_id'] = order_id
            self.state_data['target_sell_price'] = tp_price

        except Exception as e:
            logger.error(f"@TP_ERROR@ Ошибка установки TP: {e}")

    def _fix_executed_grid_deal(self, order: Dict) -> None:
        """
        HYDRA-NET: Fix executed grid deal and set take-profit
        Transitions from grid mode to normal position monitoring
        """
        try:
            symbol = self.state_data.get('symbol')
            if not symbol:
                return

            time.sleep(2.5)  # Wait for balance update

            # Get actual balance
            balance = self.exchange.fetch_balance()
            coin_name = symbol.split('/')[0]
            actual_qty = safe_float(balance['free'].get(coin_name, 0))

            # Use filled amount from order if balance is 0
            filled = safe_float(order.get('filled', self.state_data.get('amount', 0)))
            raw_amount = actual_qty if actual_qty > 0 else filled

            # Apply precision to amount
            safe_amount = float(self.exchange.exchange.amount_to_precision(symbol, raw_amount))

            # Check minimum amount limits
            market = self.exchange.exchange.markets.get(symbol, {})
            min_amount = market.get('limits', {}).get('amount', {}).get('min', 0)
            min_cost = market.get('limits', {}).get('cost', {}).get('min', 0)

            buy_price = self.state_data.get('buy_price', 0)
            order_cost = safe_amount * buy_price

            # Fallback: if amount is below minimum, round up to minimum
            if safe_amount < min_amount and min_amount > 0:
                logger.warning(f"@GRID_FIX@ Amount {safe_amount} below minimum {min_amount}, rounding up")
                safe_amount = min_amount
                safe_amount = float(self.exchange.exchange.amount_to_precision(symbol, safe_amount))

            # Fallback: if cost is below minimum, skip order creation
            if order_cost < min_cost and min_cost > 0:
                logger.warning(f"@GRID_FIX@ Order cost ${order_cost:.2f} below minimum ${min_cost:.2f}, skipping TP order")
                # Transition to IDLE state without creating sell order
                self.state_data = {}
                self.state = BotState.IDLE
                return

            # Calculate take-profit price using hydra_net config (not generic trading config)
            take_profit_pct = self.hydra_net_config.get('take_profit_pct', 0.8)
            sell_raw = buy_price * (1 + (take_profit_pct / 100))
            sell_p = float(self.exchange.exchange.price_to_precision(symbol, sell_raw))

            # Ensure sell price is above buy price
            if sell_p <= buy_price:
                sell_p += self.exchange.exchange.markets[symbol]['precision']['price']

            # Create sell order
            is_dry_run = trading_config.get('dry_run', False)
            if is_dry_run:
                logger.info(f"@DRY_RUN_TP@ Virtual TP set for {symbol} @ ${sell_p}")
                order_id = "virtual_tp_12345"
            else:
                sell_order = self.order_manager.sell(symbol, safe_amount, sell_p)
                order_id = sell_order['id']

            # Transition to normal position monitoring
            self.state_data['order_id'] = order_id
            self.state_data['amount'] = safe_amount
            self.state_data['is_grid_active'] = False
            self.state_data['buy_time'] = time.time()
            self.state_data['target_sell_price'] = sell_p
            self.state_data['is_breakeven'] = False
            self.state_data['partial_tp_hit'] = False
            self.state_data['trailing_high'] = buy_price

            # Log trade and link dispatcher features
            trade_id = self.trade_db.log_trade(symbol, "buy", safe_amount, buy_price, confidence=100.0)
            df = self.state_data.get('dispatcher_features')
            if df and trade_id > 0:
                try:
                    self.trade_db.log_dispatcher_features(
                        trade_id=trade_id, symbol=symbol,
                        confidence=df.get('confidence', 0.0),
                        rvol_spike=df.get('rvol_spike', 0.0),
                        rvol_local=df.get('rvol_local', 0.0),
                        dump_depth=df.get('dump_depth', 0.0),
                        obi_skew=df.get('obi_skew', 0.0),
                        btc_1h=df.get('btc_1h', 0.0),
                        score=df.get('score', 0.0),
                        mode=df.get('mode', 'normal'),
                    )
                    logger.info(f"@DISPATCHER_LINK@ Grid buy linked trade_id={trade_id}")
                except Exception as link_err:
                    logger.debug(f"@DISPATCHER_LINK_WARN@ {link_err}")
            logger.info(f"@GRID_EXECUTED@ Position filled at bottom: {symbol} | TP set @ ${sell_p}")

        except Exception as e:
            logger.error(f"@GRID_ERROR@ Failed to fix executed grid deal: {e}")
            # Reset state on error
            self.state_data = {}
            self.state = BotState.IDLE

    def _launch_grid_network(self, symbol: str, price: float, tickers: Dict, mode_override: Optional[str] = None, dispatcher_features: Optional[Dict] = None) -> None:
        """
        HYDRA-NET: Launch dynamic grid network
        Creates initial buy order that will be synchronized with price.
        Optional mode_override from HydraDispatcher overrides default grid params.
        Optional dispatcher_features dict stores scan-time features for later DB linkage.
        """
        try:
            # Check if grid mode is enabled
            if not self.hydra_net_config.get('enabled', False):
                logger.info("@GRID_DISABLED@ HYDRA-NET disabled, using normal entry")
                self._enter_trade(symbol, price, tickers, dispatcher_features=dispatcher_features)
                return

            # Capital Router guard: check if grid is allowed
            if hasattr(self, 'capital_router') and not self.capital_router.state.grid_allowed:
                logger.info(f"@GRID_BLOCKED@ Capital Router: grid disabled (mode={self.capital_router.state.mode}), single-shot entry")
                self._enter_trade(symbol, price, tickers, dispatcher_features=dispatcher_features)
                return

            buy_price = safe_float(tickers[symbol]['ask'])
            trading_config = self.config.get_trading_config()
            slot_size = trading_config.get('slot_size', 18.0)

            # Calculate order amount
            amount_target = float(self.exchange.exchange.amount_to_precision(symbol, slot_size / buy_price))

            # Check minimum order size
            min_order_size = self.hydra_net_config.get('min_order_size_usdt', 5.0)
            if slot_size < min_order_size:
                logger.warning(f"@GRID_WARN@ Slot size ${slot_size} below minimum ${min_order_size}")
                return

            is_dry_run = trading_config.get('dry_run', False)

            if is_dry_run:
                logger.info(f"@DRY_RUN_GRID@ Virtual grid order: {amount_target} {symbol} @ ${buy_price}")
                order_id = "virtual_grid_12345"
            else:
                logger.info(f"@GRID_LAUNCH@ Launching HYDRA-NET for {symbol} @ ${buy_price}")
                order = self.order_manager.buy(symbol, amount_target, buy_price)
                order_id = order['id']

            # Initialize grid state
            # NOTE: entry_price and current_level are fixed HERE (not lazily in
            # _synchronize_grid_network) so dry-run grid fill never races the
            # synchronizer. _synchronize_grid_network keeps a guarded
            # `if 'entry_price' not in self.state_data` so it does not overwrite.
            self.state_data = {
                'symbol': symbol,
                'buy_price': buy_price,
                'buy_time': time.time(),
                'amount': amount_target,
                'amount_target': amount_target,
                'order_id': order_id,
                'is_grid_active': True,
                'is_breakeven': False,
                'partial_tp_hit': False,
                'trailing_high': buy_price,
                'is_dry_run': is_dry_run,
                'entry_price': buy_price,
                'current_level': 1,
                'dispatcher_features': dispatcher_features or {},
            }
            # Dispatcher mode override (Phase 1: observation only)
            if mode_override and getattr(self, 'dispatcher_enabled', False):
                self.state_data['dispatcher_mode'] = mode_override
                try:
                    dp = self.dispatcher.get_grid_params(mode_override)
                    self.state_data['grid_distance'] = dp.grid_distance_pct
                    self.state_data['take_profit_pct'] = dp.take_profit_pct
                    self.state_data['slot_multiplier'] = dp.slot_multiplier
                    self.state_data['max_grids'] = dp.max_grid_levels
                    logger.info(f"@DISPATCHER_GRID@ {symbol} using {mode_override} params: distance={dp.grid_distance_pct}% tp={dp.take_profit_pct}%")
                except Exception as dp_err:
                    logger.warning(f"@DISPATCHER_GRID_WARN@ Failed to apply {mode_override}: {dp_err}")
            logger.info(f"@GRID_INIT_FIX@ entry_price принудительно зафиксирован при создании ордера: {buy_price:.6f}")

            self.last_grid_update = time.time()
            self.state = BotState.BUYING

            logger.info(f"@GRID_ACTIVE@ HYDRA-NET launched for {symbol} | Order ID: {order_id}")

        except Exception as e:
            logger.error(f"@GRID_ERROR@ Failed to launch grid network: {e}")
            # Reset price history on error
            self.price_history[symbol] = [price, time.time()]
