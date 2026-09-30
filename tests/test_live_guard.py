"""TZ-11: real trading must be confirmed explicitly via HYDRA_LIVE_CONFIRM.

`main()` refuses to start (exit code 2) when `trading.dry_run` is false and
`exchange.demo_trading` is false unless HYDRA_LIVE_CONFIRM=yes. The check runs
before `core.bot` is imported, so a misconfigured deploy cannot even build the
bot.
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _load_main():
    """Import root-level main.py as a module named `hydra_main`."""
    spec = importlib.util.spec_from_file_location(
        'hydra_main', os.path.join(ROOT, 'main.py')
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeConfig:
    """Stand-in for shared/config.py `config` with scriptable flags."""

    def __init__(self, dry_run: bool, demo_trading: bool):
        self.config = {
            'trading': {'dry_run': dry_run},
            'exchange': {'name': 'bybit', 'demo_trading': demo_trading},
        }

    def get_trading_config(self):
        return self.config['trading']


@pytest.fixture
def main_module(monkeypatch):
    monkeypatch.delenv('HYDRA_LIVE_CONFIRM', raising=False)
    # Force a fresh `core.bot` import attempt so its presence in sys.modules is
    # a reliable signal that the guard let execution through.
    monkeypatch.delitem(sys.modules, 'core.bot', raising=False)
    return _load_main()


def _patch_config(monkeypatch, dry_run, demo_trading):
    import config as config_module
    monkeypatch.setattr(config_module, 'config', FakeConfig(dry_run, demo_trading))


def test_live_without_confirm_exits_2_before_importing_bot(monkeypatch, main_module):
    _patch_config(monkeypatch, dry_run=False, demo_trading=False)

    assert main_module.main() == 2
    assert 'core.bot' not in sys.modules


def test_live_with_wrong_confirm_value_still_blocked(monkeypatch, main_module):
    _patch_config(monkeypatch, dry_run=False, demo_trading=False)
    monkeypatch.setenv('HYDRA_LIVE_CONFIRM', 'YES')   # must be exactly "yes"

    assert main_module.main() == 2
    assert 'core.bot' not in sys.modules


def test_live_with_confirm_passes_guard(monkeypatch, main_module):
    _patch_config(monkeypatch, dry_run=False, demo_trading=False)
    monkeypatch.setenv('HYDRA_LIVE_CONFIRM', 'yes')
    started = _stub_trading_bot(monkeypatch)

    assert main_module.main() == 0
    assert started == ['run']


def test_dry_run_needs_no_confirm(monkeypatch, main_module):
    _patch_config(monkeypatch, dry_run=True, demo_trading=False)
    started = _stub_trading_bot(monkeypatch)

    assert main_module.main() == 0
    assert started == ['run']


def test_demo_trading_needs_no_confirm(monkeypatch, main_module):
    _patch_config(monkeypatch, dry_run=False, demo_trading=True)
    started = _stub_trading_bot(monkeypatch)

    assert main_module.main() == 0
    assert started == ['run']


def _stub_trading_bot(monkeypatch):
    """Replace core.bot.TradingBot with a recorder; returns the call log."""
    import types
    calls = []

    class StubBot:
        def run(self):
            calls.append('run')

    stub = types.ModuleType('core.bot')
    stub.TradingBot = StubBot
    monkeypatch.setitem(sys.modules, 'core.bot', stub)
    return calls
