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
    bot = TradingBot()
    bot.run()


if __name__ == "__main__":
    main()
