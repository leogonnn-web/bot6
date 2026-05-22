"""IDLE state handler."""
import time
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger

from ..state_enum import BotState


class IdleStateMixin:
    def _handle_idle_state(self):
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
            time.sleep(5)
            return

        logger.info("@IDLE@ All checks passed, transitioning to SCANNING")
        self.state = BotState.SCANNING
