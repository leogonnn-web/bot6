"""Tests for Pydantic config validation (shared/config_models.py)."""
import json
import os

import pytest
from config_models import TradingConfig, HydraNetConfig, validate_config, ConfigValidationError


class TestTradingConfig:
    def test_valid_config_passes(self, valid_trading):
        tc = TradingConfig(**valid_trading)
        assert tc.slot_size == 18.0

    def test_slot_size_zero_rejected(self, valid_trading):
        valid_trading['slot_size'] = 0
        with pytest.raises(Exception):
            TradingConfig(**valid_trading)

    def test_slot_size_negative_rejected(self, valid_trading):
        valid_trading['slot_size'] = -10
        with pytest.raises(Exception):
            TradingConfig(**valid_trading)

    def test_negative_take_profit_rejected(self, valid_trading):
        valid_trading['take_profit'] = -1.0
        with pytest.raises(Exception):
            TradingConfig(**valid_trading)

    def test_trailing_callback_negative_rejected(self, valid_trading):
        valid_trading['trailing_callback_pct'] = -0.5
        with pytest.raises(Exception):
            TradingConfig(**valid_trading)

    def test_missing_required_field_rejected(self):
        """All required fields must be present — empty dict must fail."""
        with pytest.raises(Exception):
            TradingConfig()


class TestHydraNetConfig:
    def test_valid_hydra_passes(self, valid_hydra):
        hc = HydraNetConfig(**valid_hydra)
        assert hc.max_grid_levels == 3

    def test_max_grid_levels_zero_rejected(self, valid_hydra):
        valid_hydra['max_grid_levels'] = 0
        with pytest.raises(Exception):
            HydraNetConfig(**valid_hydra)


class TestValidateConfig:
    def test_full_config_returns_dict(self, valid_full_config):
        result = validate_config(valid_full_config)
        assert isinstance(result, dict)
        assert 'trading' in result

    def test_bad_trading_raises(self, valid_full_config):
        valid_full_config['trading']['slot_size'] = -10
        with pytest.raises(ConfigValidationError):
            validate_config(valid_full_config)


class TestFullSchema:
    """TZ-07 / B-12: every section has a schema; unknown keys/sections fail."""

    @pytest.fixture
    def repo_config(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'shared', 'config.json')
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    def test_repo_config_json_passes(self, repo_config):
        out = validate_config(repo_config)
        assert set(out) == set(repo_config)
        assert out['dispatcher']['dynamic_min_score']['base'] == 1.0
        assert out['symbols'] == repo_config['symbols']

    def test_bad_type_reports_field_path(self, repo_config):
        repo_config['toxic_flow']['sweep_sell_pct_min'] = "abc"
        with pytest.raises(ConfigValidationError, match=r"toxic_flow\.sweep_sell_pct_min"):
            validate_config(repo_config)

    def test_unknown_key_in_dispatcher_rejected(self, repo_config):
        repo_config['dispatcher']['min_scor_for_entry'] = 1.0
        with pytest.raises(ConfigValidationError, match=r"dispatcher\.min_scor_for_entry"):
            validate_config(repo_config)

    def test_unknown_nested_key_rejected(self, repo_config):
        repo_config['dispatcher']['dynamic_min_score']['bais'] = 1.0
        with pytest.raises(ConfigValidationError, match=r"dynamic_min_score\.bais"):
            validate_config(repo_config)

    def test_removed_arbitrage_section_rejected(self, repo_config):
        repo_config['arbitrage'] = {'enabled': False}
        with pytest.raises(ConfigValidationError, match="unknown section 'arbitrage'"):
            validate_config(repo_config)

    def test_symbols_must_be_strings(self, repo_config):
        repo_config['symbols'] = ['BTC/USDT', 42]
        with pytest.raises(ConfigValidationError, match=r"symbols\.1"):
            validate_config(repo_config)

    def test_unset_keys_not_injected(self, valid_trading):
        """Optional sections keep only what the JSON set: `.get(k, default)` call sites see no new keys."""
        cfg = {'trading': valid_trading, 'market_conditions': {'btc_trend_detection': False}}
        out = validate_config(cfg)
        assert out['market_conditions'] == {'btc_trend_detection': False}
