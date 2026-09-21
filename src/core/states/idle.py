"""IDLE state handler."""
import time
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger

from ..state_enum import BotState


class IdleStateMixin:
    def _handle_idle_state(self):
        # Non-blocking re-check gate: sleeping here would stall the main loop
        # (and its heartbeat) for 5s, which the external watchdog reads as a
        # freeze. Instead we return immediately and retry after the deadline.
        now = time.time()
        if now < getattr(self, '_idle_next_check_ts', 0.0):
            return

        risk_ok = self._check_risk_limits()
        time_ok = self._check_time_session()
        balance_ok = self._check_balance()

        if not risk_ok:
            logger.info("@IDLE@ Risk limits check failed")
        if not time_ok:
            logger.info("@IDLE@ Time session check failed")
        if not balance_ok:
            logger.info("@IDLE@ Balance check failed")

        if not risk_ok or not time_ok or not balance_ok:
            self._idle_next_check_ts = now + 5
            return

        self._idle_next_check_ts = 0.0
        logger.info("@IDLE@ All checks passed, transitioning to SCANNING")
        self.state = BotState.SCANNING
