"""Tests for Pydantic config validation (shared/config_models.py)."""
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
