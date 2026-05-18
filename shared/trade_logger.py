"""v16 trade DB wrapper (bot.py imports trade_logger.TradeDatabase)."""
from database import TradeDatabase as _TradeDatabase


class TradeDatabase(_TradeDatabase):
    def get_session_stats(self) -> dict:
        stats = super().get_session_stats()
        profit = stats.get("session_profit", 0.0)
        return {
            **stats,
            "total_profit": profit,
        }
