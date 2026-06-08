"""HYDRA Bybit Client v17.0
Clean API client for Bybit V5 exchange
Only exchange communication - no business logic
"""

import os
import time
import threading
import asyncio
import json
from collections import deque
from typing import Optional, Dict, List, Deque, Tuple
import sys

# Add shared to path for logger
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float


class WebSocketListener:
    """Raw WebSocket listener for Bybit V5 real-time ticker updates with auto-reconnect"""

    WS_URL = 'wss://stream.bybit.com/v5/public/spot'

    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret = secret
        self.ws_active = False
        self.ws_thread = None
        self.ws_running = False

        # Thread-safe price storage
        self.latest_prices: Dict[str, Dict] = {}
        self.price_lock = threading.Lock()
        self.last_update_time: Dict[str, float] = {}
        self.update_lock = threading.Lock()

        # Reconnection / health
        self.data_timeout_sec = 10
        self.reconnect_interval_sec = 5
        self.max_reconnect_attempts = 10
        self._reconnect_count = 0
        self._last_reconnect_time = 0.0

        # publicTrade aggregator (for ToxicFlowFilter).
        # Per-symbol ring buffer of recent trades (last 200 events) for
        # short-window (e.g. 3s) statistics, plus an exponentially-weighted
        # moving average of trade size that approximates a 1h rolling
        # average without retaining the raw history. Memory cost is O(1)
        # per symbol regardless of trade frequency.
        # Each entry: (ts, price, size, side)  side ∈ {'Buy','Sell'} (taker)
        self.trade_history: Dict[str, Deque[Tuple[float, float, float, str]]] = {}
        self.trade_size_ema: Dict[str, float] = {}
        self.trade_count_total: Dict[str, int] = {}
        self.trade_lock = threading.Lock()
        # EMA decay: α=0.001 ≈ effective window of last ~1000 trades; on
        # an active spot pair this corresponds to roughly 20-60 minutes,
        # close to the 1-hour rolling average requested in the spec.
        self._trade_ema_alpha = 0.001
        # Minimum number of trades before large-print detection becomes
        # active for a symbol (avoids triggering on early outliers).
        self._trade_warmup_count = 100
        # Max trades retained per symbol (3s windows on the busiest pairs
        # rarely exceed ~150 trades).
        self._trade_history_maxlen = 200

    @staticmethod
    def _to_bybit_topic(symbol: str) -> str:
        """Convert CCXT symbol (BTC/USDT) to Bybit topic (BTCUSDT)"""
        return symbol.replace('/', '')

    @staticmethod
    def _from_bybit_symbol(raw: str) -> str:
        """Convert Bybit symbol back to CCXT format if needed"""
        # Bybit sends symbols like BTCUSDT; we keep them as-is for matching
        return raw

    def _update_price(self, symbol: str, data: Dict) -> None:
        """Thread-safe price update from Bybit V5 ticker data"""
        now = time.time()
        # Bybit V5 spot ticker provides lastPrice; bid1Price/ask1Price may be absent
        last = safe_float(data.get('lastPrice', data.get('last')))
        ask = safe_float(data.get('ask1Price', data.get('askPrice')))
        bid = safe_float(data.get('bid1Price', data.get('bidPrice')))
        # Approximate missing bid/ask with a 0.05% synthetic spread around lastPrice
        if last > 0:
            if ask == 0:
                ask = round(last * 1.0005, 8)
            if bid == 0:
                bid = round(last * 0.9995, 8)
        price = {
            'ask': ask,
            'bid': bid,
            'last': last,
            'timestamp': now,
            'turnover24h': safe_float(data.get('turnover24h', 0)),
            'volume24h': safe_float(data.get('volume24h', 0)),
            'bidVolume': safe_float(data.get('bid1Size', 0)),
            'askVolume': safe_float(data.get('ask1Size', 0)),
        }
        with self.price_lock:
            self.latest_prices[symbol] = price
        with self.update_lock:
            self.last_update_time[symbol] = now

    # ------------------------------------------------------------------
    # publicTrade ingestion (for ToxicFlowFilter)
    # ------------------------------------------------------------------
    def _record_trade(self, symbol: str, ts: float, price: float,
                      size: float, side: str) -> None:
        """Thread-safe ingestion of a single publicTrade event.

        side is the taker side: 'Buy' means the taker bought into ask
        (aggressive buy), 'Sell' means the taker sold into bid
        (aggressive sell). Updates per-symbol ring buffer and EMA of
        trade size.
        """
        if price <= 0 or size <= 0:
            return
        with self.trade_lock:
            buf = self.trade_history.get(symbol)
            if buf is None:
                buf = deque(maxlen=self._trade_history_maxlen)
                self.trade_history[symbol] = buf
            buf.append((ts, price, size, side))

            cnt = self.trade_count_total.get(symbol, 0)
            self.trade_count_total[symbol] = cnt + 1

            prev_ema = self.trade_size_ema.get(symbol)
            if prev_ema is None:
                self.trade_size_ema[symbol] = size
            else:
                a = self._trade_ema_alpha
                self.trade_size_ema[symbol] = prev_ema + a * (size - prev_ema)

    def get_trade_stats(self, symbol: str, window_sec: float = 3.0) -> Dict:
        """Return aggregate trade stats over the last `window_sec` seconds.

        Returns dict with keys:
            count          — total trades in window
            sell_count     — number of taker-sell trades
            buy_count      — number of taker-buy trades
            sell_pct       — sell_count / count  (0..1, or 0.0 if count=0)
            buy_pct        — buy_count / count   (0..1, or 0.0 if count=0)
            consec_down    — longest run of strictly-decreasing prices
                             ending at the most recent trade
            last_size      — size of the most recent trade  (0.0 if none)
            last_side      — side of the most recent trade  ('' if none)
            ema_size       — current EMA of trade size       (None if warmup)
            size_ratio     — last_size / ema_size             (1.0 if warmup)
            total_seen     — total trades ever ingested for the symbol
                             (for warmup gating in detectors)
        """
        cutoff = time.time() - window_sec
        with self.trade_lock:
            buf = self.trade_history.get(symbol)
            ema = self.trade_size_ema.get(symbol)
            total_seen = self.trade_count_total.get(symbol, 0)
            recent = [t for t in buf if t[0] >= cutoff] if buf else []
        count = len(recent)
        if count == 0:
            return {
                'count': 0, 'sell_count': 0, 'buy_count': 0,
                'sell_pct': 0.0, 'buy_pct': 0.0, 'consec_down': 0,
                'last_size': 0.0, 'last_side': '',
                'ema_size': ema, 'size_ratio': 1.0,
                'total_seen': total_seen,
            }
        sell_count = sum(1 for t in recent if t[3] == 'Sell')
        buy_count = count - sell_count
        # Count longest consecutive strictly-decreasing tail ending at the
        # most recent trade. Iterate backwards over price series.
        consec_down = 0
        for i in range(len(recent) - 1, 0, -1):
            if recent[i][1] < recent[i - 1][1]:
                consec_down += 1
            else:
                break
        last_ts, last_price, last_size, last_side = recent[-1]
        size_ratio = (last_size / ema) if (ema and ema > 0) else 1.0
        return {
            'count': count,
            'sell_count': sell_count,
            'buy_count': buy_count,
            'sell_pct': sell_count / count,
            'buy_pct': buy_count / count,
            'consec_down': consec_down,
            'last_size': last_size,
            'last_side': last_side,
            'ema_size': ema,
            'size_ratio': size_ratio,
            'total_seen': total_seen,
        }

    async def _ws_ticker_loop(self, symbols: List[str]):
        """Async WebSocket loop using raw websockets connection to Bybit V5"""
        import websockets

        all_symbols = list(set(symbols + ['BTC/USDT']))
        # Subscribe to tickers (price), orderbook.1 (bid/ask sizes for OBI),
        # and publicTrade (for ToxicFlowFilter aggressive-sweep / large-print).
        topics = (
            [f"tickers.{self._to_bybit_topic(s)}" for s in all_symbols]
            + [f"orderbook.1.{self._to_bybit_topic(s)}" for s in all_symbols]
            + [f"publicTrade.{self._to_bybit_topic(s)}" for s in all_symbols]
        )
        # Bybit V5 limits args to 10 per subscription message
        BATCH_SIZE = 10
        topic_batches = [topics[i:i + BATCH_SIZE] for i in range(0, len(topics), BATCH_SIZE)]
        # Build a reverse map once (raw_symbol -> ccxt_symbol) so each WS
        # message doesn't re-scan all_symbols (O(N) per message → O(1)).
        raw_to_ccxt = {self._to_bybit_topic(s): s for s in all_symbols}

        while self.ws_running:
            try:
                async with websockets.connect(self.WS_URL, ping_interval=20, ping_timeout=10) as ws:
                    logger.info("@WS_CONN@ Connected to Bybit V5 WebSocket")

                    # Subscribe in batches of 10
                    for batch in topic_batches:
                        sub_msg = {"op": "subscribe", "args": batch}
                        await ws.send(json.dumps(sub_msg))
                        # Wait briefly for subscription ack
                        try:
                            ack = await asyncio.wait_for(ws.recv(), timeout=2)
                            ack_data = json.loads(ack)
                            if ack_data.get('success') is False:
                                logger.warning(f"@WS_SUB_FAIL@ {ack_data.get('ret_msg')}")
                        except asyncio.TimeoutError:
                            pass

                    self.ws_active = True
                    self._reconnect_count = 0

                    async for message in ws:
                        if not self.ws_running:
                            break
                        try:
                            msg = json.loads(message)
                            # Bybit heartbeat (JSON frame)
                            if msg.get('op') == 'ping':
                                await ws.send(json.dumps({"op": "pong"}))
                                continue
                            # Subscription success / failure
                            if 'success' in msg:
                                continue
                            # Ticker data
                            topic = msg.get('topic', '')
                            if topic.startswith('tickers.'):
                                raw_symbol = topic.split('.', 1)[1]
                                ccxt_symbol = raw_to_ccxt.get(raw_symbol)
                                data = msg.get('data', {})
                                if ccxt_symbol and data:
                                    self._update_price(ccxt_symbol, data)
                            elif topic.startswith('orderbook.1.'):
                                raw_symbol = topic.split('.', 2)[2]
                                ccxt_symbol = raw_to_ccxt.get(raw_symbol)
                                data = msg.get('data', {})
                                if ccxt_symbol and data:
                                    # orderbook.1 gives b/a arrays: [[price, size], ...]
                                    bids = data.get('b', [])
                                    asks = data.get('a', [])
                                    bid_size = safe_float(bids[0][1]) if bids and len(bids[0]) >= 2 else 0.0
                                    ask_size = safe_float(asks[0][1]) if asks and len(asks[0]) >= 2 else 0.0
                                    # Merge sizes into existing price dict (create if absent)
                                    with self.price_lock:
                                        existing = self.latest_prices.get(ccxt_symbol, {})
                                        existing['bidVolume'] = bid_size
                                        existing['askVolume'] = ask_size
                                        self.latest_prices[ccxt_symbol] = existing
                            elif topic.startswith('publicTrade.'):
                                raw_symbol = topic.split('.', 1)[1]
                                ccxt_symbol = raw_to_ccxt.get(raw_symbol)
                                if not ccxt_symbol:
                                    continue
                                trades = msg.get('data', [])
                                if not isinstance(trades, list):
                                    continue
                                # publicTrade payload is a list of trade
                                # frames; ingest each one.
                                for tr in trades:
                                    try:
                                        ts = float(tr.get('T', 0)) / 1000.0
                                        if ts <= 0:
                                            ts = time.time()
                                        price = safe_float(tr.get('p'))
                                        size = safe_float(tr.get('v'))
                                        side = tr.get('S', '')
                                        self._record_trade(ccxt_symbol, ts, price, size, side)
                                    except Exception as e:
                                        logger.debug(f"@WS_TRADE_PARSE@ {e}")
                        except Exception as e:
                            logger.debug(f"@WS_PARSE@ {e}")

            except Exception as e:
                logger.warning(f"@WS_ERR@ {e}")
                self.ws_active = False
                self._reconnect_count += 1
                if self._reconnect_count > self.max_reconnect_attempts:
                    logger.critical("@WS_FATAL@ Max reconnects exceeded, giving up")
                    self.ws_running = False
                    break

                delay = min(self.reconnect_interval_sec * (2 ** (self._reconnect_count - 1)), 60)
                logger.info(f"@WS_RECONNECT@ Attempt {self._reconnect_count}/{self.max_reconnect_attempts} in {delay}s")
                await asyncio.sleep(delay)

    def _run_websocket_async(self, symbols: List[str]):
        """Run async WebSocket loop in daemon thread"""
        try:
            asyncio.run(self._ws_ticker_loop(symbols))
        except Exception as e:
            logger.error(f"@WS_THREAD_FATAL@ {e}")

    def start(self, symbols: List[str]) -> bool:
        """Start WebSocket streaming in background thread"""
        try:
            import websockets
        except ImportError:
            logger.warning("@WS_WARN@ websockets library not installed")
            return False

        self.ws_running = True
        self._reconnect_count = 0
        self.ws_thread = threading.Thread(
            target=self._run_websocket_async,
            args=(symbols,),
            daemon=True
        )
        self.ws_thread.start()
        logger.info(f"@WS_START@ WebSocket streaming started for {len(symbols)} symbols")
        return True

    def stop(self):
        """Stop WebSocket streaming"""
        self.ws_running = False
        self.ws_active = False
        if self.ws_thread:
            self.ws_thread.join(timeout=3)
        logger.info("@WS_STOP@ WebSocket streaming stopped")

    def get_price(self, symbol: str) -> Optional[Dict]:
        """Get price for symbol with thread safety"""
        with self.price_lock:
            return self.latest_prices.get(symbol)

    def is_data_fresh(self, symbol: str) -> bool:
        """Check if data for symbol is fresh (updated within timeout)"""
        with self.update_lock:
            last_update = self.last_update_time.get(symbol, 0)
            return (time.time() - last_update) < self.data_timeout_sec

    def is_active(self) -> bool:
        """Check if WebSocket is active and running with fresh data"""
        thread_alive = self.ws_thread and self.ws_thread.is_alive()
        has_fresh = any(
            (time.time() - t) < self.data_timeout_sec
            for t in self.last_update_time.values()
        )
        return self.ws_active and self.ws_running and thread_alive and has_fresh


class BybitClient:
    """Bybit V5 API client - pure exchange interface"""
    
    def __init__(self, api_key: str = None, secret: str = None):
        import ccxt
        
        self.api_key = api_key or os.getenv('BYBIT_API_KEY', '')
        self.secret = secret or os.getenv('BYBIT_API_SECRET', '')
        
        logger.info("@EXCHANGE_INIT@ Initializing Bybit V5 client...")
        
        # REST API client
        self.exchange = ccxt.bybit({
            'apiKey': self.api_key,
            'secret': self.secret,
            'enableRateLimit': True,
            'options': {
                'version': 'v5',
                'defaultType': 'spot',
                'createMarketBuyOrderRequiresPrice': False
            }
        })
        
        # WebSocket listener
        self.ws_listener = WebSocketListener(self.api_key, self.secret)
    
    def load_markets(self) -> Dict:
        """Load market specifications"""
        try:
            if not self.api_key or not self.secret:
                logger.warning("@EXCHANGE_WARN@ No API keys, virtual mode")
                return {}
            return self.exchange.load_markets()
        except Exception as e:
            logger.warning(f"@EXCHANGE_WARN@ Load markets failed: {e}")
            return {}
    
    def clear_caches(self):
        """Clear exchange caches (REST only; WS listener has no ccxt cache)"""
        try:
            self.exchange.clear_caches()
        except Exception as e:
            logger.error(f"@CACHE_ERROR@ Clear cache failed: {e}")
    
    def amount_to_precision(self, symbol: str, amount: float) -> str:
        """Round amount to exchange precision"""
        try:
            return self.exchange.amount_to_precision(symbol, amount)
        except Exception as e:
            logger.error(f"@PRECISION_ERROR@ amount_to_precision: {e}")
            return str(amount)
    
    def price_to_precision(self, symbol: str, price: float) -> str:
        """Round price to exchange precision"""
        try:
            return self.exchange.price_to_precision(symbol, price)
        except Exception as e:
            logger.error(f"@PRECISION_ERROR@ price_to_precision: {e}")
            return str(price)
    
    def fetch_balance(self) -> Dict:
        """Get account balance.

        SAFETY: only return a virtual $1000 balance when API keys are missing
        (i.e. dry-run / unconfigured environment). In LIVE mode any error must
        surface as zero balance so CapitalRouter does NOT silently promote the
        bot to a higher tier on transient network failures.
        """
        if not self.api_key or not self.secret:
            return {'free': {'USDT': 1000.0}, 'total': {'USDT': 1000.0}}
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            logger.error(f"@EXCHANGE_ERROR@ fetch_balance failed (LIVE): {e}")
            return {'free': {'USDT': 0.0}, 'total': {'USDT': 0.0}}
    
    def fetch_tickers(self, symbols: List[str]) -> Dict:
        """Get tickers for multiple symbols"""
        try:
            return self.exchange.fetch_tickers(symbols)
        except Exception as e:
            logger.error(f"@EXCHANGE_ERROR@ fetch_tickers: {e}")
            return {}
    
    def fetch_ticker(self, symbol: str) -> Dict:
        """Get ticker for single symbol"""
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"@EXCHANGE_ERROR@ fetch_ticker for {symbol}: {e}")
            return {'last': 0.0, 'ask': 0.0, 'bid': 0.0}
    
    def fetch_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 60) -> List:
        """Get OHLCV candle data"""
        try:
            return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as e:
            logger.error(f"@EXCHANGE_ERROR@ fetch_ohlcv for {symbol}: {e}")
            return []
    
    def fetch_closed_orders(self, symbol: str, limit: int = 5) -> List:
        """Get closed orders"""
        try:
            return self.exchange.fetch_closed_orders(symbol, limit=limit)
        except Exception as e:
            logger.error(f"@EXCHANGE_ERROR@ fetch_closed_orders for {symbol}: {e}")
            return []
    
    def create_limit_buy_order(self, symbol: str, amount: float, price: float) -> Dict:
        """Create limit buy order"""
        try:
            logger.info(f"@LIMIT_BUY@ BUY {amount} {symbol} @ ${price}")
            return self.exchange.create_limit_buy_order(symbol, amount, price)
        except Exception as e:
            logger.error(f"@ORDER_ERROR@ BUY order failed for {symbol}: {e}")
            raise
    
    def create_limit_sell_order(self, symbol: str, amount: float, price: float) -> Dict:
        """Create limit sell order"""
        try:
            logger.info(f"@LIMIT_SELL@ SELL {amount} {symbol} @ ${price}")
            return self.exchange.create_limit_sell_order(symbol, amount, price)
        except Exception as e:
            logger.error(f"@ORDER_ERROR@ SELL order failed for {symbol}: {e}")
            raise
    
    def create_market_sell_order(self, symbol: str, amount: float) -> Dict:
        """Create market sell order (panic sell)"""
        try:
            logger.warning(f"@MARKET_SELL@ Market sell {amount} {symbol}")
            return self.exchange.create_market_sell_order(symbol, amount)
        except Exception as e:
            logger.error(f"@ORDER_ERROR@ Market sell failed for {symbol}: {e}")
            raise
    
    def cancel_order(self, order_id: str, symbol: str) -> Dict:
        """Cancel order"""
        try:
            logger.info(f"@CANCEL_ORDER@ Cancel {order_id} for {symbol}")
            return self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            logger.error(f"@ORDER_ERROR@ Cancel failed for {order_id}: {e}")
            raise
    
    def amend_order(self, order_id: str, symbol: str, amount: float, price: float) -> Optional[Dict]:
        """
        Amend existing order with Rate Limit protection
        Retries on DDOS/Rate Limit errors with exponential backoff
        """
        max_retries = 3
        base_delay = 0.5
        
        for attempt in range(max_retries):
            try:
                amt_str = self.exchange.amount_to_precision(symbol, amount)
                price_str = self.exchange.price_to_precision(symbol, price)
                logger.debug(f"@AMEND_ORDER@ Amend {order_id} -> {symbol} @ ${price_str} (attempt {attempt + 1}/{max_retries})")
                
                return self.exchange.amend_order(
                    id=order_id,
                    symbol=symbol,
                    type='limit',
                    side='buy',
                    amount=float(amt_str),
                    price=float(price_str)
                )
            except Exception as e:
                error_str = str(e).lower()
                
                # Check for rate limit errors
                if any(keyword in error_str for keyword in ['rate limit', 'ddos', 'too many requests', '33004']):
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)  # Exponential backoff
                        logger.warning(f"@RATE_LIMIT@ Rate limit hit, retrying in {delay}s... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"@RATE_LIMIT@ Max retries exceeded for amend_order {order_id}")
                        return None
                else:
                    # Non-rate-limit error, fail immediately
                    logger.error(f"@ORDER_ERROR@ Amend failed for {order_id}: {e}")
                    return None
        
        logger.error(f"@ORDER_ERROR@ Amend failed for {order_id} after {max_retries} retries")
        return None
    
    def fetch_order(self, order_id: str, symbol: str) -> Dict:
        """Get order status with fallback"""
        for attempt in range(3):
            try:
                return self.exchange.fetch_order(order_id, symbol, params={"acknowledged": True})
            except Exception as e:
                if "last 500 orders" in str(e):
                    try:
                        time.sleep(0.5)
                        closed_orders = self.fetch_closed_orders(symbol, limit=5)
                        for order in closed_orders:
                            if order['id'] == order_id:
                                return order
                    except Exception as e:
                        logger.debug(f"@ORDER_LOOKUP_WARN@ Closed orders lookup failed: {e}")
                time.sleep(0.5)
        
        logger.warning(f"@ORDER_FALLBACK@ Order {order_id} not found, assuming closed")
        return {'id': order_id, 'status': 'closed', 'price': None, 'filled': 0.0}
