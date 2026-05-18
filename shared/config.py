"""
Configuration management with environment support and new v16.0 features
Supports: Trading params, Indicators, Scanner, ATR stops
"""
import json
import os
from dotenv import load_dotenv

from paths import DEFAULT_CONFIG, ENV_FILE


class Config:
    """Configuration management with environment support"""
    
    def __init__(self, config_file: str = None):
        load_dotenv(ENV_FILE)
        
        self.config_file = config_file or DEFAULT_CONFIG
        self.config = self._load_config()
    
    def _load_config(self) -> dict:
        """Load configuration from JSON file"""
        default_config = {
            "trading": {
                "slot_size": 18.0,
                "entry_threshold": 0.75,
                "drop_threshold": 0.65,
                "panic_stop": 2.0,
                "stop_loss_total": 12.0,
                "timeout_breakeven": 1200,
                "min_exchange_limit": 5.2,
                "volatility_min": 0.85,
                "spread_max": 0.10,
                "use_dynamic_stops": True,
                "atr_multiplier": 1.5,
                "min_stop_pct": 1.0,
                "order_execution_timeout_sec": 60,
                "block_night_trading": False,
                "allowed_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23],
                "dry_run": False,
                "log_dry_run_trades": True,
                "take_profit": 1.5,
                "partial_tp_activation_pct": 1.0,
                "partial_tp_size_pct": 50.0,
                "move_to_breakeven": True,
                "trailing_callback_pct": 0.5
            },
            "symbols": ["NOT/USDT", "TON/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT"],
            "exchange": {"name": "bybit"},
            "api_retry": {"max_retries": 3, "retry_delay": 0.5, "backoff_factor": 2.0},
            "cache": {"ticker_ttl": 2, "balance_ttl": 5, "ohlcv_ttl": 10},
            "indicators": {
                "enabled": True,
                "rsi_period": 14,
                "rsi_oversold": 30,
                "rsi_overbought": 70,
                "ema_fast": 9,
                "ema_slow": 21,
                "macd_fast": 12,
                "macd_slow": 26,
                "macd_signal": 9,
                "min_signal_score": 2
            },
            "stochastic": {
                "enabled": True,
                "period": 14,
                "k_smooth": 3,
                "d_smooth": 3
            },
            "scanner": {
                "enabled": True,
                "file": "hot_symbols.txt",
                "cache_ttl": 600,
                "use_priority": True
            }
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    loaded = json.load(f)
                    self._deep_merge(default_config, loaded)
            except Exception as e:
                print(f"Warning: Could not load config file: {e}")
        
        return default_config
    
    def _deep_merge(self, base: dict, updates: dict) -> None:
        """Deep merge updates into base dict"""
        for key, value in updates.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
    
    def get_trading_config(self) -> dict:
        """Get trading parameters"""
        return self.config['trading']
    
    def get_symbols(self) -> list:
        """Get trading symbols"""
        return self.config['symbols']
    
    def get_api_config(self) -> dict:
        """Get API configuration"""
        return {
            'apiKey': os.getenv('BYBIT_API_KEY', ''),
            'secret': os.getenv('BYBIT_API_SECRET', ''),
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'recvWindow': 10000
            }
        }
    
    def get_retry_config(self) -> dict:
        """Get retry configuration"""
        return self.config['api_retry']
    
    def get_cache_config(self) -> dict:
        """Get cache configuration"""
        return self.config['cache']
    
    def get_indicator_config(self) -> dict:
        """Get technical indicators configuration"""
        return self.config.get('indicators', {
            "enabled": True,
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "ema_fast": 9,
            "ema_slow": 21,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "min_signal_score": 2
        })
    
    def get_stochastic_config(self) -> dict:
        """Get Stochastic Oscillator configuration (NEW in v16.0)"""
        return self.config.get('stochastic', {
            "enabled": True,
            "period": 14,
            "k_smooth": 3,
            "d_smooth": 3
        })
    
    def get_scanner_config(self) -> dict:
        """Get Scanner configuration (NEW in v16.0)"""
        return self.config.get('scanner', {
            'enabled': True,
            'file': 'hot_symbols.txt',
            'cache_ttl': 600,
            'use_priority': True
        })
    
    def are_indicators_enabled(self) -> bool:
        """Check if technical indicators are enabled"""
        return self.get_indicator_config().get('enabled', True)
    
    def is_stochastic_enabled(self) -> bool:
        """Check if Stochastic is enabled (NEW in v16.0)"""
        return self.get_stochastic_config().get('enabled', True)
    
    def use_dynamic_stops(self) -> bool:
        """Check if dynamic ATR stops are enabled (NEW in v16.0)"""
        return self.get_trading_config().get('use_dynamic_stops', False)

    def get_take_profit_pct(self) -> float:
        """Take-profit %: explicit take_profit or entry_threshold (v16/v17)."""
        trading = self.get_trading_config()
        if 'take_profit' in trading:
            return float(trading['take_profit'])
        return float(trading['entry_threshold'])

    def get_min_equity_usd(self) -> float:
        """Minimum USDT equity before bot stops (was stop_loss_total)."""
        trading = self.get_trading_config()
        if 'min_equity_usd' in trading:
            return float(trading['min_equity_usd'])
        return float(trading.get('stop_loss_total', 0.0))

    def get_falling_knife_threshold_pct(self) -> float:
        trading = self.get_trading_config()
        return float(trading.get('falling_knife_threshold_pct', 3.0))

    def get_signal_optimizer_config(self) -> dict:
        """signal_optimizer section from config_v17.json (defaults match legacy hardcoding)."""
        return self.config.get('signal_optimizer', {
            'min_confidence_threshold': 50,
            'strong_buy_threshold': None,
            'use_conflict_detection': True,
            'volatility_adjusted': True,
            'signal_weights': {
                'rsi': 2.0,
                'ema': 2.0,
                'macd': 1.0,
                'stochastic': 3.0,
                'ichimoku': 2.0,
                'volume_poc': 1.5,
            },
        })

    def get_market_conditions_config(self) -> dict:
        return self.config.get('market_conditions', {
            'btc_trend_detection': True,
            'volatility_adjustment': True,
            'trading_session_adjustment': False,
            'high_volatility_threshold': 1.5,
            'low_volatility_threshold': 0.7,
            'btc_correlation_filter': False,
            'btc_correlation_threshold': 0.5,
            'btc_correlation_period': 24,
            'btc_filter': {
                'enabled': True,
                'symbol': 'BTC/USDT',
                'lookback_minutes': 5,
                'max_drop_pct': 0.5
            }
        })


# Default instance for v16 / shared (v17 uses hydra_v17_config.config)
config = Config()
