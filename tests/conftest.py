"""Shared fixtures for HYDRA Trading Bot test suite."""
import json
import os
import sys
import pytest

# Ensure shared/ and src/ are importable
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SHARED = os.path.join(ROOT, 'shared')
SRC = os.path.join(ROOT, 'src')
for p in (SRC, SHARED):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# .env isolation: tests must never see real exchange credentials.
# shared/config.py binds `load_dotenv` at import time and calls it in
# Config.__init__ (including the module-level `config = Config()`). conftest
# is imported before any test module, so stubbing it here guarantees the
# local .env file is never loaded into os.environ during the test session.
# ---------------------------------------------------------------------------
def _load_dotenv_stub(*args, **kwargs):
    return False

try:
    import dotenv
    dotenv.load_dotenv = _load_dotenv_stub
except ImportError:
    pass


@pytest.fixture(scope='session', autouse=True)
def _isolate_env_from_dotenv():
    """Purge exchange credentials from os.environ for the whole test session.

    Covers keys present in the real shell environment and anything that may
    have been loaded before the load_dotenv stub above took effect.
    """
    # If shared/config.py was somehow imported before this conftest, it holds
    # a real `load_dotenv` binding — replace it so late Config() instantiations
    # cannot re-populate os.environ from the local .env file.
    config_mod = sys.modules.get('config')
    if config_mod is not None:
        config_mod.load_dotenv = _load_dotenv_stub

    saved = {}
    for key in list(os.environ):
        if key.endswith(('_API_KEY', '_API_SECRET')) or key in ('HEALTHCHECK_URL', 'GRAFANA_PASSWORD'):
            saved[key] = os.environ.pop(key)
    yield
    os.environ.update(saved)


# ---------------------------------------------------------------------------
# Minimal valid config dicts (mirrors shared/config.json structure)
# ---------------------------------------------------------------------------
VALID_TRADING = {
    'slot_size': 18.0,
    'max_trades_per_day': 2500,
    'trailing_callback_pct': 1.1,
    'take_profit': 1.5,
    'panic_stop': 2.0,
    'drop_threshold': 0.65,
    'dry_run': True,
    'order_execution_timeout_sec': 60,
    'partial_tp_activation_pct': 1.0,
    'partial_tp_size_pct': 50.0,
    'move_to_breakeven': True,
    'cooldown_after_loss_minutes': 0,
}

VALID_HYDRA = {
    'enabled': True,
    'max_grid_levels': 3,
    'grid_distance_pct': 0.4,
    'dump_threshold': -0.75,
    'min_rvol': 1.35,
    'grid_update_interval_sec': 3.0,
    'take_profit_pct': 0.8,
    'min_order_size_usdt': 5.0,
}


@pytest.fixture
def valid_trading():
    """Return a copy of a valid trading config dict."""
    return dict(VALID_TRADING)


@pytest.fixture
def valid_hydra():
    """Return a copy of a valid hydra_net config dict."""
    return dict(VALID_HYDRA)


@pytest.fixture
def valid_full_config(valid_trading, valid_hydra):
    """Full config dict with trading + hydra_net sections."""
    return {'trading': valid_trading, 'hydra_net': valid_hydra}


@pytest.fixture
def tmp_json(tmp_path):
    """Factory fixture: writes a dict to a temp JSON file and returns its path."""
    def _write(data: dict, filename: str = 'test.json') -> str:
        fp = tmp_path / filename
        fp.write_text(json.dumps(data), encoding='utf-8')
        return str(fp)
    return _write
