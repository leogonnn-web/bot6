"""Breakeven order + adaptive ATR-based breakeven timeout."""
import time
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float

from indicators.matrix import ATRAnalyzer


class BreakevenMixin:
    def _set_breakeven(self):
        try:
            symbol = self.state_data['symbol']
            trading_config = self.config.get_trading_config()
            is_dry_run = trading_config.get('dry_run', False)
            order_id = self.state_data.get('order_id')

            try:
                market_info = self.exchange.exchange.market(symbol)
                taker_fee = safe_float(market_info.get('taker', 0.001))
                maker_fee = safe_float(market_info.get('maker', 0.001))
            except Exception as e:
                logger.debug(f"@FEE_FALLBACK@ Failed to read market fees: {e}")
                taker_fee, maker_fee = 0.001, 0.001

            breakeven_multiplier = 1.0 + (taker_fee + maker_fee) + 0.0002

            # Отменяем ордер только если это не dry_run и order_id существует
            if not is_dry_run and order_id and not order_id.startswith('virtual_'):
                try:
                    self.exchange.cancel_order(order_id, symbol)
                    time.sleep(0.5)
                except Exception as cancel_err:
                    logger.warning(f"@BREAKEVEN_CANCEL_WARN@ Failed to cancel order {order_id}: {cancel_err}")

            buy_price = self.state_data['buy_price']
            amount = self.state_data.get('amount', 0)
            raw_price = buy_price * breakeven_multiplier
            breakeven_price = float(self.exchange.exchange.price_to_precision(symbol, raw_price))

            breakeven_price = buy_price * 1.001  # Small profit to cover fees
            amount = float(self.exchange.exchange.amount_to_precision(symbol, amount))
            breakeven_price = float(self.exchange.exchange.price_to_precision(symbol, breakeven_price))

            if is_dry_run:
                new_order_id = f"virtual_breakeven_{int(time.time())}"
                logger.info(f"@DRY_RUN_BREAKEVEN@ Virtual breakeven order: {amount} @ {breakeven_price}")
            else:
                new_order = self.order_manager.sell(symbol, amount, breakeven_price)
                new_order_id = new_order['id']
                logger.info(f"@BREAKEVEN_ORDER@ Breakeven order created: {new_order_id}")

            self.state_data['order_id'] = new_order_id
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
                logger.debug(f"@ATR_TIMEOUT@ Insufficient OHLCV data: {len(ohlcv)} candles, using fallback 1200s")
                return 1200  # Fallback to 20 min

            # Calculate ATR
            atr = ATRAnalyzer.calculate(ohlcv, period=14)
            current_price = safe_float(ohlcv[-1][4])

            if atr <= 0 or current_price <= 0:
                logger.debug(f"@ATR_TIMEOUT@ Invalid ATR: {atr} or price: {current_price}, using fallback 1200s")
                return 1200

            # Normalize ATR as % of price
            atr_pct = (atr / current_price) * 100

            # Base timeout: higher volatility (ATR) = faster exit needed
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
            trading_config = self.config.get_trading_config()
            config_timeout = trading_config.get('breakeven_timeout_sec', None)
            if config_timeout:
                timeout_sec = config_timeout

            logger.debug(f"@ATR_TIMEOUT@ ATR: {atr_pct:.2f}%, Breakeven timeout: {timeout_sec}s")
            return int(timeout_sec)

        except Exception as e:
            logger.error(f"@ATR_ERROR@ ATR timeout calculation error for {symbol}: {e}", exc_info=True)
            return 1200  # Fallback
