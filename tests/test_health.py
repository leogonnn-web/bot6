"""pytest suite for HealthChecker — mimics Bybit network timeout scenarios"""
import time
import signal
import pytest
from unittest.mock import MagicMock, patch

from core.health import HealthChecker


class DummyBot:
    """Minimal bot mock for health tests"""
    def __init__(self):
        self.ws_tickers_cache = {}
        self.last_rest_poll_time = time.time()
        self.state = 'IDLE'
        self.state_entry_time = time.time()
        self.balance = 1000.0
        self.config = MagicMock()
        self.config.config = {'trading': {'dry_run': True}}
        self.trade_db = MagicMock()
        self.trade_db.health_check = MagicMock(return_value=True)


def test_exchange_api_passive_ok():
    bot = DummyBot()
    hc = HealthChecker(bot)
    assert hc._check_exchange_api() is True


def test_exchange_api_passive_stale():
    bot = DummyBot()
    bot.last_rest_poll_time = time.time() - 120  # 2 minutes stale
    hc = HealthChecker(bot)
    assert hc._check_exchange_api() is False


def test_tickers_95pct_fresh():
    bot = DummyBot()
    now = time.time()
    bot.ws_tickers_cache = {
        'BTC/USDT': {'timestamp': now},
        'ETH/USDT': {'timestamp': now - 2},
        'SOL/USDT': {'timestamp': now - 5},
        'TON/USDT': {'timestamp': now - 1},
        'NOT/USDT': {'timestamp': now},
    }
    hc = HealthChecker(bot)
    assert hc._check_tickers_cache() is True


def test_tickers_below_95pct_fresh():
    bot = DummyBot()
    now = time.time()
    # 20 symbols, only 18 fresh (90% < 95%)
    bot.ws_tickers_cache = {
        'BTC/USDT': {'timestamp': now},
        'ETH/USDT': {'timestamp': now - 2},
        'SOL/USDT': {'timestamp': now - 15},  # stale (>10s)
        'TON/USDT': {'timestamp': now - 1},
        'NOT/USDT': {'timestamp': now - 20},  # stale (>10s)
    }
    hc = HealthChecker(bot)
    assert hc._check_tickers_cache() is False


def test_database_persistent_check():
    bot = DummyBot()
    bot.trade_db.health_check.return_value = True
    hc = HealthChecker(bot)
    assert hc._check_database() is True
    bot.trade_db.health_check.assert_called_once()


def test_database_persistent_failure():
    bot = DummyBot()
    bot.trade_db.health_check.return_value = False
    hc = HealthChecker(bot)
    assert hc._check_database() is False


def test_state_stuck_buying():
    bot = DummyBot()
    bot.state = 'BUYING'
    bot.state_entry_time = time.time() - 400  # 400s > 300s limit
    hc = HealthChecker(bot)
    assert hc._check_state_stuck() is False


def test_state_not_stuck_scanning():
    bot = DummyBot()
    bot.state = 'SCANNING'
    bot.state_entry_time = time.time() - 400
    hc = HealthChecker(bot)
    assert hc._check_state_stuck() is True


def test_overall_healthy():
    bot = DummyBot()
    now = time.time()
    bot.ws_tickers_cache = {
        'BTC/USDT': {'timestamp': now},
        'ETH/USDT': {'timestamp': now},
    }
    hc = HealthChecker(bot)
    report = hc.check()
    assert report['overall'] is True
    assert hc.consecutive_fails == 0


def test_overall_degraded_counts_fails():
    bot = DummyBot()
    now = time.time()
    bot.last_rest_poll_time = now - 120  # stale exchange
    bot.ws_tickers_cache = {
        'BTC/USDT': {'timestamp': now},
        'ETH/USDT': {'timestamp': now},
    }
    hc = HealthChecker(bot)
    report = hc.check()
    assert report['overall'] is False
    assert hc.consecutive_fails == 1


def test_bybit_timeout_scenario():
    """Mimic Bybit REST timeout: stale exchange + stale tickers."""
    bot = DummyBot()
    now = time.time()
    # No REST poll for 90 seconds (API timeout)
    bot.last_rest_poll_time = now - 90
    # Only 50% tickers fresh (below 95% threshold)
    bot.ws_tickers_cache = {
        'BTC/USDT': {'timestamp': now - 15},
        'ETH/USDT': {'timestamp': now - 2},
    }
    hc = HealthChecker(bot)
    report = hc.check()
    assert report['checks']['exchange_api'] is False
    assert report['checks']['tickers_cache'] is False
    assert report['overall'] is False


def test_watchdog_sigterm_on_3_consecutive():
    """Verify SIGTERM is sent after 3 consecutive failures."""
    bot = DummyBot()
    bot.last_rest_poll_time = time.time() - 120

    hc = HealthChecker(bot)
    hc.max_consecutive_fails = 2
    hc.check_interval = 0  # disable caching for rapid test

    with patch('os.kill') as mock_kill:
        hc.check()  # fail 1
        assert mock_kill.call_count == 0
        hc.check()  # fail 2
        assert mock_kill.call_count == 1
        # Verify SIGTERM signal
        args, _ = mock_kill.call_args
        assert args[1] == signal.SIGTERM
