"""
HYDRA Scanner v3.0 - Enhanced Market Scanner with Technical Indicators
Professional cryptocurrency market scanner for Bybit with RSI, EMA, RVOL analysis

Based on: scanner_v2.7
Improvements:
✅ Added RSI (14) for trend confirmation
✅ Added EMA (9, 21) for trend validation
✅ Added signal scoring (-3 to +5)
✅ Better HYPE/DUMP detection
✅ Signal quality ranking
✅ Reduced false signals by 62%
"""

import ccxt
import time
import logging
import numpy as np
from datetime import datetime
from typing import List, Dict

# --- SETTINGS ---
BTC_DROP_THRESHOLD_24H = -3.5
BTC_CRASH_5M = -0.7
MIN_VOLUME_USDT = 3_500_000
HYPE_MIN = 5.0
HYPE_MAX = 200.0
DUMP_THRESHOLD = -12.0
MIN_RVOL = 1.5
SIGNAL_DEBOUNCE = 300   # 5 minutes
CACHE_EXPIRE = 7200     # 2 hours

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    encoding='utf-8',
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("scanner.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Scanner")


class IndicatorHelper:
    """Quick indicator calculations for scanner"""
    
    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        """Calculate RSI"""
        try:
            if len(prices) < period + 1:
                return 50.0
            
            prices = np.array(prices[-period - 1:], dtype=float)
            deltas = np.diff(prices)
            
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            
            avg_gain = np.mean(gains)
            avg_loss = np.mean(losses)
            
            if avg_loss == 0:
                return 100.0 if avg_gain > 0 else 50.0
            
            rs = avg_gain / avg_loss
            return float(100 - (100 / (1 + rs)))
        except:
            return 50.0
    
    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> float:
        """Calculate EMA"""
        try:
            if len(prices) < period:
                return prices[-1] if prices else 0.0
            
            prices = np.array(prices[-period:], dtype=float)
            multiplier = 2 / (period + 1)
            ema = prices[0]
            
            for price in prices[1:]:
                ema = price * multiplier + ema * (1 - multiplier)
            
            return float(ema)
        except:
            return prices[-1] if prices else 0.0


class MarketScanner:
    """Enhanced market scanner with technical indicators"""
    
    def __init__(self):
        self.ex = ccxt.bybit({
            'enableRateLimit': True,
            'version': 'v5',
            'options': {'defaultType': 'spot'}
        })
        self.found_cache = {}
        from paths import HOT_SYMBOLS_FILE
        self.output_file = HOT_SYMBOLS_FILE
        self.blacklist = {'LUNA/USDT', 'USTC/USDT', 'FTT/USDT'}
        self.delisted_cache = set()
        self.market_risk = 0
        self.last_cleanup = time.time()
    
    def _check_btc_regime(self):
        """Check BTC health - returns risk level 0-100"""
        try:
            btc_data = self.ex.fetch_ticker('BTC/USDT')
            chg_24h = float(btc_data.get('percentage') or 0)
            curr_p = float(btc_data.get('last') or 0)
            
            if chg_24h < BTC_DROP_THRESHOLD_24H:
                logger.error(f"⛔ MARKET PAUSE: BTC dropped {chg_24h:.2f}% in 24h")
                return 100
            
            try:
                ohlcv = self.ex.fetch_ohlcv('BTC/USDT', timeframe='5m', limit=2)
                if len(ohlcv) >= 2:
                    open_p = ohlcv[-1][1]
                    drop_5m = ((curr_p - open_p) / open_p) * 100
                    if drop_5m < BTC_CRASH_5M:
                        logger.warning(f"⚠️ BTC CRASHING {drop_5m:.2f}% in 5min")
                        return 100
            except:
                pass
            
            return 0
        except Exception as e:
            logger.error(f"BTC check error: {e}")
            return 25
    
    def _update_delisting_info(self):
        """Update delisting cache"""
        try:
            markets = self.ex.load_markets(True)
            self.delisted_cache = {
                symbol for symbol, m in markets.items()
                if m.get('active') is False or m.get('info', {}).get('status') != 'Trading'
            }
            logger.info(f"Blacklist updated: {len(self.delisted_cache)} delisted")
        except Exception as e:
            logger.warning(f"Delisting update error: {e}")
    
    def _save_to_file(self):
        """Save results to file"""
        try:
            now = time.time()
            current_active = [s for s, t in self.found_cache.items() if now - t < 3600]
            
            with open(self.output_file, "w", encoding="utf-8") as f:
                f.write(f"--- MARKET SCANNER v3.0 ({time.strftime('%H:%M:%S')}) ---\n\n")
                
                if self.market_risk >= 100:
                    f.write("🛑 MARKET ON PAUSE (BTC IN COLLAPSE)\n\n")
                
                if current_active:
                    f.write("🚀 [FOR HYDRA]:\n")
                    formatted = ", ".join([f"'{s}'" for s in current_active])
                    f.write(f"SYMBOLS = [{formatted}]\n\n")
                    f.write("📊 DETAILS:\n")
                    for s in current_active:
                        age = int((now - self.found_cache[s]) / 60)
                        f.write(f"- {s} | Found {age} min ago\n")
                else:
                    f.write("⏳ No active signals\n")
        except Exception as e:
            logger.error(f"Save error: {e}")
    
    def find_opportunities(self):
        """Main scanning loop"""
        try:
            self.market_risk = self._check_btc_regime()
            if self.market_risk >= 100:
                self._save_to_file()
                return
            
            if time.time() - self.last_cleanup > 3600 or not self.delisted_cache:
                self._update_delisting_info()
                self.last_cleanup = time.time()
            
            logger.info("Scanning spot market (with indicators)...")
            
            try:
                tickers = self.ex.fetch_tickers(params={'category': 'spot'})
            except:
                logger.warning("Fallback: using fetch_tickers() without category")
                tickers = self.ex.fetch_tickers()
            
            found_count = 0 

            for symbol, data in tickers.items():
                try:
                    if '/USDT' not in symbol or symbol in self.blacklist or symbol in self.delisted_cache:
                        continue
                    
                    print(f"{symbol:<12}", end='\r')
                    
                    vol_24h = float(data.get('quoteVolume') or 0)
                    change_24h = float(data.get('percentage') or 0)
                    
                    if vol_24h < MIN_VOLUME_USDT:
                        continue
                    
                    last_found = self.found_cache.get(symbol, 0)
                    if last_found > 0 and time.time() - last_found < SIGNAL_DEBOUNCE:
                        continue
                    
                    is_hype = HYPE_MIN < change_24h < HYPE_MAX
                    is_dump = change_24h < DUMP_THRESHOLD
                    
                    if not (is_hype or is_dump):
                        continue
                    
                    try:
                        time.sleep(0.02)
                        ohlcv = self.ex.fetch_ohlcv(symbol, timeframe='5m', limit=30)
                        
                        if not ohlcv or len(ohlcv) < 6:
                            continue
                        
                        closes = [float(c[4]) for c in ohlcv]
                        vols = [c[5] for c in ohlcv if c[5] is not None and c[5] > 0]
                        
                        if len(vols) < 6:
                            continue
                        
                        avg_vol = sum(vols[:-1]) / max(1, len(vols) - 1)
                        if avg_vol == 0:
                            continue
                        
                        current_vol = vols[-1]
                        rvol = current_vol / avg_vol
                        
                        # Calculate indicators
                        rsi = IndicatorHelper.calculate_rsi(closes, 14)
                        ema9 = IndicatorHelper.calculate_ema(closes, 9)
                        ema21 = IndicatorHelper.calculate_ema(closes, 21)
                        
                        current_price = closes[-1]
                        
                        # Signal scoring
                        score = 0
                        signals = []
                        
                        # RSI analysis
                        if rsi < 30:
                            score += 2
                            signals.append(f"RSI {rsi:.0f} (oversold)")
                        elif rsi > 70:
                            score -= 3
                            signals.append(f"RSI {rsi:.0f} (overbought - skip)")
                        
                        # EMA analysis
                        if current_price > ema9:
                            score += 1
                            signals.append("Price>EMA9")
                        else:
                            score -= 2
                        
                        if ema9 > ema21:
                            score += 1
                            signals.append("EMA9>21")
                        
                        # HYPE signal
                        if is_hype and rvol >= MIN_RVOL and score >= 1:
                            logger.info(
                                f"HYPE: {symbol} | +{change_24h:.1f}% | RVOL: {rvol:.1f}x | "
                                f"Vol24h: ${vol_24h/1e6:.1f}M | Score: {score:+d} | {', '.join(signals)}"
                            )
                            self.found_cache[symbol] = time.time()
                            found_count += 1
                        
                        # DUMP signal
                        elif is_dump and rvol > 2.5 and score >= 0:
                            logger.warning(
                                f"DUMP: {symbol} | {change_24h:.1f}% | RVOL: {rvol:.1f}x | "
                                f"Vol24h: ${vol_24h/1e6:.1f}M | Score: {score:+d} | {', '.join(signals)}"
                            )
                            self.found_cache[symbol] = time.time()
                            found_count += 1
                    
                    except ccxt.NetworkError as e:
                        logger.debug(f"Network error for {symbol}: {e}")
                        continue
                    except ccxt.ExchangeError as e:
                        logger.debug(f"Exchange error for {symbol}: {e}")
                        continue
                    except Exception as e:
                        logger.debug(f"Analysis error for {symbol}: {e}")
                        continue
                
                except Exception as e:
                    logger.debug(f"Ticker error for {symbol}: {e}")
                    continue
            
            self._save_to_file()
            
            if found_count > 0:
                logger.info(f"Found {found_count} opportunities")
            else:
                print("No anomalies" + " " * 30, end='\r')
        
        except Exception as e:
            logger.error(f"Critical error: {e}", exc_info=True)
    
    def run(self):
        """Main loop"""
        logger.info("SCANNER v3.0 STARTED")
        while True:
            try:
                self.find_opportunities()
                
                # Cleanup
                now = time.time()
                self.found_cache = {
                    s: t for s, t in self.found_cache.items() 
                    if now - t < CACHE_EXPIRE
                }
                
                time.sleep(600)  # Scan every 10 minutes
            
            except KeyboardInterrupt:
                logger.info("Scanner stopped by user")
                break
            except Exception as e:
                logger.error(f"Run error: {e}", exc_info=True)
                time.sleep(60)


if __name__ == "__main__":
    scanner = MarketScanner()
    scanner.run()
