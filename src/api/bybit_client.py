"""
HYDRA Bybit Client v17.0
Clean API client for Bybit V5 exchange
Only exchange communication - no business logic
"""

import os
import time
import threading
import asyncio
from typing import Optional, Dict, List
import sys

# Add shared to path for logger
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared')))
from logger_setup import logger
from utils import safe_float


class WebSocketListener:
    """WebSocket listener for real-time ticker updates with thread safety"""
    
    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret = secret
        self.ws_exchange = None
        self.ws_active = False
        self.ws_thread = None
        self.ws_running = False
        
        # Thread-safe price storage
        self.latest_prices: Dict[str, Dict] = {}
        self.price_lock = threading.Lock()
        self.last_update_time: Dict[str, float] = {}
        self.update_lock = threading.Lock()
        
        # Fallback settings
        self.data_timeout_sec = 10  # Consider data stale after 10 seconds
        
    def _init_websocket(self) -> bool:
        """Initialize WebSocket client (ccxt.pro)"""
        try:
            import ccxt.pro as ccxtpro
            self.ws_exchange = ccxtpro.bybit({
                'apiKey': self.api_key,
                'secret': self.secret,
                'enableRateLimit': True,
                'options': {
                    'version': 'v5',
                    'defaultType': 'spot'
                }
            })
            logger.info("@WS_CONN@ WebSocket client initialized")
            self.ws_active = True
            return True
        except (ImportError, Exception) as e:
            logger.warning(f"@WS_WARN@ WebSocket unavailable: {e}")
            return False
    
    async def _ws_ticker_loop(self, symbols: List[str]):
        """Async WebSocket loop for ticker updates"""
        try:
            while self.ws_running:
                try:
                    tickers = await self.ws_exchange.watch_tickers(symbols)
                    
                    # Thread-safe update of prices
                    with self.price_lock:
                        for symbol, ticker in tickers.items():
                            self.latest_prices[symbol] = {
                                'ask': safe_float(ticker.get('ask')),
                                'bid': safe_float(ticker.get('bid')),
                                'last': safe_float(ticker.get('last')),
                                'timestamp': time.time()
                            }
                    
                    # Thread-safe update of timestamps
                    with self.update_lock:
                        current_time = time.time()
                        for symbol in symbols:
                            self.last_update_time[symbol] = current_time
                    
                except Exception as e:
                    logger.error(f"WebSocket error: {e}")
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"WebSocket loop error: {e}")
    
    def _run_websocket_async(self, symbols: List[str]):
        """Run async WebSocket loop in thread"""
        try:
            asyncio.run(self._ws_ticker_loop(symbols))
        except Exception as e:
            logger.error(f"WebSocket thread error: {e}")
    
    def start(self, symbols: List[str]) -> bool:
        """Start WebSocket streaming in background thread"""
        if not self._init_websocket():
            return False
        
        try:
            # Always include BTC/USDT for trend analysis
            all_symbols = list(set(symbols + ['BTC/USDT']))
            self.ws_running = True
            self.ws_thread = threading.Thread(target=self._run_websocket_async, args=(all_symbols,), daemon=True)
            self.ws_thread.start()
            logger.info(f"@WS_START@ WebSocket streaming started for {len(all_symbols)} symbols (including BTC/USDT)")
            return True
        except Exception as e:
            logger.error(f"Failed to start WebSocket: {e}")
            return False
    
    def stop(self):
        """Stop WebSocket streaming"""
        self.ws_running = False
        if self.ws_thread:
            self.ws_thread.join(timeout=2)
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
        """Check if WebSocket is active and running"""
        return self.ws_active and self.ws_running and self.ws_thread and self.ws_thread.is_alive()


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
        """Clear exchange caches"""
        try:
            self.exchange.clear_caches()
            if self.ws_active and self.ws_exchange:
                self.ws_exchange.clear_caches()
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
        """Get account balance"""
        try:
            if not self.api_key or not self.secret:
                return {'free': {'USDT': 1000.0}, 'total': {'USDT': 1000.0}}
            return self.exchange.fetch_balance()
        except Exception as e:
            logger.error(f"@EXCHANGE_ERROR@ fetch_balance: {e}")
            return {'free': {'USDT': 1000.0}, 'total': {'USDT': 1000.0}}
    
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
        """Amend existing order"""
        try:
            amt_str = self.exchange.amount_to_precision(symbol, amount)
            price_str = self.exchange.price_to_precision(symbol, price)
            logger.debug(f"@AMEND_ORDER@ Amend {order_id} -> {symbol} @ ${price_str}")
            
            return self.exchange.amend_order(
                id=order_id,
                symbol=symbol,
                type='limit',
                side='buy',
                amount=float(amt_str),
                price=float(price_str)
            )
        except Exception as e:
            logger.error(f"@ORDER_ERROR@ Amend failed for {order_id}: {e}")
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
                    except:
                        pass
                time.sleep(0.5)
        
        logger.warning(f"@ORDER_FALLBACK@ Order {order_id} not found, assuming closed")
        return {'id': order_id, 'status': 'closed', 'price': None, 'filled': 0.0}
