import sqlite3
import time
from collections import defaultdict
from typing import Dict, List, Tuple

from logger_setup import logger
from paths import TRADES_DB


class TradeDatabase:
    def __init__(self):
        self.db_path = TRADES_DB
        self.setup_database()

    def setup_database(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    side TEXT,
                    amount REAL,
                    price REAL,
                    timestamp REAL,
                    confidence REAL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS dispatcher_features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER REFERENCES trades(id),
                    timestamp REAL,
                    symbol TEXT,
                    confidence REAL,
                    rvol_spike REAL,
                    rvol_local REAL,
                    dump_depth REAL,
                    obi_skew REAL,
                    btc_1h REAL,
                    score REAL,
                    mode TEXT
                )
            ''')
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error setting up database: {e}")

    def log_trade(self, symbol, side, amount, price, confidence=0.0):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trades (symbol, side, amount, price, timestamp, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (symbol, side, amount, price, time.time(), confidence))
            conn.commit()
            conn.close()
            logger.info(f"Trade logged: {side} {symbol}")
        except Exception as e:
            logger.error(f"Error logging trade to database: {e}")

    def log_dispatcher_features(
        self,
        trade_id: int,
        symbol: str,
        confidence: float,
        rvol_spike: float,
        rvol_local: float,
        dump_depth: float,
        obi_skew: float,
        btc_1h: float,
        score: float,
        mode: str,
    ):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO dispatcher_features
                   (trade_id, timestamp, symbol, confidence, rvol_spike,
                    rvol_local, dump_depth, obi_skew, btc_1h, score, mode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (trade_id, time.time(), symbol, confidence, rvol_spike,
                 rvol_local, dump_depth, obi_skew, btc_1h, score, mode),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error logging dispatcher features: {e}")

    def get_session_stats(self) -> dict:
        """Session PnL and trade counts from trades.db (FIFO buy/sell per symbol)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                'SELECT symbol, side, amount, price FROM trades ORDER BY timestamp ASC'
            )
            rows = cursor.fetchall()
            conn.close()

            open_buys: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
            total_trades = 0
            winning_trades = 0
            session_profit = 0.0

            for symbol, side, amount, price in rows:
                amount = float(amount)
                price = float(price)
                if side == 'buy':
                    open_buys[symbol].append((amount, price))
                elif side == 'sell' and open_buys[symbol]:
                    buy_amount, buy_price = open_buys[symbol].pop(0)
                    matched = min(amount, buy_amount)
                    profit = (price - buy_price) * matched
                    session_profit += profit
                    total_trades += 1
                    if profit > 0:
                        winning_trades += 1
                    remainder = buy_amount - matched
                    if remainder > 0:
                        open_buys[symbol].insert(0, (remainder, buy_price))

            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
            return {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "session_profit": session_profit,
                "total_profit": session_profit,
                "win_rate": win_rate,
            }
        except Exception as e:
            logger.error(f"Error reading session stats: {e}")
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "session_profit": 0.0,
                "total_profit": 0.0,
                "win_rate": 0.0,
            }
