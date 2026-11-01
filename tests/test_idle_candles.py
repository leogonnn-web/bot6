"""TZ-21 / B-32: empty 1m candles in the last hour mark a coin nobody trades.

Checked on the 33 real demo entries (2026-11-01, public klines): all three panic
exits (BAT x2, BSB) had 11-43 idle bars out of 60, no panic below 10. RVOL cannot
see this - a dead baseline makes one stray order look like a volume spike.
The count is written on every dispatcher_features row; the gate (idle_bars_max)
and the warning (idle_bars_warn) are config knobs.
"""
import logging

import pytest

from indicators.dump_features import idle_bar_count, is_idle_bar, IDLE_BARS_SHORT_HISTORY
from tests.test_dispatcher_features import (  # noqa: F401
    _ValidatingScanner, analyzer_ok, db, _df_rows, CANDIDATE, TRADING,
)

ACTIVE = [0, 1.00, 1.01, 0.99, 1.00, 500.0]
FLAT = [0, 1.00, 1.00, 1.00, 1.00, 0.0]
ONE_PRINT = [0, 1.00, 1.00, 1.00, 1.00, 12.0]       # high == low, volume > 0
DOJI_WITH_RANGE = [0, 1.00, 1.01, 0.99, 1.00, 3.0]   # open == close but traded


def _bars(idle, total=60):
    return [FLAT] * idle + [ACTIVE] * (total - idle - 1) + [ACTIVE]   # last = forming


# ------------------------------------------------------------- pure function
def test_is_idle_bar_criteria():
    assert is_idle_bar(FLAT)
    assert is_idle_bar(ONE_PRINT)
    assert not is_idle_bar(ACTIVE)
    assert not is_idle_bar(DOJI_WITH_RANGE)          # open==close alone is not idle
    assert is_idle_bar([0, 1, 1])                    # malformed -> idle


def test_idle_bar_count_excludes_forming_bar():
    assert idle_bar_count([ACTIVE] * 59 + [FLAT]) == 0
    assert idle_bar_count(_bars(16)) == 16


def test_idle_bar_count_short_history_is_fully_idle():
    assert idle_bar_count([ACTIVE] * 30) == IDLE_BARS_SHORT_HISTORY
    assert idle_bar_count([]) == IDLE_BARS_SHORT_HISTORY
    assert idle_bar_count([ACTIVE] * 51) == 0        # 50 closed bars is enough


# ------------------------------------------------------------- scanner gate
def _scanner(db, idle, idle_max=10, idle_warn=6):
    s = _ValidatingScanner(dispatcher_enabled=True, trade_db=db)
    s.exchange.fetch_ohlcv = lambda *a, **k: _bars(idle)
    s.config.get_trading_config = lambda: {**TRADING, 'idle_bars_max': idle_max, 'idle_bars_warn': idle_warn}
    s._add_to_rejected_cache = lambda *a: None
    return s


def test_idle_at_or_above_max_is_refused_with_reason(analyzer_ok, db, caplog):
    with caplog.at_level(logging.INFO, logger='HYDRA'):
        assert _scanner(db, idle=16)._validate_candidate(dict(CANDIDATE), 'neutral', 0.0) is None
    assert '@IDLE_CANDLES@' in caplog.text
    assert _df_rows(db, "reject_reason, idle_bars, rvol_spike") == [('idle_candles', 16, 3.0)]


def test_idle_between_warn_and_max_passes_with_warning(analyzer_ok, db, caplog):
    with caplog.at_level(logging.INFO, logger='HYDRA'):
        res = _scanner(db, idle=8)._validate_candidate(dict(CANDIDATE), 'neutral', 0.0)
    assert res is not None
    assert '@IDLE_CANDLES_WARN@' in caplog.text and '@IDLE_CANDLES@' not in caplog.text
    assert res['dispatcher_features']['idle_bars'] == 8
    assert _df_rows(db, "reject_reason, idle_bars") == [(None, 8)]


def test_idle_below_warn_is_silent_but_recorded(analyzer_ok, db, caplog):
    with caplog.at_level(logging.INFO, logger='HYDRA'):
        res = _scanner(db, idle=2)._validate_candidate(dict(CANDIDATE), 'neutral', 0.0)
    assert res is not None
    assert 'IDLE_CANDLES' not in caplog.text
    assert _df_rows(db, "idle_bars") == [(2,)]


def test_max_zero_means_log_only(analyzer_ok, db, caplog):
    with caplog.at_level(logging.INFO, logger='HYDRA'):
        res = _scanner(db, idle=40, idle_max=0)._validate_candidate(dict(CANDIDATE), 'neutral', 0.0)
    assert res is not None
    assert '@IDLE_CANDLES_WARN@' in caplog.text
    assert _df_rows(db, "reject_reason, idle_bars") == [(None, 40)]


def test_other_rejects_carry_idle_bars_too(analyzer_ok, db):
    s = _scanner(db, idle=4)
    s._calculate_real_rvol = lambda ohlcv: 0.5
    assert s._validate_candidate(dict(CANDIDATE), 'neutral', 0.0) is None
    assert _df_rows(db, "reject_reason, idle_bars") == [('rvol_low', 4)]


def test_short_history_is_refused(analyzer_ok, db):
    s = _scanner(db, idle=0)
    s.exchange.fetch_ohlcv = lambda *a, **k: [ACTIVE] * 20
    assert s._validate_candidate(dict(CANDIDATE), 'neutral', 0.0) is None
    assert _df_rows(db, "reject_reason, idle_bars") == [('idle_candles', IDLE_BARS_SHORT_HISTORY)]


# ------------------------------------------------------------- config
def test_repo_config_and_schema(valid_full_config):
    import json, os
    from config_models import validate_config, TradingConfig
    repo = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'shared', 'config.json'), encoding='utf-8'))
    assert (repo['trading']['idle_bars_max'], repo['trading']['idle_bars_warn']) == (10, 6)
    validate_config(repo)
    assert TradingConfig(**valid_full_config['trading']).idle_bars_max == 10    # schema default
    valid_full_config['trading']['idle_bars_max'] = -1
    with pytest.raises(Exception):
        validate_config(valid_full_config)
