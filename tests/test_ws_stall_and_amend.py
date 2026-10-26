"""B-30: a quiet price feed is not a reason to restart; a silent WS reconnects itself.
B-31: order re-pricing goes through ccxt's edit_order (amend_order never existed).

Demo stage 2, 2026-10-25 19:11: `tickers_cache` (>=95% of 127 symbols fresh within
10 s) failed three times in a row during a quiet minute -> health_status=0 ->
@WATCHDOG_RESTART@ with an open grid. The WebSocket was alive the whole time.
Same run: 'bybit' object has no attribute 'amend_order' x6 -> every limit-chase
re-peg failed and 8 of 12 exits fell to the market backstop.
"""
import asyncio
import time
from unittest.mock import MagicMock

import pytest

import api.bybit_client as bc
from api.bybit_client import BybitClient, WebSocketListener, WSStall
from core.health import HealthChecker
from tests.test_health import DummyBot  # noqa: F401


# ------------------------------------------------------------------ B-30 health
def _stale_tickers(bot, fresh=10, stale=10):
    now = time.time()
    bot.ws_tickers_cache = {f"F{i}/USDT": {'timestamp': now} for i in range(fresh)}
    bot.ws_tickers_cache.update({f"S{i}/USDT": {'timestamp': now - 60} for i in range(stale)})


def test_stale_tickers_alone_do_not_degrade_health():
    bot = DummyBot()
    _stale_tickers(bot)
    hc = HealthChecker(bot)
    hc.check_interval = 0
    for _ in range(5):
        report = hc.check()
    assert report['checks']['tickers_cache'] is False      # still reported
    assert report['overall'] is True                        # but never "degraded"
    assert hc.consecutive_fails == 0


def test_tickers_fresh_ratio_gauge_is_set(monkeypatch):
    import core.health as health_mod
    gauge = MagicMock()
    monkeypatch.setattr(health_mod.METRICS, 'tickers_fresh_ratio', gauge)
    bot = DummyBot()
    _stale_tickers(bot, fresh=3, stale=1)
    hc = HealthChecker(bot)
    hc.check()
    gauge.set.assert_called_once_with(pytest.approx(0.75))


def test_dead_feed_still_degrades_via_exchange_api():
    bot = DummyBot()
    _stale_tickers(bot, fresh=0, stale=5)
    bot.last_rest_poll_time = time.time() - 120
    hc = HealthChecker(bot)
    report = hc.check()
    assert report['checks']['exchange_api'] is False
    assert report['overall'] is False


# ------------------------------------------------------------------ B-30 listener
class _SilentWs:
    async def recv(self):
        await asyncio.sleep(3600)


class _ChattyWs:
    async def recv(self):
        return '{"topic":"tickers.BTCUSDT","data":{}}'


def test_recv_or_stall_raises_after_silence():
    ws = WebSocketListener('', '')
    ws.stall_timeout_sec = 0.05
    with pytest.raises(WSStall):
        asyncio.run(ws._recv_or_stall(_SilentWs()))


def test_recv_or_stall_returns_frames_normally():
    ws = WebSocketListener('', '')
    ws.stall_timeout_sec = 0.5
    assert asyncio.run(ws._recv_or_stall(_ChattyWs())).startswith('{"topic"')


# ------------------------------------------------------------------ B-31 amend
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bc.time, 'sleep', lambda *_: None)
    c = BybitClient(api_key='k', secret='s')
    # spec: the real ccxt.bybit has edit_order and no amend_order
    c.exchange = MagicMock(spec=['edit_order', 'amount_to_precision', 'price_to_precision'])
    c.exchange.amount_to_precision.side_effect = lambda s, a: str(a)
    c.exchange.price_to_precision.side_effect = lambda s, p: str(p)
    return c


def test_amend_uses_ccxt_edit_order(client):
    client.exchange.edit_order.return_value = {'id': 'o1'}
    res = client.amend_order('o1', 'LIT/USDT', 4.69, 3.18, side='sell')
    assert res == {'id': 'o1'}
    client.exchange.edit_order.assert_called_once_with('o1', 'LIT/USDT', 'limit', 'sell', 4.69, 3.18)


def test_amend_retries_rate_limit_then_succeeds(client):
    client.exchange.edit_order.side_effect = [Exception('too many requests'), {'id': 'o1'}]
    assert client.amend_order('o1', 'LIT/USDT', 4.69, 3.18, side='sell') == {'id': 'o1'}
    assert client.exchange.edit_order.call_count == 2


def test_amend_non_rate_limit_error_is_none(client):
    client.exchange.edit_order.side_effect = Exception('order not found')
    assert client.amend_order('o1', 'LIT/USDT', 4.69, 3.18, side='sell') is None
    assert client.exchange.edit_order.call_count == 1
