"""Dust that cannot be sold is not a position; a filled TP is always booked.

Stage 2 demo, 2026-10-20 (B-27): the TP on NEAR filled and left 0.01599 NEAR
(~$0.08) - above the min lot (0.01) but below the min order value ($5).
_transition_to_idle adopted it as a position, every tick then ran
@NO_TP_ORDER@ -> panic sell -> 170140 "Order value exceeded lower limit",
2295 times in an hour, no further trades.

Same run, LIT and GRASS: the TP filled, the remainder was below the min lot, and
the balance check ran before the order check -> IDLE via coin_balance_zero with
no log_trade (two exits missing from trades.db).
"""
import time
from unittest.mock import MagicMock

import pytest

from core.state_enum import BotState
from tests.test_state_unknown import bot, _in_position, _fresh, TRADING  # noqa: F401

NEAR = 'NEAR/USDT'
NEAR_MARKET = {'limits': {'amount': {'min': 0.01}, 'cost': {'min': 5.0}}, 'precision': {'amount': 0.01}}


def _near(bot, price=4.95):
    bot.exchange.exchange.markets = {NEAR: NEAR_MARKET}
    bot.ws_tickers_cache[NEAR] = _fresh(price)
    bot.exchange.exchange.amount_to_precision = lambda s, a: str(int(a * 100) / 100)


# ------------------------------------------------------------------ threshold
def test_dust_threshold_is_min_notional_when_price_known(bot):
    _near(bot)
    assert bot._dust_threshold(NEAR) == pytest.approx(5.0 / 4.95)


def test_dust_threshold_falls_back_to_min_lot_without_price(bot):
    _near(bot)
    bot.ws_tickers_cache = {}
    bot.exchange.ticker = None
    assert bot._dust_threshold(NEAR) == 0.01


def test_dust_threshold_price_lookup_does_not_touch_ws_cache(bot):
    _near(bot)
    bot.ws_tickers_cache = {}
    bot.exchange.ticker = {'last': 4.95}
    assert bot._dust_threshold(NEAR) == pytest.approx(5.0 / 4.95)
    assert NEAR not in bot.ws_tickers_cache          # health counts stale entries there


def test_unknown_market_is_never_a_position(bot):
    _near(bot)
    assert bot._dust_threshold('DELISTED/USDT') == float('inf')


def test_single_position_ignores_unsellable_dust(bot):
    _near(bot)
    bot.exchange.holdings = {'NEAR': 0.01599, 'DELISTED': 3.0}
    assert bot._single_position_ok('SOL/USDT') is True
    bot.exchange.holdings = {'NEAR': 1.3}
    assert bot._single_position_ok('SOL/USDT') is False


# ------------------------------------------------------------------ B-27 loop
def test_tp_fill_leaving_unsellable_dust_goes_idle_not_adopted(bot):
    _in_position(bot, buy_price=4.90, amount=1.22)
    bot.state_data['symbol'] = NEAR
    _near(bot)
    bot.exchange.order = {'id': 'tp1', 'status': 'closed', 'side': 'sell', 'filled': 1.21, 'average': 4.95}
    bot.exchange.coin_balance = 0.01599
    bot._handle_in_position_state()
    assert bot.state == BotState.IDLE
    assert bot.trade_db.log_trade.call_count == 1
    assert bot.session_profit > 0
    assert not bot.order_manager.sell.called and not bot.order_manager.market_sell.called


def test_adopted_dust_without_order_exits_once_without_sending_orders(bot):
    """State restored with the NEAR ghost (order_id=None, amount=dust): one tick -> IDLE."""
    _in_position(bot, order_id=None, buy_price=4.90, amount=0.01599)
    bot.state_data['symbol'] = NEAR
    _near(bot)
    bot.exchange.coin_balance = 0.01599
    bot._handle_in_position_state()
    assert bot.state == BotState.IDLE
    assert not bot.order_manager.sell.called and not bot.order_manager.market_sell.called
    assert bot.exchange.cancelled == []            # no cancel(None) -> no "unknown parameter" spam


def test_panic_sell_refuses_unsellable_amount(bot):
    """Price unknown to the threshold but known to quantize: still no order, straight to IDLE."""
    _in_position(bot, order_id=None, buy_price=4.90, amount=0.01599)
    bot.state_data['symbol'] = NEAR
    bot.exchange.exchange.markets = {NEAR: NEAR_MARKET}
    bot.exchange.exchange.amount_to_precision = lambda s, a: str(a)
    bot.ws_tickers_cache[NEAR] = _fresh(4.95)
    bot.exchange.coin_balance = 0.01599
    bot._panic_sell(urgent=False, reason='no_tp_order')
    assert bot.state == BotState.IDLE
    assert not bot.order_manager.sell.called and not bot.order_manager.market_sell.called


# ------------------------------------------------------------------ TP booking
def test_tp_filled_and_balance_zero_same_tick_is_booked(bot):
    _in_position(bot)
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(1.0)
    bot.exchange.order = {'id': 'tp1', 'status': 'closed', 'side': 'sell', 'filled': 5.0, 'average': 1.015}
    bot.exchange.coin_balance = 0.0
    bot._handle_in_position_state()
    assert bot.state == BotState.IDLE
    assert bot.trade_db.log_trade.call_count == 1
    assert bot.trade_db.log_trade.call_args[0][1] == 'sell'
    assert bot.session_profit == pytest.approx(bot._calc_pnl(1.0, 1.015, 5.0))
    assert 'SHIB/USDT' in bot.symbol_cooldown


def test_zero_balance_with_unknown_order_waits_for_the_status(bot):
    _in_position(bot)
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(1.0)
    bot.exchange.order = None
    bot.exchange.coin_balance = 0.0
    bot._handle_in_position_state()
    assert bot.state == BotState.IN_POSITION
    assert not bot.trade_db.log_trade.called
    bot.exchange.order = {'id': 'tp1', 'status': 'closed', 'side': 'sell', 'filled': 5.0, 'average': 1.015}
    bot._handle_in_position_state()
    assert bot.state == BotState.IDLE
    assert bot.trade_db.log_trade.call_count == 1


def test_zero_balance_with_unknown_order_gives_up_after_30_ticks(bot):
    _in_position(bot)
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(1.0)
    bot.exchange.order = None
    bot.exchange.coin_balance = 0.0
    for _ in range(29):
        bot._handle_in_position_state()
    assert bot.state == BotState.IN_POSITION
    bot._handle_in_position_state()
    assert bot.state == BotState.IDLE


# ------------------------------------------------------------------ breakeven timeout
def test_breakeven_timeout_config_needs_no_market_data(bot):
    bot.exchange.fetch_ohlcv = MagicMock(return_value=[])
    bot.config.get_trading_config.return_value = {**TRADING, 'breakeven_timeout_sec': 900}
    assert bot._calculate_adaptive_breakeven_timeout('SHIB/USDT') == 900
    assert not bot.exchange.fetch_ohlcv.called


def test_breakeven_timeout_atr_is_cached_between_ticks(bot):
    _in_position(bot)
    bot.ws_tickers_cache['SHIB/USDT'] = _fresh(1.0)
    candles = [[0, 1.0, 1.02, 0.98, 1.0, 10]] * 20
    bot.exchange.fetch_ohlcv = MagicMock(return_value=candles)
    t1 = bot._calculate_adaptive_breakeven_timeout('SHIB/USDT')
    t2 = bot._calculate_adaptive_breakeven_timeout('SHIB/USDT')
    assert t1 == t2 and t1 in (300, 900, 1200, 1800, 2400)
    assert bot.exchange.fetch_ohlcv.call_count == 1


# ------------------------------------------------------------------ entry guard
def test_entry_sellable_rejects_slot_at_min_notional(bot):
    _near(bot)
    assert bot._entry_sellable(NEAR, 1.01, 4.95) is False      # $5.00 -> 1.00 after fee = $4.95
    assert bot._entry_sellable(NEAR, 1.21, 4.95) is True       # $5.99 -> 1.20 = $5.94


def test_enter_trade_skips_unsellable_slot(bot):
    _near(bot)
    bot.config.get_trading_config.return_value = {**TRADING, 'slot_size': 5.0}
    bot.exchange.holdings = {}
    bot._enter_trade(NEAR, 4.95, {NEAR: {'ask': 4.95}})
    assert bot.state == BotState.IDLE
    assert not bot.order_manager.buy.called
