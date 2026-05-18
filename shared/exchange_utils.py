"""
HYDRA-NET - EXCHANGE UTILITIES PRO v17.4
High-speed communication interface for Bybit V5 API & WebSocket Streams
FULL EXPANDED VERSION - CLEANED FROM DUPLICATES AND EMOJI
"""
import os
import time
import sys
from typing import Optional, Dict, List

# Подключаем пути к общей папке shared
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from logger_setup import logger
from utils import safe_float

class ExchangeManager:
    """Профессиональный менеджер взаимодействия с биржей Bybit V5"""
    
    def __init__(self):
        import ccxt
        logger.info("@EXCHANGE_INIT@ Подключение к REST API Bybit V5...")
        
        self.api_key = os.getenv('BYBIT_API_KEY', '')
        self.secret = os.getenv('BYBIT_API_SECRET', '')
        
        # Базовое подключение Bybit V5 REST API
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
        
        # Инициализация WebSocket-клиента через асинхронный движок
        self.ws_exchange = None
        self.ws_active = self._init_websocket_stream()

    def _init_websocket_stream(self) -> bool:
        """Автоматическая инициализация WebSocket-потока ccxt.pro"""
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
            logger.info("@WS_CONN@ Высокоскоростной WebSocket-шлюз Bybit V5 успешно подключен.")
            return True
        except (ImportError, Exception):
            logger.warning("@WS_WARN@ Библиотека ccxt.pro не найдена или недоступна. Включена REST-эмуляция кэша.")
            return False

    def load_markets(self):
        """Загрузка спецификаций торговых пар с биржи"""
        try:
            logger.info("@MARKETS_LOAD@ Загрузка спецификаций рынка...")
            if not self.api_key or not self.secret:
                logger.warning("@EXCHANGE_WARN@ API ключи не найдены. Активирован виртуальный контур.")
                return {}
            return self.exchange.load_markets()
        except Exception as e:
            logger.warning(f"@EXCHANGE_WARN@ Сбой сети Bybit: {e}. Переход в виртуальный контур.")
            return {}

    def clear_caches(self):
        """Очистка локального кэша для предотвращения утечек памяти"""
        try:
            self.exchange.clear_caches()
            if self.ws_active and self.ws_exchange:
                self.ws_exchange.clear_caches()
        except Exception as e:
            logger.error(f"@CACHE_ERROR@ Ошибка очистки кэша биржи: {e}")

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        """Округляет количество монеты под шаг лота и фильтры биржи Bybit"""
        try:
            return self.exchange.amount_to_precision(symbol, amount)
        except Exception as e:
            logger.error(f"@PRECISION_ERROR@ Ошибка amount_to_precision для {symbol}: {e}")
            return str(amount)

    def price_to_precision(self, symbol: str, price: float) -> str:
        """Округляет цену монеты под шаг стоимости (Tick Size) биржи Bybit"""
        try:
            return self.exchange.price_to_precision(symbol, price)
        except Exception as e:
            logger.error(f"@PRECISION_ERROR@ Ошибка price_to_precision для {symbol}: {e}")
            return str(price)

    def fetch_balance(self) -> dict:
        """Безопасный запрос баланса аккаунта со слоем симуляции"""
        try:
            if not self.api_key or not self.secret:
                return {'free': {'USDT': 1000.0}, 'total': {'USDT': 1000.0}}
            return self.exchange.fetch_balance()
        except Exception as e:
            logger.error(f"@EXCHANGE_ERROR@ Ошибка fetch_balance: {e}")
            return {'free': {'USDT': 1000.0}, 'total': {'USDT': 1000.0}}

    def fetch_tickers(self, symbols: List[str]) -> dict:
        """Запрос текущих цен по списку монет"""
        try:
            return self.exchange.fetch_tickers(symbols)
        except Exception as e:
            logger.error(f"@EXCHANGE_ERROR@ Ошибка fetch_tickers: {e}")
            return {}

    def fetch_ticker(self, symbol: str) -> dict:
        """Запрос цены по одной конкретной монете"""
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"@EXCHANGE_ERROR@ Ошибка fetch_ticker для {symbol}: {e}")
            return {'last': 0.0, 'ask': 0.0, 'bid': 0.0}

    def fetch_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 60) -> list:
        """Запрос исторических графиков (свечей)"""
        try:
            return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as e:
            logger.error(f"@EXCHANGE_ERROR@ Ошибка fetch_ohlcv для {symbol}: {e}")
            return []

    def fetch_closed_orders(self, symbol: str, limit: int = 5) -> list:
        """Запрос списка последних закрытых ордеров по паре"""
        try:
            return self.exchange.fetch_closed_orders(symbol, limit=limit)
        except Exception as e:
            logger.error(f"@EXCHANGE_ERROR@ Ошибка fetch_closed_orders для {symbol}: {e}")
            return []

    def create_limit_buy_order(self, symbol: str, amount: float, price: float) -> dict:
        """Создание лимитного ордера на ПОКУПКУ"""
        try:
            logger.info(f"@LIMIT_BUY@ Создание ордера BUY: {amount} {symbol} по ${price}")
            return self.exchange.create_limit_buy_order(symbol, amount, price)
        except Exception as e:
            logger.error(f"@ORDER_ERROR@ Ошибка создания BUY ордера для {symbol}: {e}")
            raise e

    def create_limit_sell_order(self, symbol: str, amount: float, price: float) -> dict:
        """Создание лимитного ордера на ПРОДАЖУ"""
        try:
            logger.info(f"@LIMIT_SELL@ Создание ордера SELL: {amount} {symbol} по ${price}")
            return self.exchange.create_limit_sell_order(symbol, amount, price)
        except Exception as e:
            logger.error(f"@ORDER_ERROR@ Ошибка создания SELL ордера для {symbol}: {e}")
            raise e

    def create_market_sell_order(self, symbol: str, amount: float) -> dict:
        """Создание рыночного ордера на ПРОДАЖУ (Паник-селл)"""
        try:
            logger.warning(f"@MARKET_SELL@ Экстренная продажа по рынку: {amount} {symbol}")
            return self.exchange.create_market_sell_order(symbol, amount)
        except Exception as e:
            logger.error(f"@ORDER_ERROR@ Ошибка рыночной продажи для {symbol}: {e}")
            raise e

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Отмена активного ордера на бирже"""
        try:
            logger.info(f"@CANCEL_ORDER@ Отмена ордера {order_id} для {symbol}")
            return self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            logger.error(f"@ORDER_ERROR@ Не удалось отменить ордер {order_id}: {e}")
            raise e

    def amend_order(self, order_id: str, symbol: str, amount: float, price: float) -> Optional[dict]:
        """Высокочастотный метод Bybit V5: изменение ордера на лету"""
        try:
            amt_str = self.exchange.amount_to_precision(symbol, amount)
            price_str = self.exchange.price_to_precision(symbol, price)
            logger.debug(f"@AMEND_ORDER@ Синхронизация ордера {order_id} -> {symbol} | Цена: {price_str}")
            
            modified_order = self.exchange.amend_order(
                id=order_id,
                symbol=symbol,
                type='limit',
                side='buy',
                amount=float(amt_str),
                price=float(price_str)
            )
            return modified_order
        except Exception as e:
            logger.error(f"@ORDER_ERROR@ Ошибка синхронизации ордера через amend_order: {e}")
            return None

    def fetch_order(self, order_id: str, symbol: str) -> dict:
        """Безопасный запрос статуса ордера с автоподстраховкой Ордер-Дожима"""
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
        
        logger.warning(f"@ORDER_FALLBACK@ Статус ордера {order_id} не получен, применен безопасный статус 'closed'")
        return {'id': order_id, 'status': 'closed', 'price': None, 'filled': 0.0}
