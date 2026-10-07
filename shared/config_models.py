"""
Pydantic v2 schemas for HYDRA Trading Bot configuration.

Design notes
------------
* Goal: catch type errors / out-of-range values BEFORE the trading loop starts,
  so the bot fails fast with a clear log message instead of crashing later
  inside a hot path with cryptic stack traces.
* Scope: every top-level section has a model (`_SECTIONS`); an unknown
  top-level section is an error. All sections except `trading`/`hydra_net`
  use `extra='forbid'`, so a typo in a key fails at startup.
* `trading`/`hydra_net` keep `extra='allow'`: they still carry ~25
  undocumented fields read via `trading_config.get('foo', def)`; switching
  them to forbid needs an inventory first (BACKLOG B-12).
* The validators return *dicts* (via `model_dump`) to keep the existing
  Config public API (`get_trading_config()` returns dict) unchanged.

Public entry point
------------------
`validate_config(raw_config: dict) -> dict`
  - Returns a NEW dict with the same shape as `raw_config`, every section
    re-emitted from its validated Pydantic model.
  - Raises `ConfigValidationError` (subclass of ValueError) on failure.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


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


class _Strict(BaseModel):
    """Base for fully-specified sections: an unknown key is a typo, not a feature."""

    model_config = ConfigDict(extra='forbid')


class ExchangeConfig(_Strict):
    """Schema for the `exchange` section.

    `demo_trading` selects Bybit Demo Trading (api-demo.bybit.com): the live
    order/balance code paths run against a virtual balance. Keys are then read
    from BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET.
    """

    name: str = Field(default='bybit', description="Exchange id (ccxt)")
    demo_trading: bool = Field(default=False, description="Use Bybit Demo Trading endpoint")


class WebsocketConfig(_Strict):
    enabled: bool = True
    reconnect_interval_sec: float = Field(default=5, gt=0)
    max_reconnect_attempts: int = Field(default=10, ge=0)


class MetricsConfig(_Strict):
    port: int = Field(default=9090, ge=1, le=65535)


class CacheConfig(_Strict):
    ticker_ttl: float = Field(default=2, ge=0)
    balance_ttl: float = Field(default=5, ge=0)
    ohlcv_ttl: float = Field(default=10, ge=0)


class ApiRetryConfig(_Strict):
    max_retries: int = Field(default=3, ge=0)
    retry_delay: float = Field(default=0.5, ge=0)
    backoff_factor: float = Field(default=2.0, ge=1.0)


class ScannerConfig(_Strict):
    enabled: bool = True
    file: str = 'hot_symbols.txt'
    cache_ttl: float = Field(default=600, ge=0)
    use_priority: bool = True


class IndicatorsConfig(_Strict):
    enabled: bool = True
    rsi_period: int = Field(default=14, ge=1)
    rsi_oversold: float = Field(default=30, ge=0, le=100)
    rsi_overbought: float = Field(default=70, ge=0, le=100)
    ema_fast: int = Field(default=9, ge=1)
    ema_slow: int = Field(default=21, ge=1)
    macd_fast: int = Field(default=12, ge=1)
    macd_slow: int = Field(default=26, ge=1)
    macd_signal: int = Field(default=9, ge=1)
    min_signal_score: float = Field(default=2, ge=0)


class StochasticConfig(_Strict):
    enabled: bool = True
    period: int = Field(default=14, ge=1)
    k_smooth: int = Field(default=3, ge=1)
    d_smooth: int = Field(default=3, ge=1)


class SignalWeights(_Strict):
    rsi: float = 2.0
    ema: float = 2.0
    macd: float = 1.0
    stochastic: float = 3.0
    ichimoku: float = 2.0
    volume_poc: float = 1.5


class SignalOptimizerConfig(_Strict):
    min_confidence_threshold: float = Field(default=50, ge=0, le=100)
    strong_buy_threshold: Optional[float] = Field(default=None, ge=0, le=100)
    use_conflict_detection: bool = True
    volatility_adjusted: bool = True
    signal_weights: SignalWeights = Field(default_factory=SignalWeights)


class BtcFilterConfig(_Strict):
    enabled: bool = False
    symbol: str = 'BTC/USDT'
    lookback_minutes: int = Field(default=5, ge=1)
    max_drop_pct: float = Field(default=0.5, ge=0)


class MarketConditionsConfig(_Strict):
    btc_trend_detection: bool = True
    volatility_adjustment: bool = True
    trading_session_adjustment: bool = False
    high_volatility_threshold: float = Field(default=1.5, ge=0)
    low_volatility_threshold: float = Field(default=0.7, ge=0)
    btc_correlation_filter: bool = False
    btc_correlation_period: int = Field(default=24, ge=1)
    btc_correlation_threshold: float = Field(default=0.5, ge=-1.0, le=1.0)
    btc_filter: Optional[BtcFilterConfig] = None


class ToxicFlowConfig(_Strict):
    """Keys mirror `core.toxic_flow.DEFAULTS` plus the `enabled` switch."""

    enabled: bool = True
    sweep_window_sec: float = Field(default=3.0, gt=0)
    sweep_min_trades: int = Field(default=10, ge=0)
    sweep_sell_pct_min: float = Field(default=0.85, ge=0, le=1)
    sweep_buy_pct_max: float = Field(default=0.10, ge=0, le=1)
    sweep_consec_down_min: int = Field(default=3, ge=0)
    large_print_size_ratio: float = Field(default=5.0, gt=0)
    large_print_warmup: int = Field(default=100, ge=0)
    cooldown_sec: float = Field(default=600.0, ge=0)
    unlock_max_trades: int = Field(default=30, ge=0)
    require_obi_unlock: bool = False


class DynamicMinScoreConfig(_Strict):
    enabled: bool = False
    base: float = 1.0
    btc_bearish_penalty: float = 0.5
    btc_crash_penalty: float = 1.0
    flet_bonus: float = -0.3


class DispatcherConfig(_Strict):
    enabled: bool = True
    feedback_loop: bool = False
    min_score_for_entry: float = 1.0
    learning_rate: float = Field(default=0.02, ge=0)
    dynamic_min_score: Optional[DynamicMinScoreConfig] = None


class ScannerProtectionConfig(_Strict):
    btc_crash_3m_pct: float = Field(default=-1.5, le=0)
    btc_crash_15m_pct: float = Field(default=-2.5, le=0)
    atr_spike_multiplier: float = Field(default=3.5, gt=0)
    min_orderbook_liquidity_pct: float = Field(default=40.0, ge=0, le=100)
    cooldown_freeze_minutes: float = Field(default=30, ge=0)


_SYMBOLS = TypeAdapter(List[str])


# Section name -> model. `trading` / `hydra_net` keep `extra='allow'` (they
# still carry undocumented fields); every other section is strict. A
# top-level key absent from this map is rejected as an unknown section.
_SECTIONS: dict[str, type[BaseModel]] = {
    'trading': TradingConfig,
    'hydra_net': HydraNetConfig,
    'exchange': ExchangeConfig,
    'websocket': WebsocketConfig,
    'metrics': MetricsConfig,
    'cache': CacheConfig,
    'api_retry': ApiRetryConfig,
    'scanner': ScannerConfig,
    'indicators': IndicatorsConfig,
    'stochastic': StochasticConfig,
    'signal_optimizer': SignalOptimizerConfig,
    'market_conditions': MarketConditionsConfig,
    'toxic_flow': ToxicFlowConfig,
    'dispatcher': DispatcherConfig,
    'scanner_protection': ScannerProtectionConfig,
}


def validate_config(raw_config: dict) -> dict:
    """Validate every section of a fully-merged config dict.

    Args:
        raw_config: The dict produced after default-config + JSON deep-merge.

    Returns:
        A dict with the same shape as ``raw_config``. ``trading``, ``hydra_net``
        and ``exchange`` are re-emitted in full from the validated models (as
        before); the other sections are re-emitted with only the keys that were
        actually set, so call sites relying on ``dict.get(key, default)`` see
        exactly what the JSON said, just type-coerced.

    Raises:
        ConfigValidationError: aggregated, human-readable message containing
        the offending section, field path, and reason. An unknown top-level
        section is reported as ``unknown section '<name>'``.
    """
    if not isinstance(raw_config, dict):
        raise ConfigValidationError(f"config must be a dict, got {type(raw_config).__name__}")

    out = dict(raw_config)
    errors: list[str] = []

    if not isinstance(raw_config.get('trading'), dict):
        errors.append("section 'trading' is missing or not a dict")

    for section, value in raw_config.items():
        if section == 'symbols':
            try:
                out['symbols'] = _SYMBOLS.validate_python(value)
            except ValidationError as exc:
                errors.append(_format_pydantic_errors('symbols', exc))
            continue
        model = _SECTIONS.get(section)
        if model is None:
            errors.append(f"unknown section '{section}'")
            continue
        if not isinstance(value, dict):
            if section != 'trading':
                errors.append(f"section '{section}' must be a dict, got {type(value).__name__}")
            continue
        try:
            validated = model.model_validate(value)
        except ValidationError as exc:
            errors.append(_format_pydantic_errors(section, exc))
            continue
        full = section in ('trading', 'hydra_net', 'exchange')
        out[section] = validated.model_dump(exclude_unset=not full)

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
