"""
HYDRA Performance Tracker v17.0 - Performance Metrics Collection
Tracks win rate, avg profit/loss, trade duration, symbol performance
"""

import time
from typing import Dict, List, Optional
from collections import defaultdict
import sys
import os

# Add shared to path for logger
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared')))
from logger_setup import logger


class PerformanceTracker:
    """Track and analyze trading performance metrics"""
    
    def __init__(self):
        self.trades = []
        self.symbol_stats = defaultdict(lambda: {
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'total_profit': 0.0,
            'total_loss': 0.0,
            'avg_profit': 0.0,
            'avg_loss': 0.0,
            'win_rate': 0.0
        })
        self.session_start_time = time.time()
    
    def log_trade(self, symbol: str, entry_price: float, exit_price: float, 
                  amount: float, entry_time: float, exit_time: float, 
                  trade_type: str = "normal"):
        """Log a completed trade for performance tracking"""
        try:
            profit = (exit_price - entry_price) * amount
            profit_pct = ((exit_price - entry_price) / entry_price) * 100
            duration = exit_time - entry_time
            
            trade_record = {
                'symbol': symbol,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'amount': amount,
                'profit': profit,
                'profit_pct': profit_pct,
                'duration': duration,
                'entry_time': entry_time,
                'exit_time': exit_time,
                'trade_type': trade_type,
                'is_win': profit > 0
            }
            
            self.trades.append(trade_record)
            
            # Update symbol stats
            stats = self.symbol_stats[symbol]
            stats['total_trades'] += 1
            
            if profit > 0:
                stats['wins'] += 1
                stats['total_profit'] += profit
            else:
                stats['losses'] += 1
                stats['total_loss'] += abs(profit)
            
            # Calculate averages
            if stats['wins'] > 0:
                stats['avg_profit'] = stats['total_profit'] / stats['wins']
            if stats['losses'] > 0:
                stats['avg_loss'] = stats['total_loss'] / stats['losses']
            stats['win_rate'] = (stats['wins'] / stats['total_trades']) * 100
            
            logger.debug(f"@PERF_TRACK@ Trade logged: {symbol} | Profit: ${profit:.2f} ({profit_pct:.2f}%) | Duration: {duration:.0f}s")
            
        except Exception as e:
            logger.error(f"Performance tracker error: {e}")
    
    def get_session_stats(self) -> Dict:
        """Get overall session performance statistics"""
        try:
            if not self.trades:
                return {
                    'total_trades': 0,
                    'wins': 0,
                    'losses': 0,
                    'win_rate': 0.0,
                    'total_profit': 0.0,
                    'total_loss': 0.0,
                    'net_profit': 0.0,
                    'avg_profit_per_trade': 0.0,
                    'avg_duration': 0.0,
                    'session_duration': 0.0
                }
            
            wins = sum(1 for t in self.trades if t['is_win'])
            losses = len(self.trades) - wins
            total_profit = sum(t['profit'] for t in self.trades if t['is_win'])
            total_loss = sum(abs(t['profit']) for t in self.trades if not t['is_win'])
            net_profit = total_profit - total_loss
            avg_duration = sum(t['duration'] for t in self.trades) / len(self.trades)
            session_duration = time.time() - self.session_start_time
            
            return {
                'total_trades': len(self.trades),
                'wins': wins,
                'losses': losses,
                'win_rate': (wins / len(self.trades)) * 100,
                'total_profit': total_profit,
                'total_loss': total_loss,
                'net_profit': net_profit,
                'avg_profit_per_trade': net_profit / len(self.trades),
                'avg_duration': avg_duration,
                'session_duration': session_duration
            }
        except Exception as e:
            logger.error(f"Session stats error: {e}")
            return {}
    
    def get_symbol_performance(self, symbol: str) -> Dict:
        """Get performance statistics for a specific symbol"""
        try:
            if symbol not in self.symbol_stats:
                return {}
            
            stats = self.symbol_stats[symbol]
            symbol_trades = [t for t in self.trades if t['symbol'] == symbol]
            
            if not symbol_trades:
                return stats
            
            avg_duration = sum(t['duration'] for t in symbol_trades) / len(symbol_trades)
            stats['avg_duration'] = avg_duration
            
            return stats
        except Exception as e:
            logger.error(f"Symbol performance error: {e}")
            return {}
    
    def get_top_performers(self, top_n: int = 5) -> List[Dict]:
        """Get top performing symbols by win rate"""
        try:
            sorted_symbols = sorted(
                self.symbol_stats.items(),
                key=lambda x: x[1]['win_rate'],
                reverse=True
            )
            
            return [
                {
                    'symbol': symbol,
                    **stats
                }
                for symbol, stats in sorted_symbols[:top_n]
            ]
        except Exception as e:
            logger.error(f"Top performers error: {e}")
            return []
    
    def get_worst_performers(self, bottom_n: int = 5) -> List[Dict]:
        """Get worst performing symbols by win rate"""
        try:
            sorted_symbols = sorted(
                self.symbol_stats.items(),
                key=lambda x: x[1]['win_rate']
            )
            
            return [
                {
                    'symbol': symbol,
                    **stats
                }
                for symbol, stats in sorted_symbols[:bottom_n]
            ]
        except Exception as e:
            logger.error(f"Worst performers error: {e}")
            return []
    
    def print_summary(self):
        """Print a summary of performance metrics"""
        try:
            stats = self.get_session_stats()
            
            logger.info("=" * 60)
            logger.info("PERFORMANCE SUMMARY")
            logger.info("=" * 60)
            logger.info(f"Total Trades: {stats['total_trades']}")
            logger.info(f"Wins: {stats['wins']} | Losses: {stats['losses']}")
            logger.info(f"Win Rate: {stats['win_rate']:.1f}%")
            logger.info(f"Total Profit: ${stats['total_profit']:.2f}")
            logger.info(f"Total Loss: ${stats['total_loss']:.2f}")
            logger.info(f"Net Profit: ${stats['net_profit']:.2f}")
            logger.info(f"Avg Profit/Trade: ${stats['avg_profit_per_trade']:.2f}")
            logger.info(f"Avg Duration: {stats['avg_duration']:.0f}s")
            logger.info(f"Session Duration: {stats['session_duration'] / 60:.1f}min")
            logger.info("=" * 60)
            
            # Top performers
            top = self.get_top_performers(3)
            if top:
                logger.info("TOP PERFORMERS:")
                for perf in top:
                    logger.info(f"  {perf['symbol']}: {perf['win_rate']:.1f}% win rate ({perf['total_trades']} trades)")
            
            # Worst performers
            worst = self.get_worst_performers(3)
            if worst:
                logger.info("WORST PERFORMERS:")
                for perf in worst:
                    logger.info(f"  {perf['symbol']}: {perf['win_rate']:.1f}% win rate ({perf['total_trades']} trades)")
            
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"Print summary error: {e}")
    
    def reset(self):
        """Reset all performance data"""
        self.trades = []
        self.symbol_stats = defaultdict(lambda: {
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'total_profit': 0.0,
            'total_loss': 0.0,
            'avg_profit': 0.0,
            'avg_loss': 0.0,
            'win_rate': 0.0
        })
        self.session_start_time = time.time()
        logger.info("@PERF_RESET@ Performance tracker reset")
