"""pytest suite for HealthChecker — mimics Bybit network timeout scenarios"""
import time
import pytest
from unittest.mock import MagicMock, patch

from core.health import HealthChecker
from core.state_enum import BotState


class DummyBot:
    """Minimal bot mock for health tests"""
    def __init__(self):
        self.ws_tickers_cache = {}
        self.last_rest_poll_time = time.time()
        self.state = BotState.IDLE
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
    bot.state = BotState.BUYING
    bot.state_entry_time = time.time() - 2200  # > hard_exit 1800 + 300
    hc = HealthChecker(bot)
    assert hc._check_state_stuck() is False


def test_state_not_stuck_scanning():
    bot = DummyBot()
    bot.state = BotState.SCANNING
    bot.state_entry_time = time.time() - 400
    hc = HealthChecker(bot)
    assert hc._check_state_stuck() is True


def test_state_stuck_buying_just_over_limit():
    """Enum comparison must actually fire: limit comes from hard_exit_timeout_sec."""
    bot = DummyBot()
    bot.config.config = {'trading': {'dry_run': True, 'hard_exit_timeout_sec': 600}}
    bot.state = BotState.BUYING
    bot.state_entry_time = time.time() - 901   # > 600 + 300
    hc = HealthChecker(bot)
    assert hc._check_state_stuck() is False
    bot.state_entry_time = time.time() - 899
    assert hc._check_state_stuck() is True


# ---- state_stuck is informational: never degrades overall health (stage-1 stop,
# ---- 2026-10-11: an open grid in BUYING > 300s made the watchdog restart the bot
# ---- every ~7 min; exit_halted in EXITING would loop the same way).
def _healthy_bot():
    bot = DummyBot()
    now = time.time()
    bot.ws_tickers_cache = {'BTC/USDT': {'timestamp': now}, 'ETH/USDT': {'timestamp': now}}
    bot.state_data = {}
    return bot


def test_open_grid_within_hard_exit_is_healthy():
    bot = _healthy_bot()
    bot.config.config = {'trading': {'dry_run': True, 'hard_exit_timeout_sec': 1800}}
    bot.state = BotState.BUYING
    bot.state_entry_time = time.time() - 1000
    hc = HealthChecker(bot)
    report = hc.check()
    assert report['checks']['state_stuck'] is True
    assert report['overall'] is True


def test_long_buying_logs_state_long_but_stays_healthy(caplog):
    bot = _healthy_bot()
    bot.state = BotState.BUYING
    bot.state_data = {'symbol': 'MINA/USDT'}
    bot.state_entry_time = time.time() - 3000
    hc = HealthChecker(bot)
    hc.check_interval = 0
    with caplog.at_level('WARNING', logger='HYDRA'), \
            patch('core.health.METRICS') as mock_metrics:
        for _ in range(5):
            report = hc.check()
            assert report['checks']['state_stuck'] is False
            assert report['overall'] is True
        mock_metrics.health_status.set.assert_called_with(1.0)
        mock_metrics.state_stuck.set.assert_called_with(1.0)
    assert hc.consecutive_fails == 0
    long_lines = [r for r in caplog.records if '@STATE_LONG@' in r.getMessage()]
    assert len(long_lines) == 1          # throttled to once per 5 min
    assert 'MINA/USDT' in long_lines[0].getMessage()


def test_exit_halted_for_hours_stays_healthy():
    bot = _healthy_bot()
    bot.state = BotState.EXITING
    bot.state_data = {'symbol': 'X/USDT', 'exit_halted': True}
    bot.state_entry_time = time.time() - 10_000
    hc = HealthChecker(bot)
    hc.check_interval = 0
    for _ in range(4):
        report = hc.check()
    assert report['checks']['state_stuck'] is False
    assert report['overall'] is True
    assert hc.consecutive_fails == 0


def test_no_carriage_return_prints_in_src():
    """print(..., end='\\r') glued thousands of chars into one docker-log line (B-10)."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / 'src'
    offenders = [str(p) for p in src.rglob('*.py') if "end='\\r'" in p.read_text(encoding='utf-8')]
    assert offenders == []


def test_log_every_throttles(caplog):
    from logger_setup import log_every
    with caplog.at_level('INFO', logger='HYDRA'):
        for _ in range(10):
            log_every('test_key_throttle', 60, '@TEST_PROGRESS@ tick', 20)
    assert sum('@TEST_PROGRESS@' in r.getMessage() for r in caplog.records) == 1


def test_exiting_limit_from_config():
    bot = DummyBot()
    bot.config.config = {'trading': {'order_execution_timeout_sec': 60, 'max_exit_attempts': 5}}
    hc = HealthChecker(bot)
    assert hc._state_limits()[BotState.EXITING] == 60 * 6 + 300


def test_state_idle_never_stuck():
    """IDLE is not a supervised state — any age is fine."""
    bot = DummyBot()
    bot.state = BotState.IDLE
    bot.state_entry_time = time.time() - 99999
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


def test_no_self_sigterm_on_consecutive_fails():
    """Restart is owned by the external watchdog — health must never self-kill.

    Two supervisors racing on the same process is audit H2/H3: the bot sent
    itself SIGTERM while the watchdog was also restarting the container.
    """
    bot = DummyBot()
    bot.last_rest_poll_time = time.time() - 120

    hc = HealthChecker(bot)
    hc.max_consecutive_fails = 2
    hc.check_interval = 0  # disable caching for rapid test

    with patch('os.kill') as mock_kill:
        hc.check()  # fail 1
        hc.check()  # fail 2 — crosses max_consecutive_fails
        hc.check()  # fail 3
        assert mock_kill.call_count == 0
    assert hc.consecutive_fails >= hc.max_consecutive_fails


def test_degraded_reports_zero_health_gauge():
    """Crossing max_consecutive_fails must drive health_status to 0."""
    bot = DummyBot()
    bot.last_rest_poll_time = time.time() - 120

    hc = HealthChecker(bot)
    hc.max_consecutive_fails = 2
    hc.check_interval = 0

    with patch('core.health.METRICS') as mock_metrics:
        hc.check()  # fail 1 — hysteresis holds the gauge at 1
        mock_metrics.health_status.set.assert_called_with(1.0)
        hc.check()  # fail 2 — degraded
        mock_metrics.health_status.set.assert_called_with(0.0)
