import sys, os
sys.path.append('/app/shared')
from database.models import TradeDatabase
db = TradeDatabase()
stats = db.get_session_stats()
print("session_profit:", stats['session_profit'])
print("total_trades:", stats['total_trades'])
print("winning_trades:", stats['winning_trades'])
print("win_rate:", stats['win_rate'])
