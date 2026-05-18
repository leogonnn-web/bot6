"""
HYDRA Indicators Matrix v17.0 - Complete Technical Analysis Suite
Unified indicator module containing all mathematical calculations

Components:
✅ RSI (Relative Strength Index)
✅ EMA (Exponential Moving Average)
✅ MACD (Moving Average Convergence Divergence)
✅ Stochastic Oscillator
✅ ATR (Average True Range)
✅ Ichimoku Cloud
✅ Volume Profile & POC
✅ Signal Optimizer (aggregation & conflict resolution)

Usage:
from src.indicators.matrix import IndicatorMatrix

matrix = IndicatorMatrix()
analysis = matrix.complete_analysis(ohlcv_data, current_price, market_volatility, btc_trend)
"""

from typing import List, Dict, Tuple, Optional
import numpy as np
import sys
import os

# Add shared to path for logger
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared')))
from logger_setup import logger


class RSIAnalyzer:
    """RSI (Relative Strength Index) calculation"""
    
    @staticmethod
    def calculate(ohlcv_data: List[List], period: int = 14) -> float:
        """Calculate RSI"""
        try:
            if len(ohlcv_data) < period + 1:
                return 50.0
            
            closes = np.array([float(c[4]) for c in ohlcv_data[-period - 1:]])
            deltas = np.diff(closes)
            
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            
            avg_gain = np.mean(gains)
            avg_loss = np.mean(losses)
            
            if avg_loss == 0:
                return 100.0 if avg_gain > 0 else 50.0
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            return float(np.clip(rsi, 0, 100))
        except Exception as e:
            logger.error(f"RSI calculation error: {e}")
            return 50.0


class EMAAnalyzer:
    """EMA (Exponential Moving Average) calculation"""
    
    @staticmethod
    def calculate(ohlcv_data: List[List], period: int = 9) -> float:
        """Calculate EMA"""
        try:
            closes = [float(c[4]) for c in ohlcv_data]
            
            if len(closes) < period:
                return closes[-1] if closes else 0.0
            
            multiplier = 2 / (period + 1)
            ema = np.mean(closes[:period])
            
            for close in closes[period:]:
                ema = close * multiplier + ema * (1 - multiplier)
            
            return float(ema)
        except Exception as e:
            logger.error(f"EMA calculation error: {e}")
            return 0.0


class MACDAnalyzer:
    """MACD (Moving Average Convergence Divergence) calculation"""
    
    @staticmethod
    def calculate(ohlcv_data: List[List], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float, float]:
        """Calculate MACD"""
        try:
            ema_fast = EMAAnalyzer.calculate(ohlcv_data, fast)
            ema_slow = EMAAnalyzer.calculate(ohlcv_data, slow)
            macd_line = ema_fast - ema_slow
            
            closes = [float(c[4]) for c in ohlcv_data]
            if len(closes) >= slow + signal:
                macd_values = []
                for i in range(slow - 1, len(closes)):
                    ema_f = EMAAnalyzer.calculate(ohlcv_data[:i + 1], fast)
                    ema_s = EMAAnalyzer.calculate(ohlcv_data[:i + 1], slow)
                    macd_values.append(ema_f - ema_s)
                
                signal_line = np.mean(macd_values[-signal:]) if macd_values else 0.0
            else:
                signal_line = macd_line
            
            histogram = macd_line - signal_line
            
            return float(macd_line), float(signal_line), float(histogram)
        except Exception as e:
            logger.error(f"MACD calculation error: {e}")
            return 0.0, 0.0, 0.0


class StochasticAnalyzer:
    """Stochastic Oscillator calculation"""
    
    @staticmethod
    def calculate(ohlcv_data: List[List], period: int = 14, k_smooth: int = 3, d_smooth: int = 3) -> Tuple[float, float]:
        """Calculate Stochastic"""
        try:
            if len(ohlcv_data) < period:
                return 50.0, 50.0
            
            lows = np.array([float(c[3]) for c in ohlcv_data[-period:]])
            highs = np.array([float(c[2]) for c in ohlcv_data[-period:]])
            closes = np.array([float(c[4]) for c in ohlcv_data[-period:]])
            
            lowest_low = np.min(lows)
            highest_high = np.max(highs)
            
            if highest_high == lowest_low:
                return 50.0, 50.0
            
            k_values = []
            for i in range(len(closes)):
                lo = np.min(lows[max(0, i - period + 1):i + 1])
                hi = np.max(highs[max(0, i - period + 1):i + 1])
                if hi == lo:
                    k_values.append(50.0)
                else:
                    k = ((closes[i] - lo) / (hi - lo)) * 100
                    k_values.append(k)
            
            if len(k_values) >= k_smooth:
                k = np.mean(k_values[-k_smooth:])
            else:
                k = k_values[-1] if k_values else 50.0
            
            if len(k_values) >= d_smooth:
                d = np.mean(k_values[-d_smooth:])
            else:
                d = k
            
            return float(np.clip(k, 0, 100)), float(np.clip(d, 0, 100))
        except Exception as e:
            logger.error(f"Stochastic calculation error: {e}")
            return 50.0, 50.0


class ATRAnalyzer:
    """ATR (Average True Range) calculation"""
    
    @staticmethod
    def calculate(ohlcv_data: List[List], period: int = 14) -> float:
        """Calculate ATR"""
        try:
            if len(ohlcv_data) < period:
                return 0.0
            
            tr_values = []
            
            for i in range(len(ohlcv_data)):
                high = float(ohlcv_data[i][2])
                low = float(ohlcv_data[i][3])
                close_prev = float(ohlcv_data[i - 1][4]) if i > 0 else high
                
                tr = max(high - low, abs(high - close_prev), abs(low - close_prev))
                tr_values.append(tr)
            
            if len(tr_values) < period:
                return np.mean(tr_values)
            
            multiplier = 2 / (period + 1)
            ema = np.mean(tr_values[:period])
            
            for tr in tr_values[period:]:
                ema = tr * multiplier + ema * (1 - multiplier)
            
            return float(ema)
        except Exception as e:
            logger.error(f"ATR calculation error: {e}")
            return 0.0


class IchimokuAnalyzer:
    """Ichimoku Cloud analysis"""
    
    TENKAN_PERIOD = 9
    KIJUN_PERIOD = 26
    SENKOU_B_PERIOD = 52
    CHIKOU_SHIFT = 26
    
    @staticmethod
    def calculate_tenkan(ohlcv_data: List[List]) -> float:
        """Calculate Tenkan-sen"""
        try:
            if len(ohlcv_data) < IchimokuAnalyzer.TENKAN_PERIOD:
                return 0.0
            
            highs = [float(c[2]) for c in ohlcv_data[-IchimokuAnalyzer.TENKAN_PERIOD:]]
            lows = [float(c[3]) for c in ohlcv_data[-IchimokuAnalyzer.TENKAN_PERIOD:]]
            
            return (max(highs) + min(lows)) / 2
        except Exception as e:
            logger.error(f"Tenkan calculation error: {e}")
            return 0.0
    
    @staticmethod
    def calculate_kijun(ohlcv_data: List[List]) -> float:
        """Calculate Kijun-sen"""
        try:
            if len(ohlcv_data) < IchimokuAnalyzer.KIJUN_PERIOD:
                return 0.0
            
            highs = [float(c[2]) for c in ohlcv_data[-IchimokuAnalyzer.KIJUN_PERIOD:]]
            lows = [float(c[3]) for c in ohlcv_data[-IchimokuAnalyzer.KIJUN_PERIOD:]]
            
            return (max(highs) + min(lows)) / 2
        except Exception as e:
            logger.error(f"Kijun calculation error: {e}")
            return 0.0
    
    @staticmethod
    def calculate_senkou_span_a(ohlcv_data: List[List]) -> float:
        """Calculate Senkou Span A"""
        try:
            tenkan = IchimokuAnalyzer.calculate_tenkan(ohlcv_data)
            kijun = IchimokuAnalyzer.calculate_kijun(ohlcv_data)
            return (tenkan + kijun) / 2
        except Exception as e:
            logger.error(f"Senkou Span A calculation error: {e}")
            return 0.0
    
    @staticmethod
    def calculate_senkou_span_b(ohlcv_data: List[List]) -> float:
        """Calculate Senkou Span B"""
        try:
            if len(ohlcv_data) < IchimokuAnalyzer.SENKOU_B_PERIOD:
                return 0.0
            
            highs = [float(c[2]) for c in ohlcv_data[-IchimokuAnalyzer.SENKOU_B_PERIOD:]]
            lows = [float(c[3]) for c in ohlcv_data[-IchimokuAnalyzer.SENKOU_B_PERIOD:]]
            
            return (max(highs) + min(lows)) / 2
        except Exception as e:
            logger.error(f"Senkou Span B calculation error: {e}")
            return 0.0
    
    @staticmethod
    def get_signals(ohlcv_data: List[List], current_price: float) -> Dict:
        """Get Ichimoku signals"""
        try:
            if len(ohlcv_data) < 52:
                return {
                    'cloud_bullish': None,
                    'price_above_cloud': None,
                    'tenkan_above_kijun': None,
                    'signal_strength': 0,
                    'recommendation': 'WAIT',
                    'components': {}
                }
            
            tenkan = IchimokuAnalyzer.calculate_tenkan(ohlcv_data)
            kijun = IchimokuAnalyzer.calculate_kijun(ohlcv_data)
            senkou_a = IchimokuAnalyzer.calculate_senkou_span_a(ohlcv_data)
            senkou_b = IchimokuAnalyzer.calculate_senkou_span_b(ohlcv_data)
            
            cloud_top = max(senkou_a, senkou_b)
            cloud_bottom = min(senkou_a, senkou_b)
            
            cloud_bullish = senkou_a > senkou_b
            price_above_cloud = current_price > cloud_top
            tenkan_above_kijun = tenkan > kijun
            
            signal_strength = 0
            if price_above_cloud:
                signal_strength += 2
            if cloud_bullish:
                signal_strength += 1
            if tenkan_above_kijun:
                signal_strength += 1
            
            if signal_strength >= 4:
                recommendation = "STRONG_BUY"
            elif signal_strength >= 2:
                recommendation = "BUY"
            else:
                recommendation = "WAIT"
            
            return {
                'cloud_bullish': cloud_bullish,
                'price_above_cloud': price_above_cloud,
                'tenkan_above_kijun': tenkan_above_kijun,
                'signal_strength': signal_strength,
                'recommendation': recommendation,
                'components': {
                    'tenkan': float(tenkan),
                    'kijun': float(kijun),
                    'senkou_a': float(senkou_a),
                    'senkou_b': float(senkou_b),
                    'cloud_top': float(cloud_top),
                    'cloud_bottom': float(cloud_bottom)
                }
            }
        except Exception as e:
            logger.error(f"Ichimoku signal error: {e}")
            return {'recommendation': 'ERROR', 'components': {}}


class VolumeProfileAnalyzer:
    """Volume Profile and POC analysis"""
    
    @staticmethod
    def calculate_poc(ohlcv_data: List[List], bins: int = 50, min_volume_threshold: float = 0.1) -> Tuple[float, Dict]:
        """
        Calculate Point of Control with noise filtering
        - bins: 50 bins for better granularity (was 20)
        - min_volume_threshold: Filter out bins with <10% of max volume
        """
        try:
            if len(ohlcv_data) < 20:  # Increased minimum to 20 candles
                return 0.0, {}
            
            highs = np.array([float(c[2]) for c in ohlcv_data])
            lows = np.array([float(c[3]) for c in ohlcv_data])
            closes = np.array([float(c[4]) for c in ohlcv_data])
            volumes = np.array([float(c[5]) if c[5] else 0 for c in ohlcv_data])
            
            # Use VWAP instead of typical price for accuracy
            typical_prices = (highs + lows + 2 * closes) / 4  # VWAP formula
            price_min = np.min(lows)
            price_max = np.max(highs)
            
            # Adaptive number of bins based on price range
            price_range = price_max - price_min
            if price_range > 0:
                adaptive_bins = min(100, max(30, int(price_range / price_min * 20)))
                bins = adaptive_bins
            
            bin_edges = np.linspace(price_min, price_max, bins + 1)
            bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
            
            bin_volumes = np.zeros(bins)
            for i, price in enumerate(typical_prices):
                bin_idx = np.digitize(price, bin_edges) - 1
                bin_idx = np.clip(bin_idx, 0, bins - 1)
                bin_volumes[bin_idx] += volumes[i]
            
            # Noise filtering - remove bins with low volume
            max_bin_volume = np.max(bin_volumes)
            noise_threshold = max_bin_volume * min_volume_threshold
            bin_volumes_filtered = np.where(bin_volumes < noise_threshold, 0, bin_volumes)
            
            # Find POC among filtered bins
            if np.sum(bin_volumes_filtered) > 0:
                poc_idx = np.argmax(bin_volumes_filtered)
            else:
                poc_idx = np.argmax(bin_volumes)  # Fallback
            
            poc_price = bin_centers[poc_idx]
            
            # Calculate Value Area (VAH/VAL) - 70% of volume
            sorted_indices = np.argsort(bin_volumes)[::-1]
            cumulative_volume = 0.0
            total_volume = np.sum(bin_volumes)
            va_bins = []
            
            for idx in sorted_indices:
                cumulative_volume += bin_volumes[idx]
                va_bins.append(idx)
                if cumulative_volume >= total_volume * 0.7:
                    break
            
            if va_bins:
                vah_price = bin_centers[max(va_bins)]
                val_price = bin_centers[min(va_bins)]
            else:
                vah_price = poc_price
                val_price = poc_price
            
            return float(poc_price), {
                'poc_price': float(poc_price),
                'poc_volume': float(bin_volumes[poc_idx]),
                'total_volume': float(np.sum(volumes)),
                'vah_price': float(vah_price),
                'val_price': float(val_price),
                'bins_used': bins,
                'noise_filtered': True
            }
        except Exception as e:
            logger.error(f"POC calculation error: {e}")
            return 0.0, {}
    
    @staticmethod
    def analyze_volume_trend(ohlcv_data: List[List], lookback: int = 10) -> Dict:
        """Analyze volume trend"""
        try:
            if len(ohlcv_data) < lookback:
                lookback = len(ohlcv_data)
            
            volumes = np.array([float(c[5]) if c[5] else 0 for c in ohlcv_data[-lookback:]])
            avg_volume = np.mean(volumes)
            recent_volume = np.mean(volumes[-3:])
            
            if recent_volume > avg_volume * 1.2:
                trend = "INCREASING"
            elif recent_volume < avg_volume * 0.8:
                trend = "DECREASING"
            else:
                trend = "STABLE"
            
            return {'trend': trend, 'current_volume': float(recent_volume), 'average_volume': float(avg_volume)}
        except Exception as e:
            logger.error(f"Volume trend error: {e}")
            return {'trend': 'UNKNOWN'}
    
    @staticmethod
    def get_signals(ohlcv_data: List[List], current_price: float) -> Dict:
        """Get volume signals"""
        try:
            if len(ohlcv_data) < 10:
                return {'at_poc': False, 'support_level': False, 'volume_strength': 0}
            
            poc_price, profile = VolumeProfileAnalyzer.calculate_poc(ohlcv_data)
            vol_trend = VolumeProfileAnalyzer.analyze_volume_trend(ohlcv_data)
            
            price_to_poc_distance = abs(current_price - poc_price) / poc_price * 100 if poc_price > 0 else 100
            at_poc = price_to_poc_distance < 0.5
            price_above_poc = current_price > poc_price
            
            signal_strength = 0
            if at_poc:
                signal_strength += 2
            elif not price_above_poc:
                signal_strength += 1
            
            if vol_trend['trend'] == 'INCREASING':
                signal_strength += 1
            
            return {
                'at_poc': at_poc,
                'poc_price': float(poc_price),
                'support_level': not price_above_poc,
                'volume_trend': vol_trend,
                'volume_strength': signal_strength
            }
        except Exception as e:
            logger.error(f"Volume signal error: {e}")
            return {'at_poc': False, 'volume_strength': 0}


class SignalOptimizer:
    """Signal aggregation and conflict resolution"""
    
    @classmethod
    def from_config(cls, config_dict: dict, market_config: dict = None, tank_mode: bool = False):
        signal_weights = config_dict.get('signal_weights', {})
        min_confidence = config_dict.get('min_confidence_threshold', 50.0)
        strong_buy = config_dict.get('strong_buy_threshold', None)
        use_conflict = config_dict.get('use_conflict_detection', True)
        volatility_adj = config_dict.get('volatility_adjusted', True)
        
        return cls(
            signal_weights=signal_weights,
            min_confidence_threshold=min_confidence,
            strong_buy_threshold=strong_buy,
            use_conflict_detection=use_conflict,
            volatility_adjusted=volatility_adj,
            tank_mode=tank_mode
        )
    
    def __init__(
        self,
        signal_weights: Optional[dict] = None,
        min_confidence_threshold: float = 50.0,
        strong_buy_threshold: Optional[float] = None,
        use_conflict_detection: bool = True,
        volatility_adjusted: bool = True,
        tank_mode: bool = False
    ):
        default_weights = {
            'rsi': 2.0,
            'ema': 2.0,
            'macd': 1.0,
            'stochastic': 3.0,
            'ichimoku': 2.0,
            'volume_poc': 1.5,
        }
        self.signal_weights = {**default_weights, **(signal_weights or {})}
        self.min_confidence_threshold = float(min_confidence_threshold)
        self.strong_buy_threshold = float(strong_buy_threshold) if strong_buy_threshold else max(75.0, self.min_confidence_threshold + 15.0)
        self.use_conflict_detection = use_conflict_detection
        self.volatility_adjusted = volatility_adjusted
        self.tank_mode = tank_mode
        self.max_possible_score = sum(self.signal_weights.values())
    
    def aggregate_signals(
        self,
        rsi_signal: Dict,
        ema_signal: Dict,
        macd_signal: Dict,
        stochastic_signal: Dict = None,
        ichimoku_signal: Dict = None,
        volume_signal: Dict = None,
        volatility_level: float = 1.0
    ) -> Dict:
        """Aggregate all signals into single confidence score"""
        try:
            score = 0.0
            signals_fired = []
            conflicts = []
            
            # TANK MODE: Hard block on Downtrend
            if self.tank_mode:
                # Block LONG if Ichimoku shows Downtrend
                if ichimoku_signal:
                    if not ichimoku_signal.get('cloud_bullish') or not ichimoku_signal.get('price_above_cloud'):
                        return {
                            'recommendation': 'SKIP',
                            'confidence': 0.0,
                            'score': 0.0,
                            'max_score': float(self.max_possible_score),
                            'signals_fired': [],
                            'conflicts': ['Ichimoku Downtrend - TANK MODE BLOCK'],
                            'tank_block_reason': 'Ichimoku Downtrend'
                        }
                
                # Block LONG if EMA shows Downtrend
                ema_alignment = rsi_signal.get('ema_alignment', 0)
                if ema_alignment <= -1:
                    return {
                        'recommendation': 'SKIP',
                        'confidence': 0.0,
                        'score': 0.0,
                        'max_score': float(self.max_possible_score),
                        'signals_fired': [],
                        'conflicts': ['EMA Downtrend - TANK MODE BLOCK'],
                        'tank_block_reason': 'EMA Downtrend'
                    }
            
            # RSI
            if rsi_signal.get('oversold'):
                score += self.signal_weights['rsi']
                signals_fired.append("RSI oversold")
            elif rsi_signal.get('overbought'):
                score -= 2.5
                conflicts.append("RSI overbought")
            
            # EMA
            ema_alignment = rsi_signal.get('ema_alignment', 0)
            if ema_alignment >= 2:
                score += self.signal_weights['ema']
                signals_fired.append("EMA strong uptrend")
            elif ema_alignment == 1:
                score += 1.0
                signals_fired.append("EMA weak uptrend")
            elif ema_alignment <= -1:
                score -= 1.5
                conflicts.append("EMA downtrend")
            
            # MACD
            if macd_signal.get('bullish'):
                score += self.signal_weights['macd']
                signals_fired.append("MACD bullish")
            elif macd_signal.get('bearish'):
                score -= 1.5
                conflicts.append("MACD bearish")
            
            # Stochastic
            if stochastic_signal:
                if stochastic_signal.get('oversold') and stochastic_signal.get('bullish_crossover'):
                    score += self.signal_weights['stochastic']
                    signals_fired.append("Stochastic oversold + crossover")
                elif stochastic_signal.get('oversold'):
                    score += 2.0
                    signals_fired.append("Stochastic oversold")
                elif stochastic_signal.get('overbought'):
                    score -= 2.0
                    conflicts.append("Stochastic overbought")
            
            # Ichimoku
            if ichimoku_signal:
                if ichimoku_signal.get('cloud_bullish') and ichimoku_signal.get('price_above_cloud'):
                    score += self.signal_weights['ichimoku']
                    signals_fired.append("Ichimoku bullish + price above cloud")
                elif ichimoku_signal.get('cloud_bullish'):
                    score += 1.5
                    signals_fired.append("Ichimoku cloud bullish")
            
            # Volume
            if volume_signal:
                if volume_signal.get('at_poc'):
                    score += self.signal_weights['volume_poc']
                    signals_fired.append("Price at POC")
                elif volume_signal.get('support_level'):
                    score += 1.0
                    signals_fired.append("Price near volume support")
            
            # TANK MODE: Hard conflict detection
            if self.tank_mode:
                if len(conflicts) >= 1:
                    return {
                        'recommendation': 'SKIP',
                        'confidence': 0.0,
                        'score': 0.0,
                        'max_score': float(self.max_possible_score),
                        'signals_fired': signals_fired,
                        'conflicts': conflicts,
                        'tank_block_reason': f'Conflict detected: {conflicts[0]}'
                    }
            else:
                # Normal mode
                if self.use_conflict_detection and len(conflicts) >= 2:
                    score *= 0.7
            
            # Volatility adjustment
            if self.volatility_adjusted and volatility_level > 1.3:
                score *= 0.85
            
            confidence = (score / self.max_possible_score) * 100
            confidence = max(0, min(100, confidence))
            
            # TANK MODE: 85% threshold for STRONG_BUY
            if self.tank_mode:
                if confidence >= 85.0:
                    recommendation = "STRONG_BUY"
                elif confidence >= 75.0:
                    recommendation = "BUY"
                else:
                    recommendation = "SKIP"
            else:
                if confidence >= self.strong_buy_threshold:
                    recommendation = "STRONG_BUY"
                elif confidence >= self.min_confidence_threshold:
                    recommendation = "BUY"
                else:
                    recommendation = "SKIP"
            
            return {
                'recommendation': recommendation,
                'confidence': float(confidence),
                'score': float(score),
                'max_score': float(self.max_possible_score),
                'signals_fired': signals_fired,
                'conflicts': conflicts
            }
        except Exception as e:
            logger.error(f"Signal aggregation error: {e}")
            return {'recommendation': 'SKIP', 'confidence': 0.0, 'score': 0.0}
    
    def adjust_threshold_for_market_conditions(
        self,
        btc_trend: str,
        market_volatility: float,
        trading_session: str = "american",
        btc_trend_detection_enabled: bool = True
    ) -> float:
        """Adjust confidence threshold based on market conditions"""
        base_threshold = float(self.min_confidence_threshold)
        
        # Only adjust for BTC trend if detection is enabled
        if btc_trend_detection_enabled:
            if btc_trend == "bullish":
                base_threshold -= 5
            elif btc_trend == "bearish":
                base_threshold += 10
        
        if market_volatility > 1.5:
            base_threshold += 5
        elif market_volatility < 0.7:
            base_threshold -= 3
        
        return float(np.clip(base_threshold, 40, 70))


class IndicatorMatrix:
    """Main interface for all indicators"""
    
    def __init__(self, optimizer: Optional[SignalOptimizer] = None):
        self.optimizer = optimizer if optimizer is not None else SignalOptimizer()
    
    def complete_analysis(
        self,
        ohlcv_data: List[List],
        current_price: float,
        market_volatility: float = 1.0,
        btc_trend: str = "neutral",
        btc_trend_detection_enabled: bool = True
    ) -> Dict:
        """
        Complete multi-indicator analysis
        
        Returns:
            Dict with comprehensive analysis and recommendation
        """
        try:
            if len(ohlcv_data) < 52:
                return {
                    'status': 'insufficient_data',
                    'recommendation': 'WAIT',
                    'confidence': 0.0,
                    'message': f'Need 52+ candles, have {len(ohlcv_data)}'
                }
            
            # Calculate all indicators
            rsi = RSIAnalyzer.calculate(ohlcv_data)
            ema_9 = EMAAnalyzer.calculate(ohlcv_data, 9)
            ema_21 = EMAAnalyzer.calculate(ohlcv_data, 21)
            macd, macd_signal, macd_hist = MACDAnalyzer.calculate(ohlcv_data)
            k, d = StochasticAnalyzer.calculate(ohlcv_data)
            atr = ATRAnalyzer.calculate(ohlcv_data)
            
            # Advanced indicators
            ichimoku_signal = IchimokuAnalyzer.get_signals(ohlcv_data, current_price)
            volume_signal = VolumeProfileAnalyzer.get_signals(ohlcv_data, current_price)
            
            # Prepare signal data
            rsi_signal = {
                'oversold': rsi < 30,
                'overbought': rsi > 70,
                'ema_alignment': 2 if current_price > ema_9 > ema_21 else (1 if current_price > ema_9 else -2)
            }
            
            ema_signal = {
                'bullish': current_price > ema_9,
                'alignment': rsi_signal['ema_alignment']
            }
            
            macd_signal = {
                'bullish': macd > macd_signal and macd_hist > 0,
                'bearish': macd < macd_signal and macd_hist < 0
            }
            
            stochastic_signal = {
                'oversold': k < 20,
                'overbought': k > 80,
                'bullish_crossover': k > d and (k - 5) <= d
            }
            
            # Aggregate signals
            signal_result = self.optimizer.aggregate_signals(
                rsi_signal=rsi_signal,
                ema_signal=ema_signal,
                macd_signal=macd_signal,
                stochastic_signal=stochastic_signal,
                ichimoku_signal=ichimoku_signal,
                volume_signal=volume_signal,
                volatility_level=market_volatility
            )
            
            # Adjust threshold for market conditions
            adjusted_threshold = self.optimizer.adjust_threshold_for_market_conditions(
                btc_trend=btc_trend,
                market_volatility=market_volatility,
                btc_trend_detection_enabled=btc_trend_detection_enabled
            )
            
            # Final recommendation
            if signal_result['confidence'] >= self.optimizer.strong_buy_threshold:
                final_recommendation = "STRONG_BUY"
            elif signal_result['confidence'] >= adjusted_threshold:
                final_recommendation = "BUY"
            else:
                final_recommendation = "SKIP"
            
            return {
                'status': 'ok',
                'recommendation': final_recommendation,
                'confidence': signal_result['confidence'],
                'adjusted_threshold': adjusted_threshold,
                'components': {
                    'rsi': float(rsi),
                    'ema_9': float(ema_9),
                    'ema_21': float(ema_21),
                    'macd': float(macd),
                    'macd_signal': float(macd_signal),
                    'macd_histogram': float(macd_hist),
                    'stochastic_k': float(k),
                    'stochastic_d': float(d),
                    'atr': float(atr)
                },
                'signals': {
                    'rsi': rsi_signal,
                    'ema': ema_signal,
                    'macd': macd_signal,
                    'stochastic': stochastic_signal,
                    'ichimoku': ichimoku_signal,
                    'volume': volume_signal
                },
                'signal_analysis': signal_result
            }
        except Exception as e:
            logger.error(f"Complete analysis error: {e}", exc_info=True)
            return {
                'status': 'error',
                'recommendation': 'SKIP',
                'confidence': 0.0,
                'message': f'Analysis error: {str(e)}'
            }


# Default instance for backward compatibility
# Will be initialized with config if available, otherwise uses defaults
analyzer = None

def initialize_analyzer(config=None, tank_mode=False):
    """Initialize analyzer with config if provided"""
    global analyzer
    if config is not None:
        try:
            signal_optimizer_config = config.get_signal_optimizer_config()
            market_conditions_config = config.get_market_conditions_config()
            optimizer = SignalOptimizer.from_config(signal_optimizer_config, market_conditions_config, tank_mode=tank_mode)
            analyzer = IndicatorMatrix(optimizer=optimizer)
            logger.info(f"@INDICATORS_INIT@ Analyzer initialized with config (TANK MODE: {tank_mode})")
        except Exception as e:
            logger.warning(f"@INDICATORS_WARN@ Failed to init with config: {e}, using defaults")
            analyzer = IndicatorMatrix(optimizer=SignalOptimizer(tank_mode=tank_mode))
    else:
        analyzer = IndicatorMatrix(optimizer=SignalOptimizer(tank_mode=tank_mode))
    return analyzer
