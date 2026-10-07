"""TZ-12A: real bid/ask in the price cache + the columns offline labelling needs.

Audit H6: the WS ticker handler fabricated a 0.05% spread around lastPrice and
overwrote whatever orderbook.1 had delivered, so the spread filter and the
limit-chase price both ran on invented quotes. The orderbook handler now stores
prices as well as sizes and stamps `book_ts`; synthetic values are only used when
no real quote exists at all.
"""
import time
from types import SimpleNamespace

import pytest

from api.bybit_client import WebSocketListener
from core.dispatcher import HydraDispatcher
from core.states.scanning import ScanningStateMixin
from database.models import TradeDatabase


@pytest.fixture
def ws():
    return WebSocketListener(api_key='', secret='')


BOOK_FRAME = {'b': [['0.99', '100']], 'a': [['1.01', '80']]}


# ----------------------------------------------------------------- orderbook.1
def test_orderbook_stores_prices_and_sizes(ws):
    ws._update_orderbook('DOGE/USDT', BOOK_FRAME['b'], BOOK_FRAME['a'])

    p = ws.get_price('DOGE/USDT')
    assert (p['bid'], p['ask']) == (0.99, 1.01)
    assert (p['bidVolume'], p['askVolume']) == (100.0, 80.0)
    assert p['bid_ask_source'] == 'book'
    assert p['book_ts'] > 0


def test_ticker_without_quotes_does_not_overwrite_fresh_book(ws):
    ws._update_orderbook('DOGE/USDT', BOOK_FRAME['b'], BOOK_FRAME['a'])
    ws._update_price('DOGE/USDT', {'lastPrice': '1.00', 'turnover24h': '500000'})

    p = ws.get_price('DOGE/USDT')
    assert (p['bid'], p['ask']) == (0.99, 1.01)      # NOT 0.9995 / 1.0005
    assert p['bid_ask_source'] == 'book'
    assert p['last'] == 1.0
    assert p['turnover24h'] == 500000.0
    # sizes from the book survive a ticker that carries no bid1Size/ask1Size
    assert (p['bidVolume'], p['askVolume']) == (100.0, 80.0)


def test_stale_book_falls_back_to_synthetic(ws):
    ws._update_orderbook('DOGE/USDT', BOOK_FRAME['b'], BOOK_FRAME['a'])
    with ws.price_lock:
        ws.latest_prices['DOGE/USDT']['book_ts'] = time.time() - (ws.BOOK_FRESH_SEC + 1)
    ws._update_price('DOGE/USDT', {'lastPrice': '1.00'})

    p = ws.get_price('DOGE/USDT')
    assert p['bid_ask_source'] == 'synthetic'
    assert (p['bid'], p['ask']) == (0.9995, 1.0005)


def test_ticker_own_quotes_are_real(ws):
    ws._update_price('DOGE/USDT', {'lastPrice': '1.00', 'bid1Price': '0.98',
                                   'ask1Price': '1.02', 'bid1Size': '5',
                                   'ask1Size': '7'})

    p = ws.get_price('DOGE/USDT')
    assert (p['bid'], p['ask']) == (0.98, 1.02)
    assert p['bid_ask_source'] == 'book'
    assert (p['bidVolume'], p['askVolume']) == (5.0, 7.0)


def test_no_book_no_quotes_is_synthetic(ws):
    ws._update_price('DOGE/USDT', {'lastPrice': '2.00'})

    p = ws.get_price('DOGE/USDT')
    assert p['bid_ask_source'] == 'synthetic'
    assert (p['bid'], p['ask']) == (1.999, 2.001)


def test_partial_book_frame_keeps_sizes_only(ws):
    """A frame with a size but no usable price must not invent quotes."""
    ws._update_orderbook('DOGE/USDT', [['0', '100']], [['0', '80']])

    p = ws.get_price('DOGE/USDT')
    assert (p['bidVolume'], p['askVolume']) == (100.0, 80.0)
    assert 'bid' not in p and 'book_ts' not in p


# ------------------------------------------------------------ dispatcher schema
@pytest.fixture
def db(tmp_path):
    d = TradeDatabase(db_path=str(tmp_path / "trades.db"))
    yield d
    d.close()


def _columns(db):
    cur = db._conn.cursor()
    cur.execute("PRAGMA table_info(dispatcher_features)")
    return {r[1] for r in cur.fetchall()}


def test_schema_has_calibration_columns(db):
    assert set(TradeDatabase.DF_COLUMNS) <= _columns(db)


def test_migration_adds_columns_to_legacy_table(tmp_path):
    """An old table without the new columns is migrated in place, not dropped."""
    import sqlite3
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE dispatcher_features (
        id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id INTEGER, timestamp REAL,
        symbol TEXT, confidence REAL, rvol_spike REAL, rvol_local REAL,
        dump_depth REAL, obi_skew REAL, btc_1h REAL, score REAL, mode TEXT)""")
    conn.execute("INSERT INTO dispatcher_features (symbol, score) VALUES ('OLD/USDT', 7.0)")
    conn.commit()
    conn.close()

    d = TradeDatabase(db_path=path)
    try:
        assert set(TradeDatabase.DF_COLUMNS) <= _columns(d)
        cur = d._conn.cursor()
        cur.execute("SELECT symbol, spread_pct FROM dispatcher_features")
        assert cur.fetchall() == [('OLD/USDT', None)]
    finally:
        d.close()


def test_log_dispatcher_features_persists_context(db):
    db.log_dispatcher_features(
        trade_id=0, symbol='DOGE/USDT', confidence=71.0, rvol_spike=2.1,
        rvol_local=2.1, dump_depth=4.5, obi_skew=0.2, btc_1h=-0.3, score=8.0,
        mode='normal',
        spread_pct=0.04, bid_vol=100.0, ask_vol=80.0, turnover24h=5_000_000.0,
        bid_ask_source='book', tp_pct=0.8, sl_pct=1.2, hold_sec=1800.0,
        entry_ask=1.01, source='demo',
    )

    cur = db._conn.cursor()
    cur.execute("""SELECT spread_pct, bid_vol, ask_vol, turnover24h, bid_ask_source,
                          tp_pct, sl_pct, hold_sec, entry_ask, source
                   FROM dispatcher_features""")
    assert cur.fetchone() == (0.04, 100.0, 80.0, 5_000_000.0, 'book',
                              0.8, 1.2, 1800.0, 1.01, 'demo')


def test_old_call_sites_still_work_and_leave_context_null(db):
    db.log_dispatcher_features(
        trade_id=0, symbol='DOGE/USDT', confidence=71.0, rvol_spike=2.1,
        rvol_local=2.1, dump_depth=4.5, obi_skew=0.2, btc_1h=-0.3, score=8.0,
        mode='normal',
    )

    cur = db._conn.cursor()
    cur.execute("SELECT spread_pct, bid_ask_source, source FROM dispatcher_features")
    assert cur.fetchone() == (None, None, None)


# ------------------------------------------------------- scanner-side context
TRADING = {'dry_run': True, 'panic_stop': 1.2, 'hard_exit_timeout_sec': 1800}


class _Scanner(ScanningStateMixin):
    """Only what _candidate_context touches."""

    def __init__(self, trading=None, demo=False):
        self.config = SimpleNamespace(
            get_trading_config=lambda: dict(trading or TRADING),
            get_take_profit_pct=lambda: 1.5,
        )
        self.dispatcher = HydraDispatcher()
        self.exchange = SimpleNamespace(demo_trading=demo)


def test_context_uses_real_book_quotes():
    ticker = {'bid': 0.99, 'ask': 1.01, 'bid_ask_source': 'book',
              'turnover24h': 5_000_000.0}

    ctx = _Scanner()._candidate_context(ticker, 100.0, 80.0, 'normal')

    assert ctx['spread_pct'] == pytest.approx((0.02 / 1.01) * 100)
    assert ctx['entry_ask'] == 1.01
    assert ctx['bid_ask_source'] == 'book'
    assert (ctx['bid_vol'], ctx['ask_vol']) == (100.0, 80.0)
    assert ctx['turnover24h'] == 5_000_000.0
    assert ctx['tp_pct'] == HydraDispatcher().get_grid_params('normal').take_profit_pct
    assert ctx['sl_pct'] == 1.2
    assert ctx['hold_sec'] == 1800
    assert ctx['source'] == 'dry_run'


def test_context_spread_is_none_for_synthetic_quotes():
    """A synthetic spread must not be recorded as a measurement."""
    ticker = {'bid': 0.9995, 'ask': 1.0005, 'bid_ask_source': 'synthetic'}

    ctx = _Scanner()._candidate_context(ticker, 0.0, 0.0, 'normal')

    assert ctx['spread_pct'] is None
    assert ctx['bid_ask_source'] == 'synthetic'


class _ValidatingScanner(_Scanner):
    """Enough of the bot for _validate_candidate to reach the dispatcher block."""

    def __init__(self, dispatcher_enabled, trade_db):
        super().__init__()
        self.config.get_market_conditions_config = lambda: {}
        self.exchange.fetch_ohlcv = lambda *a, **k: [[0, 1, 1, 1, 1, 1]] * 60
        self.indicators_enabled = True
        self.dispatcher_enabled = dispatcher_enabled
        self.trade_db = trade_db
        self.ws_tickers_cache = {'DOGE/USDT': {'bid': 0.99, 'ask': 1.01, 'bid_ask_source': 'book',
                                               'bidVolume': 100.0, 'askVolume': 80.0}}

    def _calculate_real_rvol(self, ohlcv):
        return 3.0

    def _check_btc_trend(self):
        return True

    def _calculate_btc_correlation(self, symbol):
        return 1.0

    def _add_to_rejected_cache(self, *a):
        raise AssertionError("candidate must pass validation")


@pytest.fixture
def analyzer_ok(monkeypatch):
    import indicators.matrix as m
    fake = SimpleNamespace(complete_analysis=lambda **k: {
        'status': 'ok', 'recommendation': 'BUY', 'confidence': 70.0})
    monkeypatch.setattr(m, 'analyzer', fake, raising=False)


CANDIDATE = {'symbol': 'DOGE/USDT', 'price': 1.0, 'drop': 4.5}


def test_validate_candidate_with_dispatcher_off_returns_defaults(analyzer_ok):
    """B-19: with the dispatcher disabled the candidate still validates (used to
    raise NameError on obi_skew_val) and carries neutral features; nothing is logged."""
    from unittest.mock import MagicMock
    db = MagicMock()

    res = _ValidatingScanner(dispatcher_enabled=False, trade_db=db)._validate_candidate(
        dict(CANDIDATE), 'neutral', 0.0)

    assert res['score'] == 0.0 and res['mode'] == 'normal'
    df = res['dispatcher_features']
    assert df['obi_skew'] == 0.0 and df['symbol'] == 'DOGE/USDT'
    assert 'entry_ask' not in df
    assert not db.log_dispatcher_features.called


def test_validate_candidate_logged_row_equals_carried_features(analyzer_ok):
    """B-19: the scan-time DB row and the dict carried into BUYING are one object."""
    from unittest.mock import MagicMock
    db = MagicMock()

    res = _ValidatingScanner(dispatcher_enabled=True, trade_db=db)._validate_candidate(
        dict(CANDIDATE), 'neutral', 0.0)

    kwargs = db.log_dispatcher_features.call_args.kwargs
    assert kwargs.pop('trade_id') == 0
    assert kwargs == res['dispatcher_features']
    assert kwargs['entry_ask'] == 1.01 and kwargs['bid_ask_source'] == 'book'
    assert kwargs['obi_skew'] == pytest.approx(20 / 180)


@pytest.mark.parametrize('trading, demo, expected', [
    ({'dry_run': True}, False, 'dry_run'),
    ({'dry_run': True}, True, 'dry_run'),
    ({'dry_run': False}, True, 'demo'),
    ({'dry_run': False}, False, 'live'),
])
def test_data_source(trading, demo, expected):
    s = _Scanner(trading=trading, demo=demo)
    assert s._data_source(trading) == expected
