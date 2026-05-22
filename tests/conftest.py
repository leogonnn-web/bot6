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
