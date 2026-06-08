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
        self._conn: sqlite3.Connection | None = None
        self.setup_database()
        self._ensure_connection()
    
    def _ensure_connection(self):
        """Open persistent connection if not already open"""
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            except Exception as e:
                logger.error(f"Failed to open persistent DB connection: {e}")
    
    def health_check(self) -> bool:
        """Lightweight non-blocking read check using persistent connection"""
        try:
            self._ensure_connection()
            if self._conn is None:
                return False
            cursor = self._conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            return True
        except Exception as e:
            logger.debug(f"DB health check failed: {e}")
            self._conn = None
            return False
    
    def close(self):
        """Close persistent connection"""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
    
    def setup_database(self):
        """Initialize database schema with migrations"""
        try:
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
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
                    confidence REAL,
                    profit REAL DEFAULT 0.0
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS dispatcher_features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER,
                    timestamp REAL,
                    symbol TEXT,
                    confidence REAL,
                    rvol_spike REAL,
                    rvol_local REAL,
                    dump_depth REAL,
                    obi_skew REAL,
                    btc_1h REAL,
                    score REAL,
                    mode TEXT,
                    profit REAL,
                    take_profit_pct REAL
                )
            ''')
            # Migration: add profit / take_profit_pct if missing
            cursor.execute("PRAGMA table_info(dispatcher_features)")
            df_cols = [r[1] for r in cursor.fetchall()]
            for col in ('profit', 'take_profit_pct'):
                if col not in df_cols:
                    cursor.execute(f"ALTER TABLE dispatcher_features ADD COLUMN {col} REAL")
                    logger.info(f"DB MIGRATION: added '{col}' to dispatcher_features")
            # Migration: add profit column if missing on existing table
            cursor.execute("PRAGMA table_info(trades)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'profit' not in columns:
                cursor.execute('ALTER TABLE trades ADD COLUMN profit REAL DEFAULT 0.0')
                logger.info("DB MIGRATION: added 'profit' column to trades table")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error setting up database: {e}")
    
    def log_trade(self, symbol: str, side: str, amount: float, price: float, confidence: float = 0.0, profit: float = 0.0) -> int:
        """Log a trade to database using persistent connection. Returns the trade row id."""
        try:
            self._ensure_connection()
            if self._conn:
                cursor = self._conn.cursor()
                cursor.execute('''
                    INSERT INTO trades (symbol, side, amount, price, timestamp, confidence, profit)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (symbol, side, amount, price, time.time(), confidence, profit))
                self._conn.commit()
                trade_id = cursor.lastrowid
            else:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO trades (symbol, side, amount, price, timestamp, confidence, profit)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (symbol, side, amount, price, time.time(), confidence, profit))
                conn.commit()
                trade_id = cursor.lastrowid
                conn.close()
            logger.info(f"Trade logged: {side} {symbol} id={trade_id} profit=${profit:.2f}")
            return trade_id
        except Exception as e:
            logger.error(f"Error logging trade to database: {e}")
            return 0

    def log_dispatcher_features(self, trade_id: int, symbol: str, confidence: float,
                                rvol_spike: float, rvol_local: float, dump_depth: float,
                                obi_skew: float, btc_1h: float, score: float, mode: str,
                                profit: float = None, take_profit_pct: float = None):
        """Log dispatcher scoring features for post-trade analysis (feedback loop data)."""
        try:
            ts = time.time()
            cols = [
                'trade_id', 'timestamp', 'symbol', 'confidence', 'rvol_spike',
                'rvol_local', 'dump_depth', 'obi_skew', 'btc_1h', 'score', 'mode',
                'profit', 'take_profit_pct'
            ]
            vals = [
                trade_id, ts, symbol, confidence, rvol_spike,
                rvol_local, dump_depth, obi_skew, btc_1h, score, mode,
                profit, take_profit_pct
            ]
            ph = ','.join('?' for _ in vals)
            sql = f"INSERT INTO dispatcher_features ({','.join(cols)}) VALUES ({ph})"
            self._ensure_connection()
            if self._conn:
                cursor = self._conn.cursor()
                cursor.execute(sql, vals)
                self._conn.commit()
            else:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute(sql, vals)
                conn.commit()
                conn.close()
            logger.debug(f"@DISPATCHER_LOG@ Features logged for {symbol}")
        except Exception as e:
            logger.error(f"@DISPATCHER_LOG_WARN@ {e}")

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
                elif side == 'buy_grid_complete':
                    # Skip duplicate already tracked by 'buy' entry
                    continue
                elif side.startswith('sell') and open_buys[symbol]:
                    if side == 'sell_partial':
                        # Reduce open position without counting profit
                        buy_amount, buy_price = open_buys[symbol][0]
                        matched = min(amount, buy_amount)
                        remainder = buy_amount - matched
                        if remainder > 0:
                            open_buys[symbol][0] = (remainder, buy_price)
                        else:
                            open_buys[symbol].pop(0)
                        continue
                    
                    # sell / sell_panic: match FIFO and count profit
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
                'SELECT symbol, side, amount, price, timestamp, confidence, profit FROM trades ORDER BY timestamp DESC LIMIT ?',
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
                    'confidence': row[5],
                    'profit': row[6]
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Error getting recent trades: {e}")
            return []

    def get_daily_summary(self) -> List[Dict]:
        """Get daily PnL summary grouped by date and symbol"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    date(timestamp, 'unixepoch') as trade_date,
                    symbol,
                    COUNT(*) as trade_count,
                    SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) as losses,
                    ROUND(SUM(profit), 2) as daily_profit
                FROM trades
                WHERE side LIKE 'sell%'
                GROUP BY date(timestamp, 'unixepoch'), symbol
                ORDER BY trade_date DESC, daily_profit DESC
            ''')
            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    'date': row[0],
                    'symbol': row[1],
                    'trades': row[2],
                    'wins': row[3],
                    'losses': row[4],
                    'profit': row[5]
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Error getting daily summary: {e}")
            return []
