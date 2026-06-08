"""Project paths for shared modules (repo root = parent of shared/)."""
import os

SHARED_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SHARED_DIR)

HOT_SYMBOLS_FILE = os.path.join(PROJECT_ROOT, "hot_symbols.txt")
TRADES_DB = os.path.join(PROJECT_ROOT, "shared", "state", "trades.db")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
DEFAULT_CONFIG = os.path.join(SHARED_DIR, "config.json")
V17_CONFIG = os.path.join(PROJECT_ROOT, "v17", "config_v17.json")
SESSION_PROFIT_FILE = os.path.join(PROJECT_ROOT, "session_profit.json")
