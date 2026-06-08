"""SCANNING state handler + market entry scanner."""
import time
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float
from metrics import METRICS

from ..state_enum import BotState
import indicators.matrix as _ind_matrix


class ScanningStateMixin:
    def _handle_scanning_state(self):
        self._scan_for_entries()
        if self.state == BotState.SCANNING:
            self.state = BotState.IDLE

    def _scan_for_entries(self) -> None:
        try:
            # Maintenance mode: block all new entries
            if getattr(self, 'maintenance_mode', False):
                logger.debug("@MAINTENANCE_SKIP@ New signals blocked (maintenance mode)")
                return
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
            METRICS.scan_cycle_total.inc()
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

                # ToxicFlowFilter: block entries on adversarial flow
                # (aggressive sell sweeps, large sell prints). Runs before
                # any expensive analysis so toxic symbols are cheap to skip.
                # The filter logs @TOXIC_TRIGGER@ itself once per storm
                # (state 0→1 transition); subsequent blocks during the
                # 10-min cooldown stay silent here to avoid log spam.
                if getattr(self, 'toxic_enabled', True):
                    try:
                        if self.toxic_filter.is_toxic(symbol):
                            METRICS.toxic_active.labels(symbol=symbol).set(1)
                            tox_state = self.toxic_filter.get_state(symbol)
                            reason_tag = (tox_state.get('last_trigger_reason') or 'unknown').split(':', 1)[0]
                            METRICS.toxic_blocks_total.labels(symbol=symbol, reason=reason_tag).inc()
                            logger.debug(
                                f"@TOXIC_BLOCK@ {symbol} reason={tox_state.get('last_trigger_reason', '')}"
                            )
                            continue
                        else:
                            METRICS.toxic_active.labels(symbol=symbol).set(0)
                    except Exception as tox_err:
                        # Filter must never break scanning; log and proceed.
                        logger.debug(f"@TOXIC_WARN@ {symbol}: {tox_err}")

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

                    # MIN VOLUME filter: skip illiquid / low-volume symbols
                    try:
                        min_volume = trading_config.get('min_volume_usdt', 500000)
                        quote_vol = safe_float(tickers[symbol].get('quoteVolume', 0))
                        if quote_vol > 0 and quote_vol < min_volume:
                            logger.debug(f"@VOLUME_SKIP@ {symbol} volume=${quote_vol:,.0f} < ${min_volume:,.0f}")
                            continue
                    except Exception as vol_err:
                        logger.debug(f"@VOLUME_WARN@ Volume check failed for {symbol}: {vol_err}")

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
                    if drop > 0.05:
                        logger.info(f"@DROP_DEBUG@ {symbol} ref={self.price_history[symbol][0]:.6f} cur={price_now:.6f} drop={drop:.2f}%")

                    # ── UNIFIED ENTRY PATH: scan → analyze → dispatch → grid ──
                    # No more PATH A (HYDRA-NET fast path) or PATH B (normal).
                    # Every signal goes through the full validation chain.
                    trading_config = self.config.get_trading_config()
                    min_drop = trading_config.get('drop_threshold', 0.65)
                    min_drop = max(min_drop, 0.5)  # Hard guard: never below 0.5%

                    if drop < min_drop:
                        continue
                    logger.info(f"@DROP_HIT@ {symbol} drop={drop:.2f}% >= {min_drop}%, checking RVOL...")

                    try:
                        ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=60)
                        logger.info(f"@OHLCV_FMT@ {symbol} type={type(ohlcv).__name__} len={len(ohlcv)} first={str(ohlcv[0])[:80] if ohlcv else 'empty'}")
                    except Exception as e:
                        logger.info(f"@OHLCV_FAIL@ {symbol}: {e}")
                        continue
                    real_rvol = self._calculate_real_rvol(ohlcv)
                    min_rvol = trading_config.get('min_rvol_threshold', 1.5)

                    # TANK MODE: Stricter RVOL threshold
                    if tank_mode and real_rvol < 2.0:
                        logger.debug(f"@TANK_FILTER@ RVOL too low for tank mode: {real_rvol:.1f}x < 2.0x")
                        continue

                    if real_rvol < min_rvol:
                        logger.info(f"@RVOL_SKIP@ {symbol} RVOL={real_rvol:.2f}x < {min_rvol}x, drop={drop:.2f}%")
                        continue
                    # Filter: shallow drop + low RVOL = weak momentum / fake-out
                    shallow_drop = trading_config.get('shallow_drop_threshold', 1.0)
                    shallow_rvol = trading_config.get('shallow_rvol_threshold', 2.0)
                    if drop < shallow_drop and real_rvol < shallow_rvol:
                        logger.info(f"@SHALLOW_SKIP@ {symbol} drop={drop:.2f}% < {shallow_drop}% and RVOL={real_rvol:.2f}x < {shallow_rvol}x, skipping")
                        continue
                    logger.info(f"@RVOL_PASS@ {symbol} RVOL={real_rvol:.2f}x >= {min_rvol}x, drop={drop:.2f}%")
                    logger.info(f"@INDICATORS@ {symbol} indicators_enabled={self.indicators_enabled}")

                    # ── Analyzer: always run, never bypassed ──
                    analysis = None
                    if self.indicators_enabled:
                        try:
                            analysis = _ind_matrix.analyzer.complete_analysis(
                                ohlcv_data=ohlcv,
                                current_price=price_now,
                                market_volatility=1.0,
                                btc_trend=btc_trend,
                                btc_trend_detection_enabled=market_config.get('btc_trend_detection', True)
                            )
                            logger.info(f"@ANALYZER_RAW@ {symbol} raw={analysis}")
                            if analysis.get('status', '') != 'ok':
                                logger.info(f"@ANALYZER_FAIL@ {symbol} status={analysis.get('status')} msg={analysis.get('message','')}")
                                continue
                        except Exception as e:
                            logger.info(f"@ANALYZER_EXCEPTION@ {symbol}: {e}")
                            continue
                        logger.info(f"@ANALYZER_OK@ {symbol} rec={analysis.get('recommendation')} conf={analysis.get('confidence',0):.1f}%")

                        # TANK MODE: Check for tank block reason
                        if tank_mode and 'tank_block_reason' in analysis:
                            logger.info(f"@TANK_BLOCK@ Signal blocked: {analysis['tank_block_reason']}")
                            continue

                        base_threshold = trading_config.get('min_confidence_threshold', 60.0)
                        base_threshold = max(base_threshold, 20.0)  # Hard guard: never below 20%

                        # TANK MODE: Higher threshold
                        if tank_mode:
                            base_threshold = 85.0
                            if btc_trend == "bearish":
                                logger.info(f"@TANK_BTC@ BTC bearish, blocking entry")
                                continue

                        if analysis['recommendation'] not in ['STRONG_BUY', 'BUY']:
                            logger.info(f"@SIGNAL_REJECT@ {symbol} rec={analysis.get('recommendation')} (need BUY/STRONG_BUY)")
                            continue

                        if analysis.get('confidence', 0) < base_threshold:
                            logger.info(f"@SIGNAL_REJECT@ {symbol} conf={analysis.get('confidence',0):.1f}% (need >= {base_threshold}%)")
                            continue
                    else:
                        logger.warning(f"@INDICATORS_DISABLED@ {symbol} — trading without analyzer is dangerous, skipping")
                        continue

                    # Check BTC trend (drop filter)
                    if not self._check_btc_trend():
                        continue

                    # Check BTC correlation if enabled
                    btc_correlation = self._calculate_btc_correlation(symbol)
                    correlation_threshold = market_config.get('btc_correlation_threshold', 0.5)
                    if btc_correlation < correlation_threshold:
                        logger.info(f"@CORRELATION_FILTER@ {symbol} BTC correlation {btc_correlation:.2f} < {correlation_threshold}, skipping")
                        continue

                    # ── HYDRA Dispatcher: score-based grid tuning ──
                    dispatcher_score = 0.0
                    dispatcher_mode = "normal"
                    try:
                        if getattr(self, 'dispatcher_enabled', False):
                            btc_1h_val = btc_change_1h if 'btc_change_1h' in dir() else 0.0
                            # obi_light: express OBI from top-1 bid/ask volumes in ticker cache
                            ticker_data = tickers.get(symbol, {})
                            bid_vol = safe_float(ticker_data.get('bidVolume', 0))
                            ask_vol = safe_float(ticker_data.get('askVolume', 0))
                            total_vol = bid_vol + ask_vol
                            obi_skew_val = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0.0
                            dispatcher_score = self.dispatcher.calculate_score(
                                confidence=analysis.get('confidence', 0),
                                rvol_spike=real_rvol,
                                dump_depth=drop,
                                obi_skew=obi_skew_val,
                                btc_1h=btc_1h_val,
                            )
                            dispatcher_mode = self.dispatcher.select_mode(
                                score=dispatcher_score,
                                btc_1h=btc_1h_val,
                            )
                            grid_params = self.dispatcher.get_grid_params(dispatcher_mode)
                            logger.info(
                                f"@DISPATCHER@ {symbol} score={dispatcher_score:.2f} mode={dispatcher_mode} "
                                f"grid_distance={grid_params.grid_distance_pct}% slot_mult={grid_params.slot_multiplier}x"
                            )

                            # Phase 1: Log features to DB (feedback_loop = OFF)
                            has_db = hasattr(self, 'trade_db')
                            db_ok = bool(self.trade_db) if has_db else False
                            logger.info(f"@DISPATCHER_DB_CHECK@ {symbol} has_db={has_db} db_ok={db_ok}")
                            if has_db and db_ok:
                                try:
                                    self.trade_db.log_dispatcher_features(
                                        trade_id=0,
                                        symbol=symbol,
                                        confidence=analysis.get('confidence', 0),
                                        rvol_spike=real_rvol,
                                        rvol_local=real_rvol,
                                        dump_depth=drop,
                                        obi_skew=obi_skew_val,
                                        btc_1h=btc_1h_val,
                                        score=dispatcher_score,
                                        mode=dispatcher_mode,
                                    )
                                    logger.debug(f"@DISPATCHER_LOG@ Features logged for {symbol}")
                                except Exception as db_err:
                                    import traceback
                                    logger.error(f"@DISPATCHER_LOG_WARN@ {db_err}")
                                    logger.error(traceback.format_exc())
                        else:
                            logger.warning(f"@DISPATCHER_DISABLED@ {symbol} — dispatcher not enabled, using default mode")
                    except Exception as dispatch_err:
                        logger.warning(f"@DISPATCHER_WARN@ {symbol}: {dispatch_err}")

                    logger.info(f"@SIGNAL_APPROVED@ Signal approved for {symbol} (RVOL: {real_rvol:.1f}x, Confidence: {analysis.get('confidence', 0):.1f}%)")
                    dispatcher_features = {
                        'confidence': analysis.get('confidence', 0),
                        'rvol_spike': real_rvol,
                        'rvol_local': real_rvol,
                        'dump_depth': drop,
                        'obi_skew': obi_skew_val,
                        'btc_1h': btc_1h_val,
                        'score': dispatcher_score,
                        'mode': dispatcher_mode,
                    }
                    self._launch_grid_network(symbol, price_now, tickers, mode_override=dispatcher_mode if dispatcher_score > 0 else None, dispatcher_features=dispatcher_features)
                    return
                except Exception as e:
                    logger.debug(f"@SCAN_SYMBOL_WARN@ Scan failed for {symbol}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Scan error: {e}")

    def _scan_and_collect_candidates(self):
        """Lightweight background scan: only WebSocket tickers, no REST."""
        candidates = []
        try:
            if getattr(self, 'maintenance_mode', False):
                return []
            if not self._check_global_safety():
                return []
            symbols = self.symbol_manager.get_symbols(refresh_scanner=True)
            tickers = self.ws_tickers_cache
            if not tickers:
                return []
            trading_config = self.config.get_trading_config()
            panic_drop_pct = trading_config.get('correlation_panic_drop_pct', 3.0)
            dump_count = 0
            for sym in symbols:
                if sym not in tickers: continue
                if sym not in self.price_history or not isinstance(self.price_history[sym], list):
                    continue
                ref_price = self.price_history[sym][0]
                if ref_price <= 0: continue
                cur = safe_float(tickers[sym].get('ask', 0))
                sym_drop = ((ref_price - cur) / ref_price) * 100
                if sym_drop >= panic_drop_pct:
                    dump_count += 1
            max_active_slots = self.hydra_net_config.get('max_active_slots', 3)
            if dump_count > 3:
                max_active_slots = 1
            for symbol in symbols:
                if symbol not in tickers: continue
                if self._check_symbol_cooldown(symbol): continue
                if getattr(self, 'toxic_enabled', True):
                    try:
                        if self.toxic_filter.is_toxic(symbol): continue
                    except Exception:
                        pass
                try:
                    price_now = safe_float(tickers[symbol]['ask'])
                    bid_now = safe_float(tickers[symbol].get('bid', 0))
                    spread_max_pct = trading_config.get('spread_max', 0.10)
                    if bid_now > 0 and price_now > 0:
                        spread_pct = ((price_now - bid_now) / price_now) * 100
                        if spread_pct > spread_max_pct: continue
                    min_volume = trading_config.get('min_volume_usdt', 500000)
                    quote_vol = safe_float(tickers[symbol].get('quoteVolume', tickers[symbol].get('turnover24h', 0)))
                    if quote_vol > 0 and quote_vol < min_volume: continue
                    # Lightweight proxy-RVOL via turnover24h delta
                    proxy_rvol = 0.0
                    turnover_hist = getattr(self, '_turnover_history', {}).get(symbol, [])
                    if len(turnover_hist) >= 2:
                        now_t, now_v = turnover_hist[-1]
                        for t, v in reversed(turnover_hist[:-1]):
                            if now_t - t >= 15:
                                proxy_rvol = now_v - v
                                break
                    # 00:00 UTC anomaly protection: if daily turnover was just reset
                    # on Bybit, skip proxy filter and defer to heavy RVOL validation
                    current_turnover = turnover_hist[-1][1] if turnover_hist else 0
                    if current_turnover > 0 and current_turnover < 50000:
                        # turnover24h recently reset, skip proxy filter
                        proxy_rvol = 0.0
                    min_proxy_delta = trading_config.get('min_turnover_delta_5s', 5000)
                    proxy_rvol = max(proxy_rvol, 0.0)
                    if proxy_rvol > 0 and proxy_rvol < min_proxy_delta:
                        continue
                    if symbol not in self.price_history or not isinstance(self.price_history[symbol], list):
                        self.price_history[symbol] = [price_now, time.time()]
                    if len(self.price_history[symbol]) < 2:
                        self.price_history[symbol] = [price_now, time.time()]
                    if price_now > self.price_history[symbol][0]:
                        self.price_history[symbol] = [price_now, time.time()]
                    if time.time() - self.price_history[symbol][1] > 900:
                        self.price_history[symbol] = [price_now, time.time()]
                    drop = ((self.price_history[symbol][0] - price_now) / self.price_history[symbol][0]) * 100
                    min_drop = trading_config.get('drop_threshold', 0.65)
                    min_drop = max(min_drop, 0.5)
                    if drop < min_drop: continue
                    # Lightweight candidate: no OHLCV, no analyzer, no correlation yet.
                    # Sort by drop% as a proxy for urgency.
                    composite_score = drop * proxy_rvol if proxy_rvol > 0 else drop
                    candidates.append({
                        'symbol': symbol,
                        'price': price_now,
                        'drop': drop,
                        'proxy_rvol': proxy_rvol,
                        'composite_score': composite_score,
                    })
                except Exception as e:
                    logger.debug(f"@BG_SCAN_SYMBOL_WARN@ {symbol}: {e}")
                    continue
        except Exception as e:
            logger.error(f"@BG_SCAN_ERROR@ {e}")
        candidates.sort(key=lambda x: x['composite_score'], reverse=True)
        if candidates:
            logger.info(
                f"@BG_SCAN_STATS@ checked={len(symbols)} candidates={len(candidates)} "
                f"top={candidates[0]['symbol']} comp={candidates[0]['composite_score']:.2f} drop={candidates[0]['drop']:.2f}%"
            )
        else:
            logger.info(f"@BG_SCAN_STATS@ checked={len(symbols)} candidates=0")
        return candidates[:5]

    def _validate_candidate(self, candidate, btc_trend, btc_change_1h):
        """Heavy validation for a single candidate (REST + analyzer + correlation)."""
        symbol = candidate['symbol']
        price_now = candidate['price']
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=60)
        except Exception as e:
            logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: OHLCV fetch failed: {e}")
            return None
        real_rvol = self._calculate_real_rvol(ohlcv)
        # Fallback: if REST OHLCV returns 0 volume (Bybit spot API bug), use WebSocket proxy_rvol
        if real_rvol < 0.1:
            proxy = candidate.get('proxy_rvol', 0)
            ticker = self.ws_tickers_cache.get(symbol, {})
            quote_vol_24h = safe_float(ticker.get('quoteVolume', ticker.get('turnover24h', 0)))
            if quote_vol_24h > 0 and proxy > 0:
                avg_15s = quote_vol_24h / (24 * 60 * 4)  # 4 x 15s intervals per minute
                synthetic = proxy / avg_15s if avg_15s > 0 else 0
                if synthetic > 0:
                    real_rvol = min(synthetic, 10.0)
                    logger.info(f"@RVOL_FALLBACK@ {symbol} synthetic RVOL={real_rvol:.2f}x (proxy=${proxy:,.0f}, avg_15s=${avg_15s:,.0f})")
        trading_config = self.config.get_trading_config()
        min_rvol = trading_config.get('min_rvol_threshold', 1.5)
        tank_mode = trading_config.get('tank_mode', False)
        if tank_mode and real_rvol < 2.0:
            logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: tank_mode RVOL={real_rvol:.2f}x < 2.0")
            return None
        if real_rvol < min_rvol:
            logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: RVOL={real_rvol:.2f}x < {min_rvol}x")
            self._add_to_rejected_cache(candidate, "rvol_low")
            return None
        # Filter: shallow drop + low RVOL = weak momentum / fake-out
        drop_pct = candidate.get('drop', 0)
        if drop_pct < 1.0 and real_rvol < 2.0:
            logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: shallow drop={drop_pct:.2f}% < 1.0% and RVOL={real_rvol:.2f}x < 2.0x")
            self._add_to_rejected_cache(candidate, "shallow_drop")
            return None
        market_config = self.config.get_market_conditions_config()
        analysis = None
        if self.indicators_enabled:
            try:
                analysis = _ind_matrix.analyzer.complete_analysis(
                    ohlcv_data=ohlcv, current_price=price_now, market_volatility=1.0,
                    btc_trend=btc_trend,
                    btc_trend_detection_enabled=market_config.get('btc_trend_detection', True)
                )
                if analysis.get('status', '') != 'ok':
                    logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: analyzer status={analysis.get('status', 'unknown')}")
                    return None
            except Exception as e:
                logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: analyzer exception: {e}")
                return None
            if tank_mode and 'tank_block_reason' in analysis:
                logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: tank_block={analysis.get('tank_block_reason')}")
                return None
            base_threshold = trading_config.get('min_confidence_threshold', 60.0)
            base_threshold = max(base_threshold, 20.0)
            if tank_mode:
                base_threshold = 85.0
                if btc_trend == "bearish":
                    logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: tank_mode bearish btc")
                    return None
            if analysis['recommendation'] not in ['STRONG_BUY', 'BUY']:
                logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: rec={analysis['recommendation']}")
                return None
            conf = analysis.get('confidence', 0)
            if conf < base_threshold:
                logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: conf={conf:.1f}% < {base_threshold}%")
                self._add_to_rejected_cache(candidate, "conf_low")
                return None
        else:
            logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: indicators disabled")
            return None
        if not self._check_btc_trend():
            logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: _check_btc_trend failed")
            return None
        btc_correlation = self._calculate_btc_correlation(symbol)
        correlation_threshold = market_config.get('btc_correlation_threshold', 0.5)
        if btc_correlation < correlation_threshold:
            logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: corr={btc_correlation:.2f} < {correlation_threshold}")
            self._add_to_rejected_cache(candidate, "corr_low")
            return None
        dispatcher_score = 0.0
        dispatcher_mode = "normal"
        try:
            if getattr(self, 'dispatcher_enabled', False):
                # obi_light: express OBI from top-1 bid/ask volumes in ticker cache
                ticker_data = self.ws_tickers_cache.get(symbol, {})
                bid_vol = safe_float(ticker_data.get('bidVolume', 0))
                ask_vol = safe_float(ticker_data.get('askVolume', 0))
                total_vol = bid_vol + ask_vol
                obi_skew_val = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0.0
                dispatcher_score = self.dispatcher.calculate_score(
                    confidence=analysis.get('confidence', 0), rvol_spike=real_rvol,
                    dump_depth=candidate['drop'], obi_skew=obi_skew_val, btc_1h=btc_change_1h,
                )
                dispatcher_mode = self.dispatcher.select_mode(
                    score=dispatcher_score, btc_1h=btc_change_1h,
                )
                # Phase 1: Log features to DB (feedback_loop = OFF)
                has_db = hasattr(self, 'trade_db')
                db_ok = bool(self.trade_db) if has_db else False
                if has_db and db_ok:
                    try:
                        self.trade_db.log_dispatcher_features(
                            trade_id=0,
                            symbol=symbol,
                            confidence=analysis.get('confidence', 0),
                            rvol_spike=real_rvol,
                            rvol_local=real_rvol,
                            dump_depth=candidate['drop'],
                            obi_skew=obi_skew_val,
                            btc_1h=btc_change_1h,
                            score=dispatcher_score,
                            mode=dispatcher_mode,
                        )
                        logger.debug(f"@DISPATCHER_LOG@ Features logged for {symbol}")
                    except Exception as db_err:
                        logger.error(f"@DISPATCHER_LOG_WARN@ {db_err}")
            else:
                logger.debug(f"@DISPATCHER_DISABLED@ {symbol} — dispatcher not enabled")
        except Exception as e:
            logger.info(f"@SCAN_REJECT_DETAIL@ {symbol}: dispatcher exception: {e}")
        return {
            'symbol': symbol,
            'price': price_now,
            'score': dispatcher_score,
            'mode': dispatcher_mode,
            'drop': candidate['drop'],
            'rvol': real_rvol,
            'confidence': analysis.get('confidence', 0),
            'dispatcher_features': {
                'symbol': symbol,
                'confidence': analysis.get('confidence', 0),
                'rvol_spike': real_rvol,
                'rvol_local': real_rvol,
                'dump_depth': candidate['drop'],
                'obi_skew': obi_skew_val,
                'btc_1h': btc_change_1h,
                'score': dispatcher_score,
                'mode': dispatcher_mode,
            }
        }

    def _handle_scanning_state(self):
        # Background thread continuously updates dispatcher_candidates.
        # Main thread: pick the best candidate, validate with REST, then enter.
        if getattr(self, 'maintenance_mode', False):
            self.state = BotState.IDLE
            return
        if not hasattr(self, '_candidates_lock') or not hasattr(self, 'dispatcher_candidates'):
            logger.warning("@SCAN_NO_QUEUE@ Dispatcher queue not initialized, falling back to legacy scan")
            self._scan_for_entries()
            return
        with self._candidates_lock:
            queue = list(self.dispatcher_candidates) if self.dispatcher_candidates else []
        if not queue:
            logger.debug("@SCAN_QUEUE_EMPTY@ No candidates in queue")
            self.state = BotState.IDLE
            return
        # Get BTC context once for all validations in this tick
        market_config = self.config.get_market_conditions_config()
        btc_trend = "neutral"
        btc_change_1h = 0.0
        if market_config.get('btc_trend_detection', True):
            try:
                btc_ohlcv_1h = self.exchange.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=2)
                if len(btc_ohlcv_1h) >= 2:
                    btc_open = safe_float(btc_ohlcv_1h[-2][1])
                    btc_close = safe_float(btc_ohlcv_1h[-1][4])
                    btc_change_1h = ((btc_close - btc_open) / btc_open) * 100
                    if btc_change_1h < -0.8: btc_trend = "bearish"
                    elif btc_change_1h > 0.8: btc_trend = "bullish"
            except Exception as e:
                logger.debug(f"@BTC_TREND_WARN@ {e}")
        # P.6: Retry soft-rejected candidates first
        retry_candidates = self._get_retry_candidates()
        validated_pool = []
        for rc in retry_candidates:
            sym = rc['symbol']
            if sym in self.ws_tickers_cache:
                tick = self.ws_tickers_cache[sym]
                rc['price'] = safe_float(tick.get('ask', rc['price']))
            result = self._validate_candidate(rc, btc_trend, btc_change_1h)
            if result:
                logger.info(
                    f"@SECOND_CHANCE@ {sym} passed retry "
                    f"score={result['score']:.2f} mode={result['mode']}"
                )
                validated_pool.append(result)
            if sym in getattr(self, 'rejected_cache', {}):
                del self.rejected_cache[sym]

        # Take top-3 fresh candidates by composite_score and validate all via REST
        top3 = queue[:3]
        for candidate in top3:
            symbol = candidate['symbol']
            result = self._validate_candidate(candidate, btc_trend, btc_change_1h)
            if result:
                validated_pool.append(result)
                logger.info(
                    f"@SCAN_VALID@ {symbol} score={result['score']:.2f} mode={result['mode']} "
                    f"drop={result['drop']:.2f}% rvol={result['rvol']:.1f}x"
                )
            else:
                logger.debug(f"@SCAN_REJECT@ {symbol} failed heavy validation")
            # Remove processed candidate from global queue regardless of pass/fail
            with self._candidates_lock:
                self.dispatcher_candidates = [c for c in self.dispatcher_candidates if c['symbol'] != symbol]

        if validated_pool:
            best = max(validated_pool, key=lambda x: x['score'])

            # Dynamic min_score based on BTC health
            dispatcher_cfg = self.config.config.get('dispatcher', {})
            dyn_cfg = dispatcher_cfg.get('dynamic_min_score', {})
            if dyn_cfg.get('enabled', False) and hasattr(self, 'dispatcher'):
                min_score = self.dispatcher.get_min_score(btc_change_1h, dyn_cfg)
            else:
                min_score = dispatcher_cfg.get('min_score_for_entry', 1.0)

            if best['score'] < min_score:
                logger.info(
                    f"@SCAN_REJECT_DYNAMIC@ {best['symbol']} score={best['score']:.2f} "
                    f"below dynamic_min_score={min_score:.2f} (btc_1h={btc_change_1h:+.2f}%)"
                )
                self.state = BotState.IDLE
                return

            logger.info(
                f"@SCAN_PICK@ Dispatcher chose best: {best['symbol']} score={best['score']:.2f} "
                f"mode={best['mode']} drop={best['drop']:.2f}% rvol={best['rvol']:.1f}x conf={best['confidence']:.1f}%"
            )
            self._update_symbol_cooldown(best['symbol'])
            self._launch_grid_network(
                best['symbol'], best['price'], self.ws_tickers_cache,
                mode_override=best['mode'] if best['score'] > 0 else None,
                dispatcher_features=best.get('dispatcher_features')
            )
            return

        logger.debug("@SCAN_NO_VALID@ No candidates passed heavy validation")
        self.state = BotState.IDLE

    # ------------------------------------------------------------------
    # P.6: Second-chance cache for soft rejections
    # ------------------------------------------------------------------
    def _add_to_rejected_cache(self, candidate: dict, reason: str) -> None:
        """Store a soft-rejected candidate for later retry."""
        if not hasattr(self, 'rejected_cache'):
            self.rejected_cache = {}
        symbol = candidate['symbol']
        retry_delay = getattr(self, 'rejected_retry_delay', 45)
        ttl = getattr(self, 'rejected_cache_ttl', 90)
        now = time.time()
        self.rejected_cache[symbol] = {
            'candidate': candidate,
            'reason': reason,
            'retry_at': now + retry_delay,
            'expires_at': now + ttl,
        }
        logger.debug(f"@REJECTED_CACHE@ {symbol} cached (reason={reason}, retry_in={retry_delay}s)")

    def _get_retry_candidates(self) -> list:
        """Return soft-rejected candidates whose retry window has opened."""
        if not hasattr(self, 'rejected_cache'):
            return []
        now = time.time()
        ready = []
        expired = []
        for symbol, entry in self.rejected_cache.items():
            if now > entry['expires_at']:
                expired.append(symbol)
            elif now >= entry['retry_at']:
                ready.append(entry['candidate'])
        # Clean expired
        for sym in expired:
            del self.rejected_cache[sym]
            logger.debug(f"@REJECTED_CACHE@ {sym} expired and removed")
        if ready:
            logger.info(f"@REJECTED_RETRY@ {len(ready)} candidate(s) ready for second chance")
        return ready

    def _clean_rejected_cache(self) -> None:
        """Remove all stale entries from rejected cache."""
        if not hasattr(self, 'rejected_cache'):
            return
        now = time.time()
        stale = [s for s, e in self.rejected_cache.items() if now > e['expires_at']]
        for sym in stale:
            del self.rejected_cache[sym]
        if stale:
            logger.debug(f"@REJECTED_CACHE@ Cleaned {len(stale)} stale entries")
