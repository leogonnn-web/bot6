"""Self-diagnostic health checker for HYDRA bot — non-blocking, async-safe"""
import time
import os
import signal
import logging
from typing import Dict, List
# IMPORTANT: must match the import path used by all other modules
# (`from metrics import METRICS`). Mixing `metrics` and `shared.metrics`
# yields TWO distinct module objects, each with its own _Metrics() singleton
# that wipes the other's collectors from REGISTRY on import — which silently
# disables every metric except those owned by whichever import ran last.
from metrics import METRICS

logger = logging.getLogger('HYDRA')


class HealthChecker:
    """Non-blocking health checks. Never performs active network I/O."""

    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.last_check = 0
        self.check_interval = 30  # seconds
        self.consecutive_fails = 0
        self.max_consecutive_fails = 3
        self.health_history: List[Dict] = []
        self.max_history = 10

    def check(self) -> Dict:
        """Run all health checks and return report"""
        now = time.time()
        if now - self.last_check < self.check_interval:
            return self._last_report()

        report = {
            'timestamp': now,
            'overall': True,
            'checks': {}
        }

        # 1. Exchange API status — PASSIVE (no active network I/O)
        report['checks']['exchange_api'] = self._check_exchange_api()

        # 2. Tickers cache — >=95% fresh within 10 seconds
        report['checks']['tickers_cache'] = self._check_tickers_cache()

        # 3. Database — persistent connection, lightweight SELECT 1
        report['checks']['database'] = self._check_database()

        # 4. Bot state not stuck
        report['checks']['state_stuck'] = self._check_state_stuck()

        # 5. Capital available
        report['checks']['capital'] = self._check_capital()

        # 6. No critical errors looping
        report['checks']['error_loop'] = self._check_error_loop()

        report['overall'] = all(report['checks'].values())

        if not report['overall']:
            self.consecutive_fails += 1
            failed = [k for k, v in report['checks'].items() if not v]
            logger.warning(f"@HEALTH_FAIL@ Failed: {failed} (consecutive: {self.consecutive_fails})")

            if self.consecutive_fails >= self.max_consecutive_fails:
                logger.critical(
                    f"@HEALTH_CRITICAL@ {self.max_consecutive_fails} consecutive failures! "
                    f"Triggering SIGTERM for Docker restart."
                )
                self._trigger_watchdog_restart()
        else:
            if self.consecutive_fails > 0:
                logger.info(f"@HEALTH_OK@ Restored after {self.consecutive_fails} failures")
            self.consecutive_fails = 0

        # Hysteresis: hold the Prometheus gauge at 1 on a single transient
        # failure to avoid waking up the external watchdog on a single missed
        # tick. Only flip to 0 once we have at least 2 failures in a row,
        # which then matches the watchdog's stall window (>=15s).
        gauge_val = 1.0 if (report['overall'] or self.consecutive_fails < 2) else 0.0
        METRICS.health_status.set(gauge_val)

        self.last_check = now
        self.health_history.append(report)
        if len(self.health_history) > self.max_history:
            self.health_history.pop(0)

        return report

    # ------------------------------------------------------------------
    # 1. PASSIVE exchange check — no active network I/O
    # ------------------------------------------------------------------
    def _check_exchange_api(self) -> bool:
        """Check last successful REST/WS timestamp passively."""
        last_ping = getattr(self.bot, 'last_rest_poll_time', 0)
        elapsed = time.time() - last_ping
        ok = elapsed < 60.0
        if not ok:
            logger.debug(f"Health: exchange_api stale ({elapsed:.0f}s > 60s)")
        return ok

    # ------------------------------------------------------------------
    # 2. HARDENED tickers freshness — >=95% within 10 seconds
    # ------------------------------------------------------------------
    def _check_tickers_cache(self) -> bool:
        cache = getattr(self.bot, 'ws_tickers_cache', {})
        if not cache:
            return False
        now = time.time()
        fresh_count = sum(1 for v in cache.values() if now - v.get('timestamp', 0) <= 10)
        ratio = fresh_count / len(cache)
        ok = ratio >= 0.95
        if not ok:
            logger.debug(f"Health: tickers fresh {ratio*100:.0f}% (< 95% within 10s)")
        return ok

    # ------------------------------------------------------------------
    # 3. PERSISTENT SQLite connection — lightweight read-only probe
    # ------------------------------------------------------------------
    def _check_database(self) -> bool:
        try:
            return self.bot.trade_db.health_check()
        except Exception as e:
            logger.debug(f"Health: DB fail: {e}")
            return False

    # ------------------------------------------------------------------
    # 4. State stuck detection
    # ------------------------------------------------------------------
    def _check_state_stuck(self) -> bool:
        state = getattr(self.bot, 'state', None)
        state_entry = getattr(self.bot, 'state_entry_time', 0)
        if state in ['IN_POSITION', 'BUYING', 'EXITING']:
            elapsed = time.time() - state_entry
            max_time = {'BUYING': 300, 'IN_POSITION': 3600, 'EXITING': 300}.get(state, 600)
            if elapsed > max_time:
                logger.debug(f"Health: state {state} stuck for {elapsed:.0f}s")
                return False
        return True

    # ------------------------------------------------------------------
    # 5. Capital check
    # ------------------------------------------------------------------
    def _check_capital(self) -> bool:
        trading_config = getattr(self.bot.config, 'config', {})
        if trading_config.get('trading', {}).get('dry_run', False):
            return True
        return getattr(self.bot, 'balance', 0) > 0

    # ------------------------------------------------------------------
    # 6. Error loop detection
    # ------------------------------------------------------------------
    def _check_error_loop(self) -> bool:
        return self.consecutive_fails < self.max_consecutive_fails

    # ------------------------------------------------------------------
    # Watchdog: SIGTERM → Docker restart: unless-stopped
    # ------------------------------------------------------------------
    def _trigger_watchdog_restart(self):
        """Force process exit so Docker policy triggers container restart."""
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception as e:
            logger.error(f"Watchdog SIGTERM failed: {e}, falling back to sys.exit")
            import sys
            sys.exit(1)

    def _last_report(self) -> Dict:
        if self.health_history:
            return self.health_history[-1]
        return {'timestamp': 0, 'overall': True, 'checks': {}}

    def get_summary(self) -> str:
        if not self.health_history:
            return "No health checks yet"
        latest = self.health_history[-1]
        status = "HEALTHY" if latest['overall'] else "DEGRADED"
        lines = [f"Status: {status} (fails: {self.consecutive_fails})"]
        for check, ok in latest['checks'].items():
            lines.append(f"  {check}: {'OK' if ok else 'FAIL'}")
        return "\n".join(lines)
