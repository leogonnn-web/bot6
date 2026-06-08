from database.models import TradeDatabase
db = TradeDatabase()
s = db.get_session_stats()
print("session_profit:", s["session_profit"])
print("total_trades:", s["total_trades"])
print("winning_trades:", s["winning_trades"])
print("win_rate:", s["win_rate"])
