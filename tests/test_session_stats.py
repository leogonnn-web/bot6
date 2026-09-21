"""Single source of PnL: session stats read the `profit` column, not a re-derivation.

TZ-05 (audit H7, H8; BACKLOG B-08). Before this, get_session_stats() re-computed
GROSS profit `(price - buy_price) * matched` via FIFO matching and ignored the
net `profit` column it writes itself, so the risk capital lock was fed numbers
that systematically understated losses.
"""
import time
from unittest.mock import MagicMock

import pytest

from database.models import TradeDatabase


@pytest.fixture
def db(tmp_path):
    d = TradeDatabase(db_path=str(tmp_path / "trades.db"))
    yield d
    d.close()


# --------------------------------------------------------------------------- (a)
def test_session_profit_reads_profit_column(db):
    """buy 10@1.0 + sell 10@1.1 booked at net 0.8 -> session_profit == 0.8.

    Gross would be (1.1 - 1.0) * 10 == 1.0; the net figure must win.
    """
    db.log_trade("SHIB/USDT", "buy", 10.0, 1.0)
    db.log_trade("SHIB/USDT", "sell", 10.0, 1.1, profit=0.8)

    stats = db.get_session_stats()
    assert stats["session_profit"] == pytest.approx(0.8)
    assert stats["total_trades"] == 1
    assert stats["winning_trades"] == 1
    assert stats["win_rate"] == pytest.approx(100.0)


# --------------------------------------------------------------------------- (b)
def test_partial_tp_adds_profit_but_not_a_trade(db):
    """sell_partial contributes PnL but is a leg, not a deal."""
    db.log_trade("SHIB/USDT", "buy", 10.0, 1.0)
    db.log_trade("SHIB/USDT", "sell_partial", 5.0, 1.05, profit=0.3)
    db.log_trade("SHIB/USDT", "sell", 5.0, 1.1, profit=0.8)

    stats = db.get_session_stats()
    assert stats["session_profit"] == pytest.approx(1.1)
    assert stats["total_trades"] == 1


def test_losses_are_not_understated(db):
    """A losing close must surface at its full net size."""
    db.log_trade("SHIB/USDT", "buy", 10.0, 1.0)
    db.log_trade("SHIB/USDT", "sell_panic", 10.0, 0.95, profit=-0.7)

    stats = db.get_session_stats()
    assert stats["session_profit"] == pytest.approx(-0.7)
    assert stats["total_trades"] == 1
    assert stats["winning_trades"] == 0
    assert stats["win_rate"] == pytest.approx(0.0)


def test_since_ts_scopes_the_session(db):
    db.log_trade("SHIB/USDT", "sell", 10.0, 1.1, profit=5.0)
    cutoff = time.time() + 0.01
    time.sleep(0.05)
    db.log_trade("SHIB/USDT", "sell", 10.0, 1.1, profit=2.0)

    assert db.get_session_stats(since_ts=cutoff)["session_profit"] == pytest.approx(2.0)


def test_buy_rows_never_contribute_profit(db):
    """A stray profit value on a buy row must not leak into the session."""
    db.log_trade("SHIB/USDT", "buy", 10.0, 1.0, profit=99.0)

    stats = db.get_session_stats()
    assert stats["session_profit"] == pytest.approx(0.0)
    assert stats["total_trades"] == 0


# --------------------------------------------------------------------------- (c)
def test_daily_count_ignores_buys_and_partials(db):
    """max_trades_per_day limits round trips, not legs."""
    db.log_trade("SHIB/USDT", "buy", 10.0, 1.0)
    db.log_trade("SHIB/USDT", "buy_grid_complete", 10.0, 1.0)
    db.log_trade("SHIB/USDT", "sell_partial", 5.0, 1.05, profit=0.3)
    db.log_trade("SHIB/USDT", "sell", 5.0, 1.1, profit=0.8)
    db.log_trade("DOGE/USDT", "sell_panic", 5.0, 0.9, profit=-0.4)

    assert db.get_daily_trades_count() == 2


# --------------------------------------------------------------------------- (d)
class _FakeInner:
    def __init__(self, taker=0.001, maker=0.001):
        self._taker = taker
        self._maker = maker

    def market(self, symbol):
        return {"taker": self._taker, "maker": self._maker}

    def price_to_precision(self, symbol, price):
        return f"{float(price):.6f}"

    def amount_to_precision(self, symbol, amount):
        return str(amount)


def _breakeven_bot(taker=0.001, maker=0.001, buy_price=100.0, amount=3.0):
    """Live (non-dry-run) bot so the price actually reaches order_manager.sell.

    order_id starts with 'virtual_' so _set_breakeven skips the cancel + sleep.
    """
    from core.bot import TradingBot

    b = TradingBot.__new__(TradingBot)
    b.exchange = MagicMock()
    b.exchange.exchange = _FakeInner(taker, maker)
    b.config = MagicMock()
    b.config.get_trading_config.return_value = {"dry_run": False}
    b.order_manager = MagicMock()
    b.order_manager.sell.return_value = {"id": "be1"}
    b._save_state = lambda: None
    b.state_data = {
        "symbol": "BTC/USDT",
        "buy_price": buy_price,
        "amount": amount,
        "order_id": "virtual_1",
    }
    return b


def _placed_price(bot):
    assert bot.order_manager.sell.called, "no breakeven order was placed"
    args, _ = bot.order_manager.sell.call_args
    return args[2]


def test_breakeven_price_covers_round_trip_fees():
    """The order must go out at buy * (1 + taker + maker + 0.0002) = 1.0022.

    The hardcoded 1.001 sat below the 0.2% round-trip fee, so every "breakeven"
    exit realised a loss.
    """
    bot = _breakeven_bot(taker=0.001, maker=0.001, buy_price=100.0)
    bot._set_breakeven()

    assert _placed_price(bot) == pytest.approx(100.22)
    assert bot.state_data["is_breakeven"] is True
    assert bot.state_data["order_id"] == "be1"


def test_breakeven_price_is_above_the_old_hardcoded_value():
    """Regression guard: 1.001 must never come back."""
    bot = _breakeven_bot(taker=0.001, maker=0.001, buy_price=100.0)
    bot._set_breakeven()

    assert _placed_price(bot) > 100.0 * 1.001


def test_breakeven_multiplier_tracks_actual_fees():
    """Higher venue fees must push the breakeven price up."""
    bot = _breakeven_bot(taker=0.002, maker=0.0015, buy_price=100.0)
    bot._set_breakeven()

    # 1 + 0.002 + 0.0015 + 0.0002 = 1.0037
    assert _placed_price(bot) == pytest.approx(100.37)


def test_breakeven_amount_goes_through_precision():
    bot = _breakeven_bot(buy_price=100.0, amount=3.5)
    bot._set_breakeven()

    args, _ = bot.order_manager.sell.call_args
    assert args[1] == pytest.approx(3.5)


# ------------------------------------------------- partial TP booked on fill
def _partial_tp_bot(order):
    """Live bot with a resting partial-TP order; `order` is fetch_order's answer."""
    from core.bot import TradingBot

    b = TradingBot.__new__(TradingBot)
    b.exchange = MagicMock()
    b.exchange.fetch_order.return_value = order
    b.config = MagicMock()
    b.config.get_trading_config.return_value = {"fee_pct": 0.0, "panic_slippage_pct": 0.0}
    b.trade_db = MagicMock()
    b.trade_db.log_trade.return_value = 1
    b.session_profit = 0.0
    b._save_state = lambda: None
    b.state_data = {
        "symbol": "SHIB/USDT",
        "buy_price": 1.0,
        "amount": 5.0,
        "partial_tp_order_id": "ptp1",
        "partial_tp_amount": 5.0,
    }
    return b


def test_partial_tp_not_booked_while_order_rests():
    """An open limit order is an intent — no PnL, no trade row."""
    bot = _partial_tp_bot({"status": "open", "filled": 0.0})
    bot._reconcile_partial_tp("SHIB/USDT")

    assert bot.session_profit == 0.0
    assert not bot.trade_db.log_trade.called
    assert bot.state_data["partial_tp_order_id"] == "ptp1"


def test_partial_tp_booked_at_actual_fill_price():
    """On fill, PnL uses the exchange's average price and filled qty."""
    bot = _partial_tp_bot({"status": "closed", "filled": 5.0, "average": 1.1})
    bot._reconcile_partial_tp("SHIB/USDT")

    assert bot.session_profit == pytest.approx(0.5)  # (1.1 - 1.0) * 5, fee_pct=0
    side = bot.trade_db.log_trade.call_args[0][1]
    assert side == "sell_partial"
    assert "partial_tp_order_id" not in bot.state_data


def test_partial_tp_unknown_status_keeps_waiting():
    """fetch_order None means UNKNOWN, not 'not filled' — hold the claim."""
    bot = _partial_tp_bot(None)
    bot._reconcile_partial_tp("SHIB/USDT")

    assert bot.session_profit == 0.0
    assert not bot.trade_db.log_trade.called
    assert bot.state_data["partial_tp_order_id"] == "ptp1"
    assert bot.state_data["partial_tp_unknown_count"] == 1


def test_partial_tp_canceled_books_nothing():
    bot = _partial_tp_bot({"status": "canceled", "filled": 0.0})
    bot._reconcile_partial_tp("SHIB/USDT")

    assert bot.session_profit == 0.0
    assert not bot.trade_db.log_trade.called
    assert "partial_tp_order_id" not in bot.state_data


def test_reconcile_is_a_noop_without_pending_order():
    bot = _partial_tp_bot({"status": "closed", "filled": 5.0, "average": 1.1})
    bot.state_data.pop("partial_tp_order_id")
    bot._reconcile_partial_tp("SHIB/USDT")

    assert bot.session_profit == 0.0
    assert not bot.exchange.fetch_order.called
