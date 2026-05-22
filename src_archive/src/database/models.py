"""
HYDRA Database Models v17.0
SQLite database for trade logging and statistics
"""

import sqlite3
import time
from collections import defaultdict
from typing import Dict, List, Tuple
import sys
import os

# Add shared to path for logger and paths
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared')))
from logger_setup import logger
from paths import TRADES_DB


class TradeDatabase:
    """SQLite database for trade logging and session statistics"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or TRADES_DB
        self.setup_database()
    
    def setup_database(self):
        """Initialize database schema"""
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
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error setting up database: {e}")
    
    def log_trade(self, symbol: str, side: str, amount: float, price: float, confidence: float = 0.0):
        """Log a trade to database"""
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
    
    def get_session_stats(self) -> Dict:
        """
        Calculate session PnL and statistics from trades.db
        Uses FIFO matching for buy/sell pairs per symbol
        """
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

    def get_daily_trades_count(self) -> int:
        """
        Count trades made today (UTC date)
        Used for daily trade limit enforcement
        """
        try:
            from datetime import datetime, timezone
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_ts = int(today_start.timestamp())
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COUNT(*) FROM trades WHERE timestamp >= ?',
                (today_start_ts,)
            )
            count = cursor.fetchone()[0]
            conn.close()
            
            return count
        except Exception as e:
            logger.error(f"Error reading daily trades count: {e}")
            return 0
    
    def get_recent_trades(self, limit: int = 10) -> List[Dict]:
        """Get recent trades from database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                'SELECT symbol, side, amount, price, timestamp, confidence FROM trades ORDER BY timestamp DESC LIMIT ?',
                (limit,)
            )
            rows = cursor.fetchall()
            conn.close()
            
            return [
                {
                    'symbol': row[0],
                    'side': row[1],
                    'amount': row[2],
                    'price': row[3],
                    'timestamp': row[4],
                    'confidence': row[5]
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Error getting recent trades: {e}")
            return []
