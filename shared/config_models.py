"""
Pydantic v2 schemas for HYDRA Trading Bot configuration.

Design notes
------------
* Goal: catch type errors / out-of-range values BEFORE the trading loop starts,
  so the bot fails fast with a clear log message instead of crashing later
  inside a hot path with cryptic stack traces.
* Scope: only `trading` and `hydra_net` sections are validated, as requested
  by the spec. Everything else (indicators, scanner, market_conditions, ...)
  is passed through untouched.
* `extra='allow'` is critical: the real JSON has ~25+ fields per section, but
  the spec only requires us to type-check a handful. Forbidding extras would
  break every existing caller that relies on `trading_config.get('foo', def)`.
* The validators return *dicts* (via `model_dump`) to keep the existing
  Config public API (`get_trading_config()` returns dict) unchanged.

Public entry point
------------------
`validate_config(raw_config: dict) -> dict`
  - Returns a NEW dict with the same shape as `raw_config`, but with
    `trading` and `hydra_net` re-emitted from validated Pydantic models.
  - Raises `ConfigValidationError` (subclass of ValueError) on failure.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigValidationError(ValueError):
    """Raised when config fails Pydantic validation."""


class TradingConfig(BaseModel):
    """Schema for the `trading` section.

    Only fields with strict semantic constraints are listed here. Anything
    else from the JSON passes through via `extra='allow'`.
    """

    model_config = ConfigDict(extra='allow')

    # Spec-required fields
    slot_size: float = Field(gt=0.0, description="USDT per slot, must be > 0")
    max_trades_per_day: int = Field(ge=0, le=100_000, description="Daily trade cap")
    trailing_callback_pct: float = Field(ge=0.0, le=100.0, description="Trailing callback %")

    # Additional safety-critical fields
    take_profit: float = Field(gt=0.0, description="Take-profit %, must be > 0")
    panic_stop: float = Field(gt=0.0, description="Panic stop %, must be > 0")
    drop_threshold: float = Field(ge=0.0, description="Drop threshold %, >= 0")
    dry_run: bool = Field(description="Dry run flag")
    order_execution_timeout_sec: int = Field(gt=0, description="Order timeout in seconds")

    partial_tp_activation_pct: float = Field(ge=0.0, description="Partial TP activation %")
    partial_tp_size_pct: float = Field(ge=0.0, le=100.0, description="Partial TP size %")
    move_to_breakeven: bool = Field(description="Move to breakeven flag")

    # cooldown_after_loss_minutes can be 0
    cooldown_after_loss_minutes: int = Field(ge=0, description="Cooldown after loss in minutes")

    # Optional but type-checked when present
    breakeven_timeout_sec: Optional[int] = Field(default=None, description="Breakeven timeout (None = adaptive)")
    block_night_trading: Optional[bool] = Field(default=False)
    allowed_hours: Optional[List[int]] = Field(default=None, description="Allowed trading hours 0-23")


class HydraNetConfig(BaseModel):
    """Schema for the `hydra_net` (Martingale grid) section."""

    model_config = ConfigDict(extra='allow')

    enabled: bool = Field(description="Master switch for HYDRA-NET grid mode")

    # Spec-required strict checks
    max_grid_levels: int = Field(ge=1, le=5, description="Max Martingale levels (1..5)")
    grid_distance_pct: float = Field(ge=0.0, description="Base grid distance %, strictly float >= 0")

    # Additional grid-critical fields
    dump_threshold: float = Field(le=0.0, description="Dump trigger %, must be <= 0 (negative)")
    min_rvol: float = Field(ge=0.0, description="Minimum relative volume")
    grid_update_interval_sec: float = Field(gt=0.0, description="Grid sync interval, > 0")
    take_profit_pct: float = Field(gt=0.0, description="TP %, must be > 0")
    min_order_size_usdt: float = Field(gt=0.0, description="Minimum order size in USDT")


def validate_config(raw_config: dict) -> dict:
    """Validate `trading` and `hydra_net` sections of a fully-merged config dict.

    Args:
        raw_config: The dict produced after default-config + JSON deep-merge.

    Returns:
        A dict identical to ``raw_config`` except that ``trading`` and
        ``hydra_net`` are re-emitted from the validated Pydantic models.
        Existing call sites (which use ``dict.get(key, default)``) keep working
        unchanged.

    Raises:
        ConfigValidationError: aggregated, human-readable message containing
        the offending section, field path, and reason.
    """
    if not isinstance(raw_config, dict):
        raise ConfigValidationError(f"config must be a dict, got {type(raw_config).__name__}")

    out = dict(raw_config)
    errors: list[str] = []

    trading_raw = raw_config.get('trading')
    if isinstance(trading_raw, dict):
        try:
            out['trading'] = TradingConfig.model_validate(trading_raw).model_dump()
        except ValidationError as exc:
            errors.append(_format_pydantic_errors('trading', exc))
    else:
        errors.append("section 'trading' is missing or not a dict")

    hydra_raw = raw_config.get('hydra_net')
    if isinstance(hydra_raw, dict):
        try:
            out['hydra_net'] = HydraNetConfig.model_validate(hydra_raw).model_dump()
        except ValidationError as exc:
            errors.append(_format_pydantic_errors('hydra_net', exc))
    # NOTE: `hydra_net` is optional — bot can run without it (grid disabled).
    # Only validate when present.

    if errors:
        raise ConfigValidationError("\n".join(errors))

    return out


def _format_pydantic_errors(section: str, exc: ValidationError) -> str:
    """Render Pydantic ValidationError into a multi-line, log-friendly string."""
    lines = [f"section '{section}' has {exc.error_count()} validation error(s):"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get('loc', ()))
        msg = err.get('msg', '')
        typ = err.get('type', '')
        bad_val = err.get('input', '<n/a>')
        lines.append(f"  - {section}.{loc}: {msg} (type={typ}, got={bad_val!r})")
    return "\n".join(lines)
