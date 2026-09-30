"""
HYDRA Trading Bot v17.0 - Main Entry Point
Production-ready trading bot with modular architecture
"""

import sys
import os

# Add shared/ first, THEN src/ — src/ must win for ambiguous names like
# `database` (we have `shared/database.py` AND `src/database/` package; the
# bot wants the package). insert(0, ...) puts the latest call at index 0.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'shared')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))


def main() -> int:
    """Main entry point.

    Returns process exit code (0 = ok, 1 = config error, 2 = live guard).
    """
    # ------------------------------------------------------------------
    # Step 1: load + validate config FIRST. We do this before importing
    # TradingBot so that a config error fails fast with a clean message
    # instead of being masked by deeper imports.
    # ------------------------------------------------------------------
    # Import the exception class FIRST so it is bound for the except clause
    # below; the validation itself is triggered by `from config import config`.
    try:
        from config_models import ConfigValidationError
    except ImportError:
        ConfigValidationError = None  # type: ignore[assignment]

    try:
        from config import config  # triggers Config() + Pydantic validation
    except Exception as ve:
        # Match ConfigValidationError when available, otherwise re-raise.
        if ConfigValidationError is not None and isinstance(ve, ConfigValidationError):
            print(f"[FATAL] @CONFIG_VALIDATION_ERROR@ {ve}", file=sys.stderr)
            return 1
        raise

    # ------------------------------------------------------------------
    # Step 2: live guard. Real money requires an explicit env confirmation.
    # Checked BEFORE importing core.bot so a misconfigured deploy cannot even
    # construct the bot. Three legal modes:
    #   dry_run=true                                  → virtual orders
    #   dry_run=false, demo_trading=true              → live code, demo exchange
    #   dry_run=false, demo_trading=false + CONFIRM   → real money
    # ------------------------------------------------------------------
    dry_run = bool(config.get_trading_config().get('dry_run', True))
    demo_trading = bool(config.config.get('exchange', {}).get('demo_trading', False))
    if not dry_run and not demo_trading and os.getenv('HYDRA_LIVE_CONFIRM') != 'yes':
        print("[FATAL] @LIVE_GUARD@ real trading requires HYDRA_LIVE_CONFIRM=yes")
        return 2

    # ------------------------------------------------------------------
    # Step 3: start the bot
    # ------------------------------------------------------------------
    from core.bot import TradingBot
    bot = TradingBot()
    bot.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
