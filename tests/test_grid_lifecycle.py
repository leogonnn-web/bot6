"""HYDRA-NET grid lifecycle (Phase 3, audit C2).

The bug: one `order_id` for both the resting buy level and the TP sell. After a
level filled, the next buy was placed and then immediately cancelled by the TP
refresh; the TP's fill was later read as a buy fill. These tests drive the grid
through a scripted exchange and assert on the ORDER IDS the bot touches.
"""
import time
from unittest.mock import MagicMock

import pytest

from core.bot import TradingBot
from core.state_enum import BotState


# --------------------------------------------------------------------------- fakes
class ScriptedExchange:
    """Orders keyed by id; tests mutate `.orders[id]['status']` to simulate fills."""
    def __init__(self):
        self.exchange = MagicMock()
        self.exchange.markets = {'SHIB/USDT': {'limits': {'amount': {'min': 1.0}, 'cost': {'min': 1.0}},
                                              'precision': {'price': 0.000001}}}
        self.exchange.amount_to_precision = lambda s, a: str(round(a, 4))
        self.exchange.price_to_precision = lambda s, p: str(round(p, 6))
        self.orders = {}
        self.cancelled = []
        self.amended = []
        self.coin_balance = 0.0
        self.unknown_ids = set()
        self._n = 0

    # BybitClient surface used by the grid
    def quantize_order(self, symbol, amount, price):
        amt, px = round(amount, 4), round(price, 6)
        lim = self.exchange.markets[symbol]['limits']
        if amt < lim['amount']['min'] or amt * px < lim['cost']['min']:
            return None
        return amt, px

    def fetch_order(self, order_id, symbol):
        if order_id in self.unknown_ids:
            return None
        return dict(self.orders[order_id]) if order_id in self.orders else None

    def cancel_order(self, order_id, symbol):
        self.cancelled.append(order_id)
        if order_id in self.orders and self.orders[order_id]['status'] == 'open':
            self.orders[order_id]['status'] = 'canceled'
        return {}

    def amend_order(self, order_id, symbol, amount, price, side='buy'):
        self.amended.append((order_id, amount, price, side))
        self.orders[order_id].update(price=price, amount=amount)
        return {'id': order_id}

    def get_coin_balance(self, coin):
        return self.coin_balance

    def get_non_usdt_holdings(self):
        return {}

    def fetch_ticker(self, symbol):
        return None

    def fetch_ohlcv(self, *a, **k):
        return []

    # order_manager surface (wired below)
    def _place(self, side, symbol, amount, price):
        self._n += 1
        oid = f"{side}{self._n}"
        self.orders[oid] = {'id': oid, 'side': side, 'status': 'open', 'filled': 0.0,
                            'amount': amount, 'price': price, 'average': None}
        return {'id': oid}

    def fill(self, oid, price=None):
        o = self.orders[oid]
        o['status'] = 'closed'
        o['filled'] = o['amount']
        o['average'] = price or o['price']


TRADING = {
    'dry_run': False, 'slot_size': 5.0, 'base_order_size_usdt': 5.0, 'take_profit': 1.5,
    'panic_stop': 1.2, 'order_execution_timeout_sec': 60, 'hard_exit_timeout_sec': 1800,
    'fee_pct': 0.1, 'panic_slippage_pct': 0.1, 'partial_tp_activation_pct': 1.0,
    'partial_tp_size_pct': 50.0, 'move_to_breakeven': True, 'trailing_callback_pct': 1.1,
    'limit_chaser': {'enabled': True},
}
HYDRA = {'enabled': True, 'max_grid_levels': 2, 'take_profit_pct': 0.8, 'grid_distance_pct': 0.4,
         'min_order_size_usdt': 3.0, 'failing_knife_threshold': -3.0, 'grid_update_interval_sec': 3.0}


@pytest.fixture
def bot():
    b = TradingBot.__new__(TradingBot)
    ex = ScriptedExchange()
    b.exchange = ex
    b.order_manager = MagicMock()
    b.order_manager.buy.side_effect = lambda s, a, p: ex._place('buy', s, a, p)
    b.order_manager.sell.side_effect = lambda s, a, p: ex._place('sell', s, a, p)
    b.config = MagicMock()
    b.config.get_trading_config.return_value = dict(TRADING)
    b.config.config = {'trading': dict(TRADING), 'hydra_net': dict(HYDRA)}
    b.hydra_net_config = dict(HYDRA)
    b.trade_db = MagicMock()
    b.trade_db.log_trade.return_value = 7
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
    b.last_loss_time = 0.0
    b.last_grid_update = 0.0
    b.grid_update_interval = 3.0
    b.maintenance_mode = False
    b.dispatcher_enabled = False
    b.price_history = {}
    b._save_state = lambda: None
    b._apply_dispatcher_feedback = lambda *_: None
    return b


def _fresh(price):
    return {'last': price, 'bid': price * 0.999, 'ask': price * 1.001, 'timestamp': time.time()}


def _launch(bot, ask=1.0):
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(ask)
    bot._launch_grid_network('SHIB/USDT', ask, {'SHIB/USDT': {'ask': ask}})
    assert bot.state == BotState.BUYING
    return bot.state_data['grid_buy_order_id']


def _tick(bot):
    bot._handle_buying_state()


def _open_ids(ex, side):
    return [k for k, o in ex.orders.items() if o['side'] == side and o['status'] == 'open']


# ------------------------------------------------------------------ launch / level 0
def test_launch_places_entry_only(bot):
    oid = _launch(bot)
    sd = bot.state_data
    assert oid == 'buy1' and sd['current_level'] == 0 and sd['total_qty'] == 0.0
    assert sd['tp_order_id'] is None
    assert not bot.order_manager.sell.called


def test_entry_fill_places_tp_and_next_level_and_keeps_both_open(bot):
    """THE C2 REGRESSION: after level 0 fills, level 1 buy and TP sell must BOTH rest."""
    oid = _launch(bot)
    bot.exchange.fill(oid, 1.0)
    bot.exchange.coin_balance = 4.995
    _tick(bot)
    ex = bot.exchange
    sd = bot.state_data
    assert bot.state == BotState.BUYING
    assert sd['filled_levels'] == 1 and sd['current_level'] == 1
    assert sd['tp_order_id'] in ex.orders and ex.orders[sd['tp_order_id']]['side'] == 'sell'
    assert sd['grid_buy_order_id'] in ex.orders and ex.orders[sd['grid_buy_order_id']]['side'] == 'buy'
    assert sd['tp_order_id'] != sd['grid_buy_order_id']
    assert ex.cancelled == []                                   # nothing we just placed was cancelled
    assert _open_ids(ex, 'buy') == [sd['grid_buy_order_id']]
    assert _open_ids(ex, 'sell') == [sd['tp_order_id']]
    # level 1 is below entry with the Martingale size (5 * 1.5 USDT)
    lvl = ex.orders[sd['grid_buy_order_id']]
    assert lvl['price'] < 1.0 and lvl['amount'] * lvl['price'] == pytest.approx(7.5, rel=0.01)
    # TP at avg * 1.008 for the held quantity
    tp = ex.orders[sd['tp_order_id']]
    assert tp['price'] == pytest.approx(1.008, rel=1e-4)
    assert tp['amount'] == pytest.approx(4.995, rel=1e-3)
    assert sd['order_id'] == sd['tp_order_id']                 # mirror for generic code


def test_tp_fill_during_grid_is_an_exit_not_a_buy(bot):
    oid = _launch(bot)
    bot.exchange.fill(oid, 1.0)
    bot.exchange.coin_balance = 4.995
    _tick(bot)
    sd = bot.state_data
    tp_id, lvl_id = sd['tp_order_id'], sd['grid_buy_order_id']
    bot.exchange.fill(tp_id, 1.008)
    bot.exchange.coin_balance = 0.0
    _tick(bot)
    ex = bot.exchange
    assert bot.state == BotState.IDLE
    assert lvl_id in ex.cancelled                               # resting level cancelled
    assert ex.orders[lvl_id]['status'] == 'canceled'
    sides = [c[0][1] for c in bot.trade_db.log_trade.call_args_list]
    assert sides == ['buy', 'sell']                             # not a second buy
    assert bot.session_profit > 0


def test_level1_fill_refreshes_tp_only_and_places_level2(bot):
    oid = _launch(bot)
    bot.exchange.fill(oid, 1.0)
    bot.exchange.coin_balance = 4.995
    _tick(bot)
    sd = bot.state_data
    old_tp, lvl1 = sd['tp_order_id'], sd['grid_buy_order_id']
    bot.exchange.fill(lvl1)                                     # level 1 fills at its price
    bot.exchange.coin_balance = 4.995 + bot.exchange.orders[lvl1]['amount']
    _tick(bot)
    ex = bot.exchange
    assert bot.state == BotState.BUYING
    assert sd['filled_levels'] == 2 and sd['current_level'] == 2
    assert ex.cancelled == [old_tp]                             # only the OLD TP was cancelled
    assert sd['tp_order_id'] != old_tp and ex.orders[sd['tp_order_id']]['status'] == 'open'
    assert ex.orders[sd['grid_buy_order_id']]['side'] == 'buy'
    # average between the two fill prices, TP amount = whole position
    assert ex.orders[lvl1]['price'] < sd['avg_price'] < 1.0
    assert ex.orders[sd['tp_order_id']]['amount'] == pytest.approx(sd['total_qty'], rel=1e-3)


def test_grid_complete_hands_over_to_in_position(bot):
    oid = _launch(bot)
    bot.exchange.fill(oid, 1.0)
    bot.exchange.coin_balance = 4.995
    _tick(bot)
    for _ in range(2):                                          # max_grid_levels = 2
        lvl = bot.state_data['grid_buy_order_id']
        bot.exchange.fill(lvl)
        bot.exchange.coin_balance += bot.exchange.orders[lvl]['amount']
        _tick(bot)
    sd = bot.state_data
    assert bot.state == BotState.IN_POSITION
    assert sd['is_grid_active'] is False
    assert sd['grid_buy_order_id'] is None
    assert sd['order_id'] == sd['tp_order_id']
    assert sd['buy_price'] == pytest.approx(sd['avg_price'])
    assert sd['amount'] == pytest.approx(sd['total_qty'])
    assert sd['grid_end_reason'] == 'grid_complete'
    assert _open_ids(bot.exchange, 'buy') == []
    assert len(_open_ids(bot.exchange, 'sell')) == 1


def test_level_rejected_keeps_position_with_tp(bot):
    """Insufficient balance for the Martingale size must not drop the position."""
    oid = _launch(bot)
    bot.exchange.fill(oid, 1.0)
    bot.exchange.coin_balance = 4.995
    bot.order_manager.buy.side_effect = Exception("170131 Insufficient balance")
    _tick(bot)
    sd = bot.state_data
    assert bot.state == BotState.IN_POSITION
    assert sd['grid_end_reason'] == 'level_rejected'
    assert sd['tp_order_id'] and bot.exchange.orders[sd['tp_order_id']]['status'] == 'open'


def test_falling_knife_stops_averaging(bot):
    oid = _launch(bot)
    bot.exchange.fill(oid, 1.0)
    bot.exchange.coin_balance = 4.995
    _tick(bot)
    lvl = bot.state_data['grid_buy_order_id']
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(0.96)             # -4% from entry
    _tick(bot)
    assert bot.state == BotState.IN_POSITION
    assert lvl in bot.exchange.cancelled
    assert bot.state_data['grid_end_reason'] == 'falling_knife'


def test_unknown_status_changes_nothing(bot):
    oid = _launch(bot)
    bot.exchange.fill(oid, 1.0)
    bot.exchange.coin_balance = 4.995
    _tick(bot)
    sd = bot.state_data
    snapshot = (sd['tp_order_id'], sd['grid_buy_order_id'], sd['total_qty'])
    bot.exchange.unknown_ids = {sd['tp_order_id'], sd['grid_buy_order_id']}
    for _ in range(3):
        _tick(bot)
    assert bot.state == BotState.BUYING
    assert (sd['tp_order_id'], sd['grid_buy_order_id'], sd['total_qty']) == snapshot
    assert bot.exchange.cancelled == []


def test_entry_canceled_without_fill_goes_idle_when_confirmed(bot):
    oid = _launch(bot)
    bot.exchange.orders[oid]['status'] = 'canceled'
    bot.exchange.coin_balance = 0.0
    _tick(bot)
    assert bot.state == BotState.IDLE


def test_resync_reprices_level_with_buy_side_and_never_touches_tp(bot):
    oid = _launch(bot)
    bot.exchange.fill(oid, 1.0)
    bot.exchange.coin_balance = 4.995
    _tick(bot)
    sd = bot.state_data
    sd['level_price'] = 0.5                                     # pretend the level is far off -> re-price
    bot.last_grid_update = 0.0
    bot._synchronize_grid_network()
    assert len(bot.exchange.amended) == 1
    oid_amended, _, _, side = bot.exchange.amended[0]
    assert oid_amended == sd['grid_buy_order_id'] and side == 'buy'
    assert bot.exchange.cancelled == []


# ------------------------------------------------------------------------- dry-run
def test_dry_run_goes_through_the_same_lifecycle(bot):
    bot.config.get_trading_config.return_value = {**TRADING, 'dry_run': True}
    _launch(bot, ask=1.0)
    sd = bot.state_data
    assert sd['is_dry_run'] is True and sd['grid_buy_order_id'].startswith('virtual_buy')
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(0.999)           # last <= entry ask -> entry fills
    _tick(bot)
    assert sd['filled_levels'] == 1
    assert sd['tp_order_id'].startswith('virtual_sell') and sd['grid_buy_order_id'].startswith('virtual_buy')
    book = sd['virtual_orders']
    assert book[sd['tp_order_id']]['status'] == 'open' and book[sd['grid_buy_order_id']]['status'] == 'open'
    lvl_id = sd['grid_buy_order_id']
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(1.02)            # bounce above TP
    _tick(bot)
    assert bot.state == BotState.IDLE
    assert book[lvl_id]['status'] == 'canceled'                 # resting level cancelled in the virtual book too
    assert not bot.order_manager.buy.called and not bot.order_manager.sell.called


# ------------------------------------------------------------------------ quantize
def test_quantize_order_rejects_below_limits():
    from api.bybit_client import BybitClient
    c = BybitClient.__new__(BybitClient)
    c.exchange = MagicMock()
    c.exchange.markets = {'X/USDT': {'limits': {'amount': {'min': 10}, 'cost': {'min': 5.0}}}}
    c.exchange.amount_to_precision = lambda s, a: str(a)
    c.exchange.price_to_precision = lambda s, p: str(p)
    assert c.quantize_order('X/USDT', 9, 1.0) is None          # below min lot
    assert c.quantize_order('X/USDT', 10, 0.4) is None         # notional 4 < 5
    assert c.quantize_order('X/USDT', 10, 1.0) == (10.0, 1.0)
