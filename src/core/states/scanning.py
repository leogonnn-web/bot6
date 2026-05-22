"""SCANNING state handler + market entry scanner."""
import time
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float

from ..state_enum import BotState
from indicators.matrix import analyzer


class ScanningStateMixin:
    def _handle_scanning_state(self):
        self._scan_for_entries()
        if self.state == BotState.SCANNING:
            self.state = BotState.IDLE

    def _scan_for_entries(self) -> None:
        try:
            # JSON-мост безопасности: проверка глобальной заморозки от внешнего сканера
            if not self._check_global_safety():
                # Если бот в состоянии BUYING — отменяем плавающий ордер и уходим в IDLE
                if self.state == BotState.BUYING and self.state_data.get('order_id'):
                    try:
                        symbol = self.state_data.get('symbol')
                        order_id = self.state_data.get('order_id')
                        is_dry_run = self.state_data.get('is_dry_run', False)
                        if not is_dry_run:
                            self.exchange.cancel_order(order_id, symbol)
                            logger.info(f"@GLOBAL_LOCK_CANCEL@ Canceled floating order {order_id} for {symbol}")
                        else:
                            logger.info(f"@GLOBAL_LOCK_CANCEL@ Virtual order {order_id} canceled (dry_run)")
                    except Exception as cancel_err:
                        logger.error(f"@GLOBAL_LOCK_CANCEL_ERROR@ Failed to cancel order: {cancel_err}")
                    self.state_data = {}
                    self.state = BotState.IDLE
                # Полностью запрещаем вход в новые сделки
                return

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
                except Exception as e:
                    logger.debug(f"@BTC_TREND_WARN@ BTC 1h trend fetch failed: {e}")
            try:
                btc_ohlcv_15m = self.exchange.fetch_ohlcv('BTC/USDT', timeframe='5m', limit=4)
                if len(btc_ohlcv_15m) >= 3:
                    btc_high_15m = max([safe_float(c[2]) for c in btc_ohlcv_15m])
                    btc_current = safe_float(tickers.get('BTC/USDT', {}).get('ask') or btc_ohlcv_15m[-1][4])
                    btc_drop_15m = ((btc_current - btc_high_15m) / btc_high_15m) * 100
                    trading_config = self.config.get_trading_config()
                    crash_limit = trading_config.get('btc_crash_15m_limit', -2.0)
                    if btc_drop_15m <= crash_limit:
                        print(f"Crash block: BTC dropping {btc_drop_15m:.2f}% @BTC_CRASH_BLOCK@ ", end='\r')
                        return
            except Exception as e:
                logger.debug(f"@BTC_15M_WARN@ BTC 15m crash check failed: {e}")
            print(f"Scanning market... BTC Trend: [{btc_trend.upper()}] @SCAN_WS@ ", end='\r')

            # TANK MODE: Check if enabled
            trading_config = self.config.get_trading_config()
            tank_mode = trading_config.get('tank_mode', False)

            # ── Correlation Interlock: detect systemic panic ──
            # If >3 symbols dump simultaneously, force max_active_slots=1
            max_active_slots = self.hydra_net_config.get('max_active_slots', 3)
            panic_drop_pct = trading_config.get('correlation_panic_drop_pct', 3.0)
            dump_count = 0
            for sym in symbols:
                if sym not in tickers:
                    continue
                if sym not in self.price_history or not isinstance(self.price_history[sym], list):
                    continue
                ref_price = self.price_history[sym][0]
                if ref_price <= 0:
                    continue
                cur = safe_float(tickers[sym].get('ask', 0))
                sym_drop = ((ref_price - cur) / ref_price) * 100
                if sym_drop >= panic_drop_pct:
                    dump_count += 1
            if dump_count > 3:
                max_active_slots = 1
                logger.warning(
                    f"@CORRELATION_INTERLOCK@ {dump_count} symbols dumping >{panic_drop_pct}%"
                    f" → max_active_slots forced to 1"
                )

            for symbol in symbols:
                if symbol not in tickers: continue

                # Check cooldown before scanning symbol
                if self._check_symbol_cooldown(symbol):
                    continue

                try:
                    price_now = safe_float(tickers[symbol]['ask'])

                    # SPREAD_MAX filter: skip symbols with thin / wide spread orderbooks
                    try:
                        bid_now = safe_float(tickers[symbol].get('bid', 0))
                        spread_max_pct = trading_config.get('spread_max', 0.10)
                        if bid_now > 0 and price_now > 0:
                            spread_pct = ((price_now - bid_now) / price_now) * 100
                            if spread_pct > spread_max_pct:
                                logger.debug(f"@SPREAD_SKIP@ {symbol} spread {spread_pct:.3f}% > {spread_max_pct}%")
                                continue
                    except Exception as spread_err:
                        logger.debug(f"@SPREAD_WARN@ Spread check failed for {symbol}: {spread_err}")

                    # Initialize price history if not exists
                    if symbol not in self.price_history or not isinstance(self.price_history[symbol], list):
                        self.price_history[symbol] = [price_now, time.time()]

                    # For HYDRA-NET: Use simple price comparison (current vs initial)
                    if len(self.price_history[symbol]) < 2:
                        self.price_history[symbol] = [price_now, time.time()]

                    # Update reference price if price increased (no dump)
                    if price_now > self.price_history[symbol][0]:
                        self.price_history[symbol] = [price_now, time.time()]

                    # Reset if too old (15 minutes)
                    if time.time() - self.price_history[symbol][1] > 900:
                        self.price_history[symbol] = [price_now, time.time()]

                    drop = ((self.price_history[symbol][0] - price_now) / self.price_history[symbol][0]) * 100

                    # HYDRA-NET: Check for dump conditions
                    hydra_net_enabled = self.hydra_net_config.get('enabled', False)
                    hydra_dump_threshold = self.hydra_net_config.get('dump_threshold', -3.0)
                    hydra_min_rvol = self.hydra_net_config.get('min_rvol', 2.0)

                    if hydra_net_enabled:
                        logger.debug(f"@HYDRA_DEBUG@ {symbol} price: {price_now:.6f}, drop: {drop:.2f}% (threshold: {hydra_dump_threshold}%)")

                    if hydra_net_enabled and drop >= abs(hydra_dump_threshold):
                        logger.info(f"@HYDRA_SCAN@ {symbol} dump detected: {drop:.2f}% (threshold: {abs(hydra_dump_threshold)}%)")
                        try:
                            ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=60)
                            real_rvol = self._calculate_real_rvol(ohlcv)
                            logger.info(f"@HYDRA_RVOL@ {symbol} RVOL: {real_rvol:.1f}x (threshold: {hydra_min_rvol}x)")
                            if real_rvol >= hydra_min_rvol:
                                logger.info(f"@HYDRA_ENTRY@ Launching grid for {symbol} (dump: {drop:.2f}%, RVOL: {real_rvol:.1f}x)")
                                self._launch_grid_network(symbol, price_now, tickers)
                                return
                        except Exception as e:
                            logger.error(f"@HYDRA_ERROR@ Failed to check {symbol}: {e}")
                            continue

                    # Normal trading logic
                    trading_config = self.config.get_trading_config()
                    if drop >= trading_config.get('drop_threshold', 0.65):
                        try:
                            ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=60)
                        except Exception as e:
                            logger.debug(f"@OHLCV_WARN@ Failed to fetch OHLCV for {symbol}: {e}")
                            continue
                        real_rvol = self._calculate_real_rvol(ohlcv)
                        min_rvol = trading_config.get('min_rvol_threshold', 1.5)

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

                            base_threshold = trading_config.get('min_confidence_threshold', 60.0)

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
                                self._launch_grid_network(symbol, price_now, tickers)
                                return
                        else:
                            self._launch_grid_network(symbol, price_now, tickers)
                            return
                except Exception as e:
                    logger.debug(f"@SCAN_SYMBOL_WARN@ Scan failed for {symbol}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Scan error: {e}")
