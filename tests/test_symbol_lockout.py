"""TZ-22 / B-33: per-symbol lockout after losses and a cap on repeat entries.

Demo 02.11: 13 of 38 entries on BEAM/USDT while it slid all day; the global
cooldown and the 90 s symbol cooldown let the bot catch the same knife again.
The lock reads trades.db (every exit path already writes there), so it
survives restarts. Each rule is switched off by setting its key to 0.
"""
import logging
import time

import pytest

from tests.test_dispatcher_features import (  # noqa: F401
    _ValidatingScanner, analyzer_ok, db, _df_rows, CANDIDATE, TRADING,
)

SYM = 'DOGE/USDT'
H = 3600
LOCKOUT = {
    'lockout_after_panic_min': 180,
    'lockout_losses_count': 2,
    'lockout_losses_window_min': 360,
    'lockout_losses_duration_min': 720,
    'max_entries_per_symbol_6h': 3,
}


def _trade(db, side, hours_ago, profit=0.0, symbol=SYM):
    db._execute_write(
        "INSERT INTO trades (symbol, side, amount, price, timestamp, confidence, profit) "
        "VALUES (?, ?, 1, 1, ?, 0, ?)", (symbol, side, time.time() - hours_ago * H, profit))


def _scanner(db, **overrides):
    s = _ValidatingScanner(dispatcher_enabled=True, trade_db=db)
    s.config.get_trading_config = lambda: {**TRADING, **LOCKOUT, **overrides}
    return s


def _locked(db, **overrides):
    s = _scanner(db, **overrides)
    return s._symbol_locked(SYM, s.config.get_trading_config())


# ------------------------------------------------------------- (a) panic
def test_panic_one_hour_ago_locks(db):
    _trade(db, 'sell_panic', 1, -0.2)
    assert _locked(db, lockout_losses_count=0) == 'panic'


def test_panic_four_hours_ago_does_not_lock(db):
    _trade(db, 'sell_panic', 4, -0.2)
    assert _locked(db, lockout_losses_count=0) is None


# ------------------------------------------------------------- (b) loss series
def test_two_losses_in_window_lock(db):
    _trade(db, 'sell', 5, -0.1)
    _trade(db, 'sell', 4, -0.1)
    assert _locked(db) == 'losses'


def test_one_loss_does_not_lock(db):
    _trade(db, 'sell', 4, -0.1)
    _trade(db, 'sell', 3, +0.02)
    assert _locked(db) is None


def test_losses_further_apart_than_window_do_not_lock(db):
    _trade(db, 'sell', 11, -0.1)
    _trade(db, 'sell', 4, -0.1)
    assert _locked(db) is None


def test_loss_lock_outlives_window_and_expires_from_last_loss(db):
    _trade(db, 'sell', 8, -0.1)
    _trade(db, 'sell', 7, -0.1)          # series ended 7 h ago: still inside 12 h
    assert _locked(db) == 'losses'


def test_loss_lock_expired(db):
    _trade(db, 'sell', 14, -0.1)
    _trade(db, 'sell', 13, -0.1)         # 13 h > 12 h from the last loss
    assert _locked(db) is None


def test_other_symbol_does_not_count(db):
    _trade(db, 'sell_panic', 1, -0.2, symbol='BEAM/USDT')
    _trade(db, 'sell', 2, -0.1, symbol='BEAM/USDT')
    assert _locked(db) is None


# ------------------------------------------------------------- (c) entry cap
def test_three_buys_in_6h_lock(db):
    for h in (5, 3, 1):
        _trade(db, 'buy', h)
    assert _locked(db) == 'entries'


def test_two_buys_do_not_lock(db):
    for h in (5, 1):
        _trade(db, 'buy', h)
    _trade(db, 'buy', 7)                 # outside 6 h
    assert _locked(db) is None


# ------------------------------------------------------------- (d) 0 = off
@pytest.mark.parametrize('key, setup', [
    ('lockout_after_panic_min', [('sell_panic', 1, 0.0)]),
    ('lockout_losses_count', [('sell', 2, -0.1), ('sell', 1, -0.1)]),
    ('lockout_losses_window_min', [('sell', 2, -0.1), ('sell', 1, -0.1)]),
    ('lockout_losses_duration_min', [('sell', 2, -0.1), ('sell', 1, -0.1)]),
    ('max_entries_per_symbol_6h', [('buy', 3, 0.0), ('buy', 2, 0.0), ('buy', 1, 0.0)]),
])
def test_zero_disables_only_that_rule(db, key, setup):
    for side, h, p in setup:
        _trade(db, side, h, p)
    assert _locked(db) is not None
    assert _locked(db, **{key: 0}) is None


def test_all_rules_off_never_touch_db():
    from unittest.mock import MagicMock
    db = MagicMock()
    assert _locked(db, **{k: 0 for k in LOCKOUT}) is None
    assert not db.method_calls


# ------------------------------------------------------------- (e) fail-closed
def test_db_unknown_locks(db, monkeypatch):
    monkeypatch.setattr(db, 'symbol_recent_closes', lambda *a: None)
    assert _locked(db) == 'db_unknown'


def test_db_unknown_on_entries_locks(db, monkeypatch):
    monkeypatch.setattr(db, 'symbol_recent_entries', lambda *a: None)
    assert _locked(db) == 'db_unknown'


def test_db_read_error_returns_none(db):
    db.close()
    db.db_path = '/nonexistent/dir/trades.db'
    assert db.symbol_recent_closes(SYM, 0) is None
    assert db.symbol_recent_entries(SYM, 0) is None


# ------------------------------------------------------------- (f) gate placement
def test_locked_candidate_skips_ohlcv_and_logs_one_row(analyzer_ok, db, caplog, monkeypatch):
    import logger_setup
    monkeypatch.setattr(logger_setup, '_last_emit', {})     # log_every throttle is process-wide
    _trade(db, 'sell_panic', 1, -0.2)
    s = _scanner(db)
    s.exchange.fetch_ohlcv = lambda *a, **k: pytest.fail("fetch_ohlcv must not be called")
    with caplog.at_level(logging.INFO, logger='HYDRA'):
        assert s._validate_candidate(dict(CANDIDATE), 'neutral', -0.3) is None
    assert '@SYMBOL_LOCKOUT@ DOGE/USDT (panic) until' in caplog.text
    assert _df_rows(db, "reject_reason, symbol, btc_1h") == [('symbol_lockout', SYM, -0.3)]


def test_repeated_locked_candidate_logs_one_row_per_interval(analyzer_ok, db, monkeypatch):
    _trade(db, 'sell_panic', 1, -0.2)
    s = _scanner(db)
    s.exchange.fetch_ohlcv = lambda *a, **k: pytest.fail("fetch_ohlcv must not be called")
    for _ in range(3):
        assert s._validate_candidate(dict(CANDIDATE), 'neutral', 0.0) is None
    assert _df_rows(db, "reject_reason") == [('symbol_lockout',)]
    s._lockout_logged[SYM] -= s.LOCKOUT_LOG_SEC                  # interval elapsed
    assert s._validate_candidate(dict(CANDIDATE), 'neutral', 0.0) is None
    assert _df_rows(db, "reject_reason") == [('symbol_lockout',)] * 2


def test_unlocked_candidate_passes(analyzer_ok, db):
    _trade(db, 'sell', 1, +0.02)
    res = _scanner(db)._validate_candidate(dict(CANDIDATE), 'neutral', 0.0)
    assert res is not None
    assert _df_rows(db, "reject_reason") == [(None,)]


# ------------------------------------------------------------- config
def test_repo_config_and_schema(valid_full_config):
    import json, os
    from config_models import validate_config, TradingConfig
    repo = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'shared', 'config.json'), encoding='utf-8'))
    assert {k: repo['trading'][k] for k in LOCKOUT} == LOCKOUT
    validate_config(repo)
    model = TradingConfig(**valid_full_config['trading'])
    assert {k: getattr(model, k) for k in LOCKOUT} == LOCKOUT        # schema defaults
    valid_full_config['trading']['max_entries_per_symbol_6h'] = -1
    with pytest.raises(Exception):
        validate_config(valid_full_config)
