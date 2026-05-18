"""
HYDRA Trading Bot v17.0 - Main Entry Point
Production-ready trading bot with modular architecture
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from core.bot import TradingBot


def main():
    """Main entry point"""
    # Read tank_mode from config.json if env var not set
    tank_mode_env = os.getenv('TANK_MODE', '').lower()
    if tank_mode_env == 'true':
        tank_mode = True
    elif tank_mode_env == 'false':
        tank_mode = False
    else:
        # Fallback to config.json
        try:
            sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'shared')))
            from config import config
            trading_config = config.get_trading_config()
            tank_mode = trading_config.get('tank_mode', False)
        except:
            tank_mode = False
    
    bot = TradingBot(tank_mode=tank_mode)
    bot.run()


if __name__ == "__main__":
    main()
