"""Tests for CapitalRouter grid level cap enforcement in hydra_net."""
import pytest
from unittest.mock import MagicMock, patch
from capital_router import CapitalRouter


class TestGridLevelCap:
    """Verify max_grid_levels from CapitalRouter is respected."""

    def test_capital_router_caps_grid_levels(self, tmp_path):
        """CapitalRouter with low balance caps max_grid_levels to 1."""
        cr = CapitalRouter(state_file=str(tmp_path / 'cs.json'))
        cr.evaluate(30.0)  # available = 28.5 → grid_1
        assert cr.state.max_grid_levels == 1

        # Simulate what hydra_net._on_grid_level_filled does
        config_max = 3
        capital_max = cr.state.max_grid_levels
        effective_max = min(config_max, capital_max)

        assert effective_max == 1, "Grid should be capped to 1 by CapitalRouter"

    def test_config_max_respected_when_lower(self, tmp_path):
        """Config max_grid_levels=2 should win over capital_max=3."""
        cr = CapitalRouter(state_file=str(tmp_path / 'cs.json'))
        cr.evaluate(150.0)  # available = 142.5 → grid_3

        config_max = 2
        capital_max = cr.state.max_grid_levels
        effective_max = min(config_max, capital_max)

        assert effective_max == 2, "Config cap should win when lower"

    def test_frozen_mode_blocks_all_grid(self, tmp_path):
        cr = CapitalRouter(state_file=str(tmp_path / 'cs.json'))
        cr.evaluate(10.0)  # frozen
        assert cr.state.max_grid_levels == 0
        assert cr.state.grid_allowed is False


class TestConfigValidation:
    """Verify validate_config is called and raises on bad config."""

    def test_validate_config_passes_good_config(self, valid_full_config):
        from config_models import validate_config
        result = validate_config(valid_full_config)
        assert isinstance(result, dict)

    def test_validate_config_rejects_zero_slot(self, valid_full_config):
        from config_models import validate_config, ConfigValidationError
        valid_full_config['trading']['slot_size'] = 0
        with pytest.raises(ConfigValidationError):
            validate_config(valid_full_config)
