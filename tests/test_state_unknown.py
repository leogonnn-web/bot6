"""State machine under exchange uncertainty.

Rule under test: when the exchange answers "unknown" (None) the bot must NOT
change state, place orders, log trades or reset to IDLE. IDLE is entered only
through TradingBot._transition_to_idle, which requires the exchange to confirm
the coin is no longer held.
"""
import time
from unittest.mock import MagicMock

import pytest

import core.states.in_position as in_position_mod
from core.bot import TradingBot
from core.state_enum import BotState


# --------------------------------------------------------------------------- fakes
class FakeInner:
    """The ccxt object living at BybitClient.exchange."""
    def __init__(self):
        self.markets = {}
        self.my_trades = []

    def amount_to_precision(self, symbol, amount):
        return str(amount)

    def price_to_precision(self, symbol, price):
        return str(price)

    def fetch_my_trades(self, symbol, limit=50):
        return self.my_trades

    def market(self, symbol):
        return {'taker': 0.001, 'maker': 0.001}


class FakeExchange:
    """BybitClient stand-in: every answer is scripted; None means 'unknown'."""
    def __init__(self):
        self.exchange = FakeInner()
        self.order = None            # what fetch_order returns
        self.coin_balance = None     # what get_coin_balance returns
        self.free_usdt = 100.0
        self.holdings = {}           # get_non_usdt_holdings
        self.ticker = None           # fetch_ticker
        self.cancelled = []

    def fetch_order(self, order_id, symbol):
        return self.order

    def get_coin_balance(self, coin):
        return self.coin_balance

    def get_free_usdt(self):
        return self.free_usdt

    def get_non_usdt_holdings(self):
        return self.holdings

    def fetch_ticker(self, symbol):
        return self.ticker

    def fetch_ohlcv(self, *a, **k):
        return []

    def cancel_order(self, order_id, symbol):
        self.cancelled.append(order_id)
        return {}


TRADING = {
    'dry_run': False, 'slot_size': 5.0, 'take_profit': 1.5, 'panic_stop': 1.2,
    'order_execution_timeout_sec': 60, 'partial_tp_activation_pct': 1.0,
    'partial_tp_size_pct': 50.0, 'move_to_breakeven': True, 'trailing_callback_pct': 1.1,
    'hard_exit_timeout_sec': 1800, 'fee_pct': 0.1, 'panic_slippage_pct': 0.1,
    'limit_chaser': {'enabled': True, 'chase_sec_urgent': 3.0, 'chase_sec_normal': 12.0,
                     'urgent_skip_below_pct': 1.5, 'repeg': True},
    'max_exit_attempts': 3,
}


@pytest.fixture
def bot(monkeypatch):
    """TradingBot without __init__ side effects (no ccxt, DB, signals, threads)."""
    monkeypatch.setattr(in_position_mod.time, 'sleep', lambda *_: None)
    b = TradingBot.__new__(TradingBot)
    b.exchange = FakeExchange()
    b.config = MagicMock()
    b.config.get_trading_config.return_value = dict(TRADING)
    b.config.config = {'trading': dict(TRADING)}
    b.trade_db = MagicMock()
    b.trade_db.log_trade.return_value = 1
    b.order_manager = MagicMock()
    b.order_manager.sell.return_value = {'id': 'tp1'}
    b.order_manager.market_sell.return_value = {'id': 'mk1'}
    b.order_manager.buy.return_value = {'id': 'b1'}
    b.ws_tickers_cache = {}
    b._price_rest_ts = {}
    b.state = BotState.IDLE
    b.state_data = {}
    b.state_entry_time = time.time()
    b.session_profit = 0.0
    b.symbol_cooldown = {}
    b.cooldown_duration = 90
    b._panic_events = []
    b._breaker_until = 0.0
    b.hydra_net_config = {}
    b.last_loss_time = 0.0
    b.maintenance_mode = False
    b._save_state = lambda: None
    b._apply_dispatcher_feedback = lambda *_: None
    return b


def _fresh(price):
    return {'last': price, 'bid': price * 0.999, 'ask': price * 1.001, 'timestamp': time.time()}


def _in_position(bot, order_id='tp1', buy_price=1.0, amount=5.0):
    bot.state = BotState.IN_POSITION
    bot.state_data = {
        'symbol': 'SHIB/USDT', 'buy_price': buy_price, 'amount': amount,
        'buy_time': time.time(), 'is_dry_run': False, 'target_sell_price': buy_price * 1.015,
        'order_id': order_id, 'is_breakeven': False, 'partial_tp_hit': False, 'trailing_high': buy_price,
    }


def _buying(bot, elapsed=0.0, grid=False):
    bot.state = BotState.BUYING
    bot.state_data = {
        'symbol': 'SHIB/USDT', 'buy_price': 1.0, 'buy_time': time.time() - elapsed,
        'order_id': 'b1', 'amount_target': 5.0, 'amount': 5.0, 'is_dry_run': False,
        'is_grid_active': grid, 'dispatcher_features': {},
    }


def _exiting(bot, elapsed=0.0):
    bot.state = BotState.EXITING
    bot.state_data = {
        'symbol': 'SHIB/USDT', 'buy_price': 1.0, 'amount': 5.0, 'buy_time': time.time() - 300,
        'exit_order_id': 'x1', 'exit_time': time.time() - elapsed, 'exit_amount': 5.0,
        'exit_type': 'panic', 'exit_mode': 'market', 'order_id': None, 'is_dry_run': False,
    }


def _no_orders_placed(bot):
    assert not bot.order_manager.sell.called
    assert not bot.order_manager.market_sell.called
    assert not bot.order_manager.buy.called


# ------------------------------------------------------------ _transition_to_idle
def test_idle_deferred_when_balance_unknown(bot):
    _in_position(bot)
    bot.exchange.coin_balance = None
    assert bot._transition_to_idle('t') is False
    assert bot.state == BotState.IN_POSITION
    assert bot.state_data['symbol'] == 'SHIB/USDT'


def test_idle_refused_and_position_adopted_when_coin_held(bot):
    _exiting(bot)
    bot.exchange.coin_balance = 4.9
    assert bot._transition_to_idle('t') is False
    assert bot.state == BotState.IN_POSITION
    assert bot.state_data['amount'] == 4.9
    assert bot.state_data['order_id'] is None


def test_idle_entered_when_exchange_confirms_empty(bot):
    _in_position(bot)
    bot.exchange.coin_balance = 0.0
    assert bot._transition_to_idle('t') is True
    assert bot.state == BotState.IDLE and bot.state_data == {}


def test_dry_run_position_goes_idle_without_exchange(bot):
    _in_position(bot)
    bot.state_data['is_dry_run'] = True
    bot.exchange.coin_balance = None
    assert bot._transition_to_idle('t') is True


# ------------------------------------------------------------------ fresh price
def test_fresh_price_none_when_ws_stale_and_rest_fails(bot):
    bot.ws_tickers_cache['SHIB/USDT'] = {'last': 1.0, 'timestamp': time.time() - 120}
    bot.exchange.ticker = None
    assert bot._get_fresh_price('SHIB/USDT') is None


def test_fresh_price_rest_fallback(bot):
    bot.exchange.ticker = {'last': 2.0, 'bid': 1.99, 'ask': 2.01}
    px = bot._get_fresh_price('SHIB/USDT')
    assert px['last'] == 2.0 and px['source'] == 'rest'


# ---------------------------------------------------------------------- BUYING
def test_buying_unknown_status_keeps_state(bot):
    _buying(bot)
    bot.exchange.order = None
    for _ in range(3):
        bot._handle_buying_state()
    assert bot.state == BotState.BUYING
    assert bot.state_data['order_id'] == 'b1'
    assert bot.state_data['status_unknown_count'] == 3
    _no_orders_placed(bot)


def test_buying_closed_without_fill_data_resolves_by_balance_held(bot):
    _buying(bot)
    bot.exchange.order = {'id': 'b1', 'status': 'closed', 'filled': 0.0, 'price': None, 'average': None}
    bot.exchange.coin_balance = 4.99
    bot._handle_buying_state()
    assert bot.state == BotState.IN_POSITION
    assert bot.state_data['buy_price'] == 1.0            # from state, no invented 0.0
    assert bot.order_manager.sell.called                  # TP placed on real balance


def test_buying_closed_without_fill_data_balance_unknown_stays(bot):
    _buying(bot)
    bot.exchange.order = {'id': 'b1', 'status': 'closed', 'filled': 0.0}
    bot.exchange.coin_balance = None
    bot._handle_buying_state()
    assert bot.state == BotState.BUYING


def test_buying_canceled_with_partial_fill_is_a_position(bot):
    _buying(bot)
    bot.exchange.order = {'id': 'b1', 'status': 'canceled', 'filled': 2.0, 'average': 1.01, 'side': 'buy'}
    bot.exchange.coin_balance = 1.998
    bot._handle_buying_state()
    assert bot.state == BotState.IN_POSITION
    assert bot.state_data['buy_price'] == 1.01


def test_buying_canceled_without_fill_goes_idle_only_if_confirmed(bot):
    _buying(bot)
    bot.exchange.order = {'id': 'b1', 'status': 'canceled', 'filled': 0.0}
    bot.exchange.coin_balance = None
    bot._handle_buying_state()
    assert bot.state == BotState.BUYING          # deferred
    bot.exchange.coin_balance = 0.0
    bot._handle_buying_state()
    assert bot.state == BotState.IDLE


def test_buying_timeout_rechecks_fill_after_cancel(bot):
    _buying(bot, elapsed=100)
    seq = [
        {'id': 'b1', 'status': 'open', 'filled': 0.0},                       # first poll
        {'id': 'b1', 'status': 'closed', 'filled': 5.0, 'average': 1.0},    # after cancel
    ]
    bot.exchange.fetch_order = lambda *_: seq.pop(0)
    bot.exchange.coin_balance = 4.99
    bot._handle_buying_state()
    assert 'b1' in bot.exchange.cancelled
    assert bot.state == BotState.IN_POSITION


def test_single_position_invariant_fail_closed(bot):
    bot.exchange.holdings = None
    assert bot._single_position_ok('SHIB/USDT') is False
    bot.exchange.holdings = {}
    assert bot._single_position_ok('SHIB/USDT') is True
    bot.exchange.holdings = {'DOGE': 3.0}
    assert bot._single_position_ok('SHIB/USDT') is False


# ----------------------------------------------------------------- IN_POSITION
def test_in_position_stale_price_does_nothing(bot):
    _in_position(bot)
    bot.exchange.ticker = None                        # REST down, WS cache empty
    bot.exchange.coin_balance = 5.0
    bot._handle_in_position_state()
    assert bot.state == BotState.IN_POSITION
    assert bot.state_data['price_stale_count'] == 1
    _no_orders_placed(bot)


def test_in_position_unknown_tp_status_is_not_no_tp_order(bot):
    _in_position(bot)
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(1.0)
    bot.exchange.coin_balance = 5.0
    bot.exchange.order = None
    bot._handle_in_position_state()
    assert bot.state == BotState.IN_POSITION
    _no_orders_placed(bot)                            # no market exit on "unknown"


def test_in_position_no_order_id_triggers_exit(bot):
    _in_position(bot, order_id=None)
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(1.0)
    bot.exchange.coin_balance = 5.0
    bot._handle_in_position_state()
    assert bot.state == BotState.EXITING
    assert bot.order_manager.sell.called              # limit-chase first


def test_in_position_unknown_balance_never_resets(bot):
    _in_position(bot)
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(1.0)
    bot.exchange.coin_balance = None
    bot.exchange.order = {'id': 'tp1', 'status': 'open', 'side': 'sell'}
    bot._handle_in_position_state()
    assert bot.state == BotState.IN_POSITION


def test_in_position_confirmed_zero_balance_goes_idle(bot):
    _in_position(bot)
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(1.0)
    bot.exchange.coin_balance = 0.0
    bot._handle_in_position_state()
    assert bot.state == BotState.IDLE


def test_in_position_tp_filled_logs_once_even_if_idle_deferred(bot):
    _in_position(bot)
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(1.0)
    bot.exchange.order = {'id': 'tp1', 'status': 'closed', 'side': 'sell', 'filled': 5.0, 'average': 1.015}
    # balance check inside the handler sees "unknown" -> skip; IDLE deferred
    bot.exchange.coin_balance = None
    bot._handle_in_position_state()
    bot._handle_in_position_state()
    assert bot.trade_db.log_trade.call_count == 1
    assert bot.state == BotState.IN_POSITION
    bot.exchange.coin_balance = 0.0
    bot._handle_in_position_state()
    assert bot.state == BotState.IDLE
    assert bot.trade_db.log_trade.call_count == 1


# --------------------------------------------------------------------- EXITING
def test_exiting_unknown_status_before_timeout_waits(bot):
    _exiting(bot, elapsed=10)
    bot.exchange.order = None
    bot._handle_exiting_state()
    assert bot.state == BotState.EXITING
    assert not bot.trade_db.log_trade.called


def test_exiting_timeout_sold_books_exit_with_real_price(bot):
    _exiting(bot, elapsed=100)
    bot.exchange.order = {'id': 'x1', 'status': 'open', 'filled': 0.0}
    bot.exchange.coin_balance = 0.0
    bot.exchange.exchange.my_trades = [
        {'side': 'sell', 'amount': 5.0, 'price': 0.99, 'timestamp': time.time() * 1000},
    ]
    bot._handle_exiting_state()
    assert bot.state == BotState.IDLE
    args = bot.trade_db.log_trade.call_args[0]
    assert args[1] == 'sell_panic' and args[3] == 0.99


def test_exiting_timeout_still_held_retries_then_halts(bot):
    _exiting(bot, elapsed=100)
    bot.exchange.order = {'id': 'x1', 'status': 'canceled', 'filled': 0.0}
    bot.exchange.coin_balance = 5.0
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(1.0)
    for i in range(1, 4):                              # max_exit_attempts = 3
        bot._handle_exiting_state()
        assert bot.state == BotState.EXITING
        assert bot.state_data['exit_attempts'] == i
        bot.state_data['exit_time'] = time.time() - 100  # make next poll time out again
    assert bot.order_manager.sell.call_count == 3
    bot._handle_exiting_state()                        # 4th -> halt
    assert bot.state_data.get('exit_halted') is True
    bot._handle_exiting_state()                        # halted: no more orders
    assert bot.order_manager.sell.call_count == 3
    assert not bot.trade_db.log_trade.called           # nothing was ever "assumed sold"


def test_exiting_never_assumes_fill_when_balance_unknown(bot):
    _exiting(bot, elapsed=100)
    bot.exchange.order = None
    bot.exchange.coin_balance = None
    bot._handle_exiting_state()
    assert bot.state == BotState.EXITING
    assert not bot.trade_db.log_trade.called
