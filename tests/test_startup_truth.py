"""Startup facts found in the first dry-run (2026-10-11):

1. hydra_session_profit_usdt showed the whole trades.db history ($344) because
   __init__ read get_session_stats() without session_start_ts, and a restart
   reset the in-memory counter while the gauge kept the stale value.
2. One delisted symbol (TON/USDT) made the REST fetch_tickers batch fail and its
   WS subscription was dropped silently.
"""
import time
from unittest.mock import MagicMock

from core.bot import TradingBot
from core.scanner import DynamicSymbolManager, ScannerIntegration
from core.state_enum import BotState


def _bot(tmp_path, session_start_ts, db_profit_since):
    b = TradingBot.__new__(TradingBot)
    b.config = MagicMock()
    b.config.get_trading_config.return_value = {'session_start_ts': session_start_ts, 'dry_run': True}
    b.trade_db = MagicMock()
    b.trade_db.get_session_stats.side_effect = lambda since=0: {'session_profit': db_profit_since(since)}
    b.state_file = str(tmp_path / 'hydra_state.json')
    b.exchange = MagicMock()
    b._save_state = lambda: None
    return b


def test_session_profit_is_scoped_to_session_start(tmp_path):
    start = 1_780_000_000.0
    # DB: 344.94 total, 1.25 since session start
    b = _bot(tmp_path, start, lambda since: 1.25 if since >= start else 344.94)
    assert b._session_profit_from_db() == 1.25
    b.trade_db.get_session_stats.assert_called_with(start)


def test_load_state_does_not_restore_profit_from_file(tmp_path):
    import json
    b = _bot(tmp_path, 0, lambda since: 0.0)
    (tmp_path / 'hydra_state.json').write_text(json.dumps({
        'state': 'IDLE', 'state_data': {}, 'state_entry_time': time.time(),
        'session_profit': 999.0, 'saved_at': time.time(),
    }))
    b._load_state()
    assert b.state == BotState.IDLE
    assert b.session_profit == 0.0          # file value ignored; DB is the source


def test_idle_scanning_flips_do_not_persist_state(tmp_path):
    """Regression for hydra_state.json being rewritten every second."""
    b = TradingBot.__new__(TradingBot)
    saves = []
    b._save_state = lambda: saves.append(1)
    b._last_state = BotState.IDLE
    b.state_entry_time = 0.0
    # replicate the run-loop fragment
    def step(new_state):
        b.state = new_state
        if b.state != b._last_state:
            prev = b._last_state
            b._last_state = b.state
            if not ({prev, b.state} <= {BotState.IDLE, BotState.SCANNING}):
                b._save_state()

    step(BotState.SCANNING); step(BotState.IDLE); step(BotState.SCANNING)
    assert saves == []                       # idle/scanning churn: no writes
    step(BotState.BUYING); step(BotState.IN_POSITION); step(BotState.IDLE)
    assert len(saves) == 3                   # position-bearing transitions persist


# ----------------------------------------------------------------- symbol universe
def _manager(symbols):
    scanner = MagicMock(spec=ScannerIntegration)
    scanner.read_symbols.return_value = []
    return DynamicSymbolManager(symbols, scanner)


def test_delisted_base_symbol_is_dropped():
    m = _manager(['BTC/USDT', 'TON/USDT', 'ETH/USDT'])
    dropped = m.set_known_markets({'BTC/USDT', 'ETH/USDT', 'SOL/USDT'})
    assert dropped == ['TON/USDT']
    assert set(m.get_symbols(refresh_scanner=False)) == {'BTC/USDT', 'ETH/USDT'}


def test_delisted_scanner_symbol_is_filtered_too():
    m = _manager(['BTC/USDT'])
    m.scanner.read_symbols.return_value = ['TON/USDT', 'SOL/USDT']
    m.set_known_markets({'BTC/USDT', 'SOL/USDT'})
    assert set(m.get_symbols()) == {'BTC/USDT', 'SOL/USDT'}


def test_no_markets_means_no_filtering():
    m = _manager(['BTC/USDT', 'TON/USDT'])
    assert set(m.get_symbols(refresh_scanner=False)) == {'BTC/USDT', 'TON/USDT'}


def test_apply_market_universe_skips_inactive_and_non_spot():
    b = TradingBot.__new__(TradingBot)
    b.symbol_manager = _manager(['BTC/USDT', 'OLD/USDT', 'PERP/USDT'])
    b._apply_market_universe({
        'BTC/USDT': {'spot': True, 'active': True},
        'OLD/USDT': {'spot': True, 'active': False},
        'PERP/USDT': {'spot': False, 'active': True},
    })
    assert b.symbol_manager.base_symbols == {'BTC/USDT'}


def test_apply_market_universe_empty_markets_changes_nothing():
    b = TradingBot.__new__(TradingBot)
    b.symbol_manager = _manager(['BTC/USDT', 'TON/USDT'])
    b._apply_market_universe({})
    assert b.symbol_manager.base_symbols == {'BTC/USDT', 'TON/USDT'}
