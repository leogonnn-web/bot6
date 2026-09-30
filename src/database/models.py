"""
HYDRA Database Models v17.0
SQLite database for trade logging and statistics
"""

import sqlite3
import time
from typing import Dict, List
import sys
import os

# Add shared to path for logger and paths
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared')))
from logger_setup import logger
from paths import TRADES_DB


class TradeDatabase:
    """SQLite database for trade logging and session statistics"""

    # Columns added to dispatcher_features after the original schema shipped.
    # Order matters only for readability; migration is idempotent.
    # TZ-12A: liquidity truth + the exit rules in force at signal time, so
    # offline labelling can replay the candidate without guessing. Measured once
    # in the scanner and carried through state_data to the linked rows.
    DF_CONTEXT_COLUMNS = (
        'spread_pct', 'bid_vol', 'ask_vol', 'turnover24h', 'bid_ask_source',
        'tp_pct', 'sl_pct', 'hold_sec', 'entry_ask', 'source',
    )
    DF_COLUMNS = ('profit', 'take_profit_pct') + DF_CONTEXT_COLUMNS
    DF_TEXT_COLUMNS = ('bid_ask_source', 'source')

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
                    take_profit_pct REAL,
                    spread_pct REAL,
                    bid_vol REAL,
                    ask_vol REAL,
                    turnover24h REAL,
                    bid_ask_source TEXT,
                    tp_pct REAL,
                    sl_pct REAL,
                    hold_sec REAL,
                    entry_ask REAL,
                    source TEXT
                )
            ''')
            # Migration: add columns missing on pre-existing tables. REAL unless
            # listed in DF_TEXT_COLUMNS (offline calibration inputs, TZ-12A).
            cursor.execute("PRAGMA table_info(dispatcher_features)")
            df_cols = [r[1] for r in cursor.fetchall()]
            for col in self.DF_COLUMNS:
                if col not in df_cols:
                    col_type = 'TEXT' if col in self.DF_TEXT_COLUMNS else 'REAL'
                    cursor.execute(f"ALTER TABLE dispatcher_features ADD COLUMN {col} {col_type}")
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
                                profit: float = None, take_profit_pct: float = None,
                                spread_pct: float = None, bid_vol: float = None,
                                ask_vol: float = None, turnover24h: float = None,
                                bid_ask_source: str = None, tp_pct: float = None,
                                sl_pct: float = None, hold_sec: float = None,
                                entry_ask: float = None, source: str = None):
        """Log dispatcher scoring features for post-trade analysis (feedback loop data).

        The TZ-12A parameters (spread_pct … source) default to None so older call
        sites keep working; None means "not measured", which the offline labeller
        (TZ-12B) treats as a reason to skip the row rather than as a zero.
        `spread_pct` must be left None unless bid/ask came from a real book
        (`bid_ask_source == 'book'`).
        """
        try:
            ts = time.time()
            cols = [
                'trade_id', 'timestamp', 'symbol', 'confidence', 'rvol_spike',
                'rvol_local', 'dump_depth', 'obi_skew', 'btc_1h', 'score', 'mode',
                'profit', 'take_profit_pct',
                'spread_pct', 'bid_vol', 'ask_vol', 'turnover24h', 'bid_ask_source',
                'tp_pct', 'sl_pct', 'hold_sec', 'entry_ask', 'source',
            ]
            vals = [
                trade_id, ts, symbol, confidence, rvol_spike,
                rvol_local, dump_depth, obi_skew, btc_1h, score, mode,
                profit, take_profit_pct,
                spread_pct, bid_vol, ask_vol, turnover24h, bid_ask_source,
                tp_pct, sl_pct, hold_sec, entry_ask, source,
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

    def get_session_stats(self, since_ts: float = 0) -> Dict:
        """
        Calculate session PnL and statistics from trades.db.

        Single source of truth: the `profit` column written by log_trade(), which
        is net of fees (see _calc_pnl). The previous FIFO re-derivation of
        `(price - buy_price) * matched` ignored that column and produced GROSS
        profit, so the risk capital lock (limits.py) systematically understated
        losses.

        Partial take-profits (`sell_partial`) contribute to session_profit but
        are NOT counted as trades — they are legs of one position, not separate
        deals. `total_trades` therefore counts only closing sells.

        since_ts > 0 restricts the calculation to trades on/after that epoch,
        used to scope the real-money session so prior dry-run PnL is excluded.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT side, profit FROM trades "
                "WHERE timestamp >= ? AND side LIKE 'sell%'",
                (since_ts,)
            )
            rows = cursor.fetchall()
            conn.close()

            closing_sides = ('sell', 'sell_panic')
            total_trades = 0
            winning_trades = 0
            session_profit = 0.0

            for side, profit in rows:
                profit = float(profit or 0.0)
                session_profit += profit
                if side in closing_sides:
                    total_trades += 1
                    if profit > 0:
                        winning_trades += 1

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

    def get_daily_trades_count(self, since_ts: float = 0) -> int:
        """
        Count completed trades made today (UTC date).

        Only closing sells (`sell`, `sell_panic`) are counted: `max_trades_per_day`
        limits round-trip deals, not individual legs. Counting every row made a
        single grid entry with partial exits burn several units of the daily
        limit at once.

        If since_ts > 0, count only trades on/after max(today_start, since_ts).
        Pass the real-session start (go-live epoch) so prior dry-run trades are
        excluded from the live daily limit.
        Used for daily trade limit enforcement.
        """
        try:
            from datetime import datetime, timezone
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_ts = int(today_start.timestamp())
            effective_start = max(today_start_ts, int(since_ts or 0))
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE timestamp >= ? AND side IN ('sell', 'sell_panic')",
                (effective_start,)
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
