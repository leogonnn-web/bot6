"""TZ-14 / B-25: coarse lot steps, the dust gauge, dead partial-TP warning.

The buy fee is taken in the coin; the remainder below one lot step is
unsellable, so `step * price / slot` is the worst-case leak per round trip —
independent of rounding. The scanner skips symbols above `max_lot_step_pct`,
the dust gauge makes the accumulated leak visible next to session profit, and
a partial TP that can never fire is flagged at config validation.
"""
import logging
from types import SimpleNamespace

import pytest

from config_models import validate_config
from core.bot import TradingBot
from core.dispatcher import HydraDispatcher
from core.states.scanning import ScanningStateMixin
from database.models import TradeDatabase
from metrics import METRICS


# ------------------------------------------------------------- lot-step filter
class _LotScanner(ScanningStateMixin):
    """Just enough of the bot for _lot_too_coarse -> _log_rejected_candidate."""

    def __init__(self, markets, trade_db=None):
        self.exchange = SimpleNamespace(exchange=SimpleNamespace(markets=markets),
                                        demo_trading=False)
        self.config = SimpleNamespace(
            get_trading_config=lambda: {'dry_run': True, 'panic_stop': 1.2,
                                        'hard_exit_timeout_sec': 1800},
            get_take_profit_pct=lambda: 1.5,
        )
        self.dispatcher = HydraDispatcher()
        self.ws_tickers_cache = {'QNT/USDT': {'bid': 0.99, 'ask': 1.01,
                                              'bid_ask_source': 'book',
                                              'bidVolume': 100.0, 'askVolume': 80.0}}
        self.trade_db = trade_db


TRADING_SLOT = {'slot_size': 6.0, 'max_lot_step_pct': 0.5}


def test_lot_step_above_slot_pct_is_filtered():
    # QNT-like: one step costs 3.2% of a $6 slot — above the TP itself.
    s = _LotScanner({'QNT/USDT': {'precision': {'amount': 0.19}}})
    assert s._lot_too_coarse('QNT/USDT', 1.0, 4.5, TRADING_SLOT) is True


def test_lot_step_below_slot_pct_passes():
    s = _LotScanner({'QNT/USDT': {'precision': {'amount': 0.006}}})
    assert s._lot_too_coarse('QNT/USDT', 1.0, 4.5, TRADING_SLOT) is False


def test_unknown_step_is_not_filtered():
    """No market / no precision -> don't refuse here; _entry_sellable still guards."""
    s = _LotScanner({'QNT/USDT': {'precision': {}}})
    assert s._lot_too_coarse('QNT/USDT', 1.0, 4.5, TRADING_SLOT) is False
    assert s._lot_too_coarse('MISSING/USDT', 1.0, 4.5, TRADING_SLOT) is False


def test_lot_coarse_writes_one_reject_row_per_window(tmp_path):
    db = TradeDatabase(db_path=str(tmp_path / "trades.db"))
    try:
        s = _LotScanner({'QNT/USDT': {'precision': {'amount': 0.19}}}, trade_db=db)
        assert s._lot_too_coarse('QNT/USDT', 1.0, 4.5, TRADING_SLOT) is True
        assert s._lot_too_coarse('QNT/USDT', 1.0, 4.5, TRADING_SLOT) is True

        cur = db._conn.cursor()
        cur.execute("SELECT reject_reason, dump_depth FROM dispatcher_features")
        assert cur.fetchall() == [('lot_coarse', 4.5)]
    finally:
        db.close()


# ------------------------------------------------------------------ dust gauge
def test_dust_gauge_counts_only_coins_at_or_below_threshold():
    bot = TradingBot.__new__(TradingBot)
    thresholds = {'SHIB/USDT': 200.0, 'DOGE/USDT': 5.0, 'NOPE/USDT': float('inf')}
    prices = {'SHIB/USDT': 0.00001, 'DOGE/USDT': 0.10}
    bot._dust_threshold = lambda s: thresholds.get(s, float('inf'))
    bot._approx_price = lambda s: prices.get(s, 0.0)

    bot._update_dust_gauge({'SHIB': 100.0,      # below dust -> counts
                            'DOGE': 10.0,       # above dust -> a position, not dust
                            'NOPE': 3.0,        # no spot market -> not ours
                            'QWE': 0.5})        # unknown -> not ours

    assert METRICS.dust_usdt._value.get() == pytest.approx(0.001)


# ---------------------------------------------------- dead partial-TP warning
def test_partial_tp_at_or_above_grid_tp_warns(caplog, valid_full_config):
    valid_full_config['trading']['partial_tp_activation_pct'] = 1.0  # >= 0.8 TP
    with caplog.at_level(logging.WARNING, logger='HYDRA'):
        validate_config(valid_full_config)
    assert '@CONFIG_WARN@' in caplog.text
    assert 'partial_tp_activation_pct' in caplog.text


def test_partial_tp_disabled_or_below_tp_is_quiet(caplog, valid_full_config):
    for pct in (0.0, 0.5):
        valid_full_config['trading']['partial_tp_activation_pct'] = pct
        with caplog.at_level(logging.WARNING, logger='HYDRA'):
            caplog.clear()
            validate_config(valid_full_config)
        assert '@CONFIG_WARN@' not in caplog.text
