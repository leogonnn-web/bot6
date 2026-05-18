"""
HYDRA Scanner v17.0 - Market Scanner Module
Searches for hot symbols with technical analysis

Features:
✅ HYPE/DUMP detection
✅ RSI, EMA, RVOL analysis
✅ Signal scoring
✅ BTC market regime check
✅ Integration with bot via hot_symbols.txt
"""

import os
import re
import time
import logging
from typing import List, Dict, Set
import sys

# Add paths
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from logger_setup import logger
from api.bybit_client import BybitClient
from indicators.matrix import RSIAnalyzer, EMAAnalyzer


class ScannerIntegration:
    """Reads and parses hot_symbols.txt from scanner"""
    
    def __init__(self, filename: str = "hot_symbols.txt"):
        self.filename = filename
        self.last_symbols = set()
        self.last_update = 0
        self.cache_ttl = 300  # 5 minutes
    
    def read_symbols(self, force_refresh: bool = False) -> Set[str]:
        """Read symbols from scanner file"""
        try:
            if not force_refresh and time.time() - self.last_update < self.cache_ttl:
                return self.last_symbols
            
            if not os.path.exists(self.filename):
                logger.debug(f"Scanner file {self.filename} not found")
                return set()
            
            with open(self.filename, 'r', encoding='utf-8') as f:
                content = f.read()
            
            match = re.search(r'SYMBOLS\s*=\s*\[(.*?)\]', content)
            if not match:
                return set()
            
            symbols_str = match.group(1)
            symbols = re.findall(r"'([A-Z0-9]+/USDT)'", symbols_str)
            
            result = set(symbols)
            
            if result != self.last_symbols:
                new_symbols = result - self.last_symbols
                removed_symbols = self.last_symbols - result
                
                if new_symbols:
                    logger.debug(f"Scanner: New symbols: {', '.join(new_symbols)}")
                if removed_symbols:
                    logger.debug(f"Scanner: Removed symbols: {', '.join(removed_symbols)}")
            
            self.last_symbols = result
            self.last_update = time.time()
            
            return result
        except Exception as e:
            logger.error(f"Error reading scanner file: {e}")
            return set()
    
    def get_scanner_symbols(self) -> Set[str]:
        """Get current scanner symbols (cached)"""
        return self.read_symbols(force_refresh=False)
    
    def refresh(self) -> Set[str]:
        """Force refresh from file"""
        return self.read_symbols(force_refresh=True)


class DynamicSymbolManager:
    """Manages symbol list combining scanner signals and base config"""
    
    def __init__(self, base_symbols: List[str], scanner_integration: ScannerIntegration):
        self.base_symbols = set(base_symbols)
        self.scanner = scanner_integration
        self.stats = {
            'total': 0,
            'from_scanner': 0,
            'from_base': 0,
            'last_update': 0
        }
    
    def get_symbols(self, refresh_scanner: bool = True) -> List[str]:
        """Get combined symbol list"""
        try:
            scanner_symbols = self.scanner.read_symbols(force_refresh=refresh_scanner)
            
            if scanner_symbols:
                combined = list(scanner_symbols) + [
                    s for s in self.base_symbols if s not in scanner_symbols
                ]
            else:
                combined = list(self.base_symbols)
            
            self.stats['total'] = len(combined)
            self.stats['from_scanner'] = len(scanner_symbols)
            self.stats['from_base'] = len([s for s in combined if s not in scanner_symbols])
            self.stats['last_update'] = time.time()
            
            return combined
        except Exception as e:
            logger.error(f"Error getting symbols: {e}")
            return list(self.base_symbols)
    
    def has_scanner_signals(self) -> bool:
        """Check if scanner has found any signals"""
        return len(self.scanner.get_scanner_symbols()) > 0


class MarketScanner:
    """Enhanced market scanner with technical indicators"""
    
    # Settings
    BTC_DROP_THRESHOLD_24H = -3.5
    BTC_CRASH_5M = -0.7
    MIN_VOLUME_USDT = 3_500_000
    HYPE_MIN = 5.0
    HYPE_MAX = 200.0
    DUMP_THRESHOLD = -12.0
    MIN_RVOL = 1.5
    SIGNAL_DEBOUNCE = 300
    CACHE_EXPIRE = 7200
    
    def __init__(self, output_file: str = "hot_symbols.txt"):
        self.client = BybitClient()
        self.output_file = output_file
        self.found_cache = {}
        self.blacklist = {'LUNA/USDT', 'USTC/USDT', 'FTT/USDT'}
        self.delisted_cache = set()
        self.market_risk = 0
        self.last_cleanup = time.time()
    
    def _check_btc_regime(self) -> int:
        """Check BTC health - returns risk level 0-100"""
        try:
            btc_data = self.client.fetch_ticker('BTC/USDT')
            chg_24h = float(btc_data.get('percentage') or 0)
            curr_p = float(btc_data.get('last') or 0)
            
            if chg_24h < self.BTC_DROP_THRESHOLD_24H:
                logger.error(f"MARKET PAUSE: BTC dropped {chg_24h:.2f}% in 24h")
                return 100
            
            try:
                ohlcv = self.client.fetch_ohlcv('BTC/USDT', timeframe='5m', limit=2)
                if len(ohlcv) >= 2:
                    open_p = ohlcv[-1][1]
                    drop_5m = ((curr_p - open_p) / open_p) * 100
                    if drop_5m < self.BTC_CRASH_5M:
                        logger.warning(f"BTC CRASHING {drop_5m:.2f}% in 5min")
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
            markets = self.client.load_markets()
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
                f.write(f"--- MARKET SCANNER v17.0 ({time.strftime('%H:%M:%S')}) ---\n\n")
                
                if self.market_risk >= 100:
                    f.write("MARKET ON PAUSE (BTC IN COLLAPSE)\n\n")
                
                if current_active:
                    f.write("[FOR HYDRA]:\n")
                    formatted = ", ".join([f"'{s}'" for s in current_active])
                    f.write(f"SYMBOLS = [{formatted}]\n\n")
                    f.write("DETAILS:\n")
                    for s in current_active:
                        age = int((now - self.found_cache[s]) / 60)
                        f.write(f"- {s} | Found {age} min ago\n")
                else:
                    f.write("No active signals\n")
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
            
            logger.info("Scanning spot market...")
            
            try:
                tickers = self.client.fetch_tickers(list(self.client.exchange.markets.keys())[:100])
            except:
                tickers = self.client.fetch_tickers()
            
            found_count = 0
            
            for symbol, data in tickers.items():
                try:
                    if '/USDT' not in symbol or symbol in self.blacklist or symbol in self.delisted_cache:
                        continue
                    
                    vol_24h = float(data.get('quoteVolume') or 0)
                    change_24h = float(data.get('percentage') or 0)
                    
                    if vol_24h < self.MIN_VOLUME_USDT:
                        continue
                    
                    last_found = self.found_cache.get(symbol, 0)
                    if last_found > 0 and time.time() - last_found < self.SIGNAL_DEBOUNCE:
                        continue
                    
                    is_hype = self.HYPE_MIN < change_24h < self.HYPE_MAX
                    is_dump = change_24h < self.DUMP_THRESHOLD
                    
                    if not (is_hype or is_dump):
                        continue
                    
                    try:
                        ohlcv = self.client.fetch_ohlcv(symbol, timeframe='5m', limit=30)
                        
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
                        rsi = RSIAnalyzer.calculate(ohlcv, 14)
                        ema9 = EMAAnalyzer.calculate(ohlcv, 9)
                        ema21 = EMAAnalyzer.calculate(ohlcv, 21)
                        
                        current_price = closes[-1]
                        
                        # Signal scoring
                        score = 0
                        signals = []
                        
                        if rsi < 30:
                            score += 2
                            signals.append(f"RSI {rsi:.0f} (oversold)")
                        elif rsi > 70:
                            score -= 3
                            signals.append(f"RSI {rsi:.0f} (overbought)")
                        
                        if current_price > ema9:
                            score += 1
                            signals.append("Price>EMA9")
                        else:
                            score -= 2
                        
                        if ema9 > ema21:
                            score += 1
                            signals.append("EMA9>21")
                        
                        # HYPE signal
                        if is_hype and rvol >= self.MIN_RVOL and score >= 1:
                            logger.info(
                                f"HYPE: {symbol} | +{change_24h:.1f}% | RVOL: {rvol:.1f}x | "
                                f"Vol24h: ${vol_24h/1e6:.1f}M | Score: {score:+d}"
                            )
                            self.found_cache[symbol] = time.time()
                            found_count += 1
                        
                        # DUMP signal
                        elif is_dump and rvol > 2.5 and score >= 0:
                            logger.warning(
                                f"DUMP: {symbol} | {change_24h:.1f}% | RVOL: {rvol:.1f}x | "
                                f"Vol24h: ${vol_24h/1e6:.1f}M | Score: {score:+d}"
                            )
                            self.found_cache[symbol] = time.time()
                            found_count += 1
                    
                    except Exception as e:
                        logger.debug(f"Analysis error for {symbol}: {e}")
                        continue
                
                except Exception as e:
                    logger.debug(f"Ticker error for {symbol}: {e}")
                    continue
            
            self._save_to_file()
            
            if found_count > 0:
                logger.info(f"Found {found_count} opportunities")
        
        except Exception as e:
            logger.error(f"Critical error: {e}", exc_info=True)
    
    def run(self):
        """Main loop"""
        logger.info("SCANNER v17.0 STARTED")
        while True:
            try:
                self.find_opportunities()
                
                now = time.time()
                self.found_cache = {
                    s: t for s, t in self.found_cache.items() 
                    if now - t < self.CACHE_EXPIRE
                }
                
                time.sleep(600)
            
            except KeyboardInterrupt:
                logger.info("Scanner stopped by user")
                break
            except Exception as e:
                logger.error(f"Run error: {e}", exc_info=True)
                time.sleep(60)
