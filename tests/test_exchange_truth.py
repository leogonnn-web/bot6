"""BybitClient must report exchange truth or None — never fabricated values.

Audit C1/C4/C6: the old client returned {'status': 'closed', 'filled': 0.0},
{'free': {'USDT': 0.0}} and {'last': 0.0} on errors, which downstream code read
as real fills, real zero balances and a -100% price move.
"""
import pytest

import api.bybit_client as bc
from api.bybit_client import BybitClient


class _Boom(Exception):
    pass


class FakeCcxt:
    """Minimal stand-in for ccxt.bybit with scriptable failures."""

    def __init__(self):
        self.order_calls = 0
        self.fail_orders = 0          # how many fetch_order calls raise
        self.order_error = _Boom("network")
        self.order = {'id': 'o1', 'status': 'open', 'filled': 0.0, 'side': 'buy'}
        self.closed_orders = []
        self.balance = None
        self.fail_balance = False
        self.fail_ticker = False

    def fetch_order(self, order_id, symbol, params=None):
        self.order_calls += 1
        if self.order_calls <= self.fail_orders:
            raise self.order_error
        return self.order

    def fetch_closed_orders(self, symbol, limit=5):
        return self.closed_orders

    def fetch_balance(self):
        if self.fail_balance:
            raise _Boom("balance down")
        return self.balance

    def fetch_ticker(self, symbol):
        if self.fail_ticker:
            raise _Boom("ticker down")
        return {'last': 1.23, 'bid': 1.22, 'ask': 1.24}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bc.time, 'sleep', lambda *_: None)
    c = BybitClient(api_key='k', secret='s')   # live mode (keys present)
    c.exchange = FakeCcxt()
    return c


UTA_BALANCE = {
    'free': {'USDT': 0.0},          # ccxt unified section unreliable on UTA
    'total': {'USDT': 0.0},
    'info': {'result': {'list': [{'coin': [
        {'coin': 'USDT', 'equity': '42.5', 'walletBalance': '42.5', 'availableToWithdraw': '40.0'},
        {'coin': 'SHIB', 'equity': '150000', 'walletBalance': '150000', 'availableToWithdraw': '0'},
    ]}]}},
}

CLASSIC_BALANCE = {
    'free': {'USDT': 30.0, 'DOGE': 10.0},
    'total': {'USDT': 30.0, 'DOGE': 12.0},
    'info': {},
}


# ---------------------------------------------------------------- fetch_order
def test_fetch_order_returns_none_after_3_failures(client):
    client.exchange.fail_orders = 99
    assert client.fetch_order('o1', 'SHIB/USDT') is None
    assert client.exchange.order_calls == 3


def test_fetch_order_never_fabricates_closed_status(client):
    client.exchange.fail_orders = 99
    result = client.fetch_order('o1', 'SHIB/USDT')
    assert result is None
    assert not (isinstance(result, dict) and result.get('status') == 'closed')


def test_fetch_order_recovers_on_retry(client):
    client.exchange.fail_orders = 2
    assert client.fetch_order('o1', 'SHIB/USDT') == client.exchange.order


def test_fetch_order_uses_closed_orders_on_500_limit(client):
    client.exchange.fail_orders = 99
    client.exchange.order_error = _Boom("only the last 500 orders are available")
    client.exchange.closed_orders = [{'id': 'o1', 'status': 'closed', 'filled': 5.0}]
    assert client.fetch_order('o1', 'SHIB/USDT')['filled'] == 5.0


# -------------------------------------------------------------- fetch_balance
def test_fetch_balance_live_error_is_none_not_zero(client):
    client.exchange.fail_balance = True
    assert client.fetch_balance() is None
    assert client.get_free_usdt() is None
    assert client.get_coin_balance('SHIB') is None
    assert client.get_non_usdt_holdings() is None


def test_fetch_balance_without_keys_is_virtual(monkeypatch):
    # conftest imports load .env into os.environ; make sure no real key leaks in
    # (otherwise this test would hit the live Bybit API).
    monkeypatch.delenv('BYBIT_API_KEY', raising=False)
    monkeypatch.delenv('BYBIT_API_SECRET', raising=False)
    c = BybitClient(api_key='', secret='')
    c.exchange = FakeCcxt()
    c.exchange.fail_balance = True     # would fail if it were ever called
    assert c.fetch_balance()['free']['USDT'] == 1000.0


def test_coin_balance_uta_uses_equity(client):
    client.exchange.balance = UTA_BALANCE
    assert client.get_coin_balance('SHIB') == 150000.0
    assert client.get_coin_balance('USDT') == 42.5
    assert client.get_coin_balance('DOGE') == 0.0      # fetched, simply absent


def test_free_usdt_uta_prefers_available_to_withdraw(client):
    client.exchange.balance = UTA_BALANCE
    assert client.get_free_usdt() == 40.0


def test_coin_balance_classic_account(client):
    client.exchange.balance = CLASSIC_BALANCE
    assert client.get_coin_balance('DOGE') == 12.0     # total
    assert client.get_free_usdt() == 30.0              # free


def test_non_usdt_holdings(client):
    client.exchange.balance = UTA_BALANCE
    assert client.get_non_usdt_holdings() == {'SHIB': 150000.0}
    client.exchange.balance = CLASSIC_BALANCE
    assert client.get_non_usdt_holdings() == {'DOGE': 12.0}
    client.exchange.balance = {'free': {'USDT': 5.0}, 'total': {'USDT': 5.0}, 'info': {}}
    assert client.get_non_usdt_holdings() == {}


# --------------------------------------------------------------- fetch_ticker
def test_fetch_ticker_error_is_none_not_zero_price(client):
    client.exchange.fail_ticker = True
    assert client.fetch_ticker('SHIB/USDT') is None
