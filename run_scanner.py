"""
HYDRA Scanner v17.0 - Main Entry Point
Market scanner for hot symbols
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from core.scanner import MarketScanner


def main():
    """Main entry point"""
    scanner = MarketScanner(output_file="hot_symbols.txt")
    scanner.run()


if __name__ == "__main__":
    main()
