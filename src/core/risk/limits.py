"""Risk limits checks (daily trades, cooldown after loss)."""
import time
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger

# Optional import: config validation errors must NOT be swallowed by the
# generic Exception handler below. If pydantic / config_models is missing,
# `_ConfigValidationError` falls back to a sentinel that won't match anything.
try:
    from config_models import ConfigValidationError as _ConfigValidationError  # type: ignore
except Exception:  # pragma: no cover
    class _ConfigValidationError(Exception):  # type: ignore
        """Sentinel — never raised when pydantic is unavailable."""


class RiskLimitsMixin:
    def _check_risk_limits(self) -> bool:
        try:
            # Force reload config from disk to pick up changes without restart
            self.config.config = self.config._load_config()
            trading_config = self.config.get_trading_config()

            # Log current config values for debugging
            logger.debug(f"@CONFIG_DEBUG@ max_trades_per_day: {trading_config.get('max_trades_per_day', 5)}")

            cooldown_min = trading_config.get('cooldown_after_loss_minutes', 30)
            if time.time() - self.last_loss_time < (cooldown_min * 60):
                remaining = int((cooldown_min * 60) - (time.time() - self.last_loss_time))
                print(f"Cooldown after loss. Remaining: {remaining}s @COOLDOWN@ ", end='\r')
                logger.info(f"@RISK_COOLDOWN@ Cooldown active: {remaining}s remaining")
                return False
            daily_trades = self.trade_db.get_daily_trades_count()
            max_day_trades = trading_config.get('max_trades_per_day', 5)
            logger.info(f"@RISK_CHECK@ Daily trades: {daily_trades}/{max_day_trades}")
            if daily_trades >= max_day_trades:
                print(f"Daily trade limit reached ({daily_trades}/{max_day_trades}) @DAY_LIMIT@ ", end='\r')
                logger.info(f"@RISK_LIMIT@ Daily limit reached: {daily_trades}/{max_day_trades}")
                return False
            # ── Capital protection: max loss limit ──
            try:
                stop_loss_pct = trading_config.get('stop_loss_total', 12.0)
                stats = self.trade_db.get_session_stats()
                realized_pnl = float(stats.get('session_profit', 0.0))
                # Get balance (dry_run uses virtual $1000)
                if trading_config.get('dry_run', False):
                    balance_usdt = 1000.0
                else:
                    bal = self.exchange.fetch_balance()
                    free_section = bal.get('free') if isinstance(bal, dict) else None
                    balance_usdt = float(free_section.get('USDT', 0)) if isinstance(free_section, dict) else 0.0
                max_loss_usdt = balance_usdt * (stop_loss_pct / 100.0)
                if realized_pnl < -max_loss_usdt:
                    logger.critical(
                        f"@RISK_CAPITAL_LOCK@ Realized PnL ${realized_pnl:.2f} exceeds max loss "
                        f"-${max_loss_usdt:.2f} ({stop_loss_pct}% of ${balance_usdt:.0f}). "
                        f"BLOCKING all new entries."
                    )
                    return False
                logger.info(
                    f"@RISK_PNL@ Realized PnL: ${realized_pnl:.2f}, max allowed loss: -${max_loss_usdt:.2f}"
                )
            except Exception as pnl_err:
                logger.error(f"@RISK_PNL_ERROR@ Capital protection check failed: {pnl_err}")

            # ── Circuit-breaker: halt entries after a cluster of panic exits ──
            if self._circuit_breaker_blocks():
                return False

            logger.info(f"@RISK_OK@ Risk limits passed")
            return True
        except _ConfigValidationError:
            # Hot-reload picked up an invalid config edit. Propagate so the
            # main run loop can decide to abort rather than silently
            # continuing with a stale/partial config.
            logger.critical(
                "@CONFIG_VALIDATION_ERROR@ runtime config reload failed; "
                "aborting risk check and propagating."
            )
            raise
        except Exception as e:
            logger.error(f"@RISK_ERROR@ Risk limits check error: {e}")
            return True
