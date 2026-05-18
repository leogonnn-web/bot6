# HYDRA Bot v17.0 - Quick Reference Guide

**Last Updated:** 2026-05-11  
**Status:** ✅ **READY FOR PRODUCTION**  
**Version:** 17.0 Complete

---

## 📚 Documentation Map

| Document | Purpose | Read Time |
|----------|---------|-----------|
| **README.md** | Overview & setup | 5 min |
| **SETUP_CHECKLIST.md** | Step-by-step install | 15 min |
| **README_WINDOWS.md** | Windows-specific guide | 20 min |
| **UPGRADE_GUIDE_v17.md** | How to update from v16 | 20 min |
| **IMPLEMENTATION_SUMMARY_v17.md** | What's new in v17 | 15 min |
| **TECHNICAL_ARCHITECTURE_v17.md** | Deep dive design | 30 min |
| **THIS FILE** | Quick lookup reference | 10 min |

---

## 🚀 Quick Start (2 Minutes)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
# Edit config_v17.json with your API keys and settings

# 3. Run (Terminal 1 - Scanner)
python scanner_v3.py

# 4. Run (Terminal 2 - Bot)
python bot_v17.py

# Done! Watch logs in:
tail -f logs/bot.log
```

---

## 📁 File Structure

```
bot4/
├── Core Bot Files
│   ├── bot_v17.py              ← MAIN BOT (v17.0)
│   ├── bot.py                  ← Legacy bot (v15.0)
│   └── config_v17.json         ← Bot configuration
│
├── Indicators & Analysis
│   ├── indicators_v17.py       ← Complete v17.0 indicators
│   ├── indicators_v16.py       ← Base v16.0 indicators
│   ├── ichimoku_analyzer.py    ← Ichimoku Cloud system
│   ├── volume_profile.py       ← Volume Profile & POC analysis
│   ├── signal_optimizer.py     ← Signal aggregation engine
│   └── exchange_utils.py       ← API wrapper
│
├── Scanner System
│   ├── scanner_v3.py           ← Market scanner
│   └── scanner_integration.py  ← Bot ↔ Scanner bridge
│
├── Support Files
│   ├── config.py               ← Config manager
│   ├── trade_logger.py         ← Trade database
│   ├── logger_setup.py         ← Logging setup
│   ├── utils.py                ← Utilities
│   └── requirements.txt        ← Python packages
│
└── Documentation
    ├── README.md
    ├── README_WINDOWS.md
    ├── SETUP_CHECKLIST.md
    ├── UPGRADE_GUIDE_v17.md
    ├── IMPLEMENTATION_SUMMARY_v17.md
    ├── TECHNICAL_ARCHITECTURE_v17.md
    ├── QUICK_REFERENCE_v17.md  ← YOU ARE HERE
    └── [logs directory created on first run]
```

---

## ⚙️ Configuration Quick Lookup

### config_v17.json Structure

```json
{
  "api": {
    "key": "YOUR_BYBIT_API_KEY",
    "secret": "YOUR_BYBIT_API_SECRET",
    "testnet": false
  },
  "trading": {
    "slot_size": 10,              // USDT per trade
    "drop_threshold": 0.5,        // % drop to trigger buy
    "panic_stop": 2.0,            // Emergency stop %
    "take_profit": 2.0,           // Take profit %
    "use_dynamic_stops": true,    // Use ATR-based stops
    "atr_multiplier": 1.5         // ATR stop distance
  },
  "symbols": [
    "NOT/USDT",
    "TON/USDT",
    "BNB/USDT"
  ],
  "indicators": {
    "enabled": true,
    "rsi_period": 14,
    "ema_fast": 9,
    "ema_slow": 21
  },
  "ichimoku": {
    "enabled": true,
    "tenkan_period": 9,
    "kijun_period": 26,
    "senkou_period": 52
  },
  "volume_profile": {
    "enabled": true,
    "bins": 20,
    "lookback": 100
  },
  "signal_optimizer": {
    "enabled": true,
    "min_confidence_threshold": 60
  },
  "scanner": {
    "enabled": true,
    "file": "hot_symbols.txt",
    "cache_ttl": 600
  }
}
```

### Key Settings Explained

| Setting | Default | Range | Effect |
|---------|---------|-------|--------|
| `slot_size` | 10 USDT | 1-1000 | Position size per trade |
| `drop_threshold` | 0.5% | 0.1-5% | Trigger buy when price drops this much |
| `panic_stop` | 2.0% | 1-5% | Emergency exit loss limit |
| `take_profit` | 2.0% | 1-5% | Automatic profit target |
| `rsi_period` | 14 | 7-21 | RSI sensitivity (lower=faster) |
| `ema_fast` | 9 | 5-13 | Fast EMA period |
| `ema_slow` | 21 | 15-30 | Slow EMA period |
| `min_confidence_threshold` | 60% | 40-90% | Minimum signal strength to trade |

---

## 🎯 Common Tasks

### Check Bot Status
```bash
# Real-time logs
tail -f logs/bot.log

# Last 20 trades
tail -20 logs/bot.log | grep "PROFIT\|ENTERED"

# Today's profit
grep "PROFIT TAKEN" logs/bot.log | tail -1

# Error count
grep "ERROR" logs/bot.log | wc -l
```

### Check Scanner Status
```bash
# Scanner log
tail -f scanner.log

# Current hot symbols
cat hot_symbols.txt

# Fresh scanner output
head -20 scanner.log
```

### Restart Bot
```bash
# Stop current bot (Ctrl+C in terminal 2)

# Then restart
python bot_v17.py
```

### Switch Between Versions
```bash
# Run v17 (recommended - with Ichimoku + Volume Profile)
python bot_v17.py

# Run v16 (basic version - Stochastic + ATR only)
python bot.py
```

### Change Symbols
```bash
# Edit config_v17.json, update "symbols" array
# Then restart bot (Ctrl+C, then python bot_v17.py)
```

### Adjust Confidence Threshold
```json
// More conservative (fewer trades, higher quality)
"min_confidence_threshold": 70

// More aggressive (more trades, higher risk)
"min_confidence_threshold": 50

// Balanced (default)
"min_confidence_threshold": 60
```

---

## 📊 Understanding Signals

### Signal Strength Scale

```
Score  | Confidence | Recommendation | Action
-------|------------|----------------|--------
11.5   | 100%       | STRONG BUY     | ✅ Enter trade
10+    | 87%+       | BUY            | ✅ Enter trade
7-9    | 61-78%     | CAUTION        | ⚠️ Weak signal
< 7    | < 61%      | SKIP           | ❌ Do not enter
```

### Component Weights

| Indicator | Weight | What It Checks |
|-----------|--------|-----------------|
| RSI | 2.0 | Oversold (< 30) / Overbought (> 70) |
| EMA | 2.0 | Price trend alignment |
| MACD | 1.0 | Momentum direction |
| Stochastic | 3.0 | Momentum reversal (highest weight!) |
| Ichimoku | 2.0 | Trend strength + support/resistance |
| Volume | 1.5 | POC price levels + trend |

### Interpreting Log Output

```
📉 SIGNAL: NOT/USDT dropped +0.87%
   RSI: 28.5 | EMA9: $0.00851 | EMA21: $0.00849
   Stochastic K: 18.5 | D: 22.1
   
Explanation:
✅ RSI 28.5  → Oversold (bullish!) [+2.0 weight]
✅ EMA9 > EMA21 → Bullish alignment [+2.0 weight]
✅ Stochastic K < D crossing up → Momentum turning [+3.0 weight]
⚠️ MACD still bearish [+0 weight]
✅ Ichimoku price > cloud → Bullish setup [+2.0 weight]
✅ Volume at POC → Liquidity zone [+1.5 weight]
────────────────
Total: 11.5/11.5 = 100% confidence → STRONG BUY
```

---

## 🔧 Troubleshooting Quick Guide

### Bot Won't Start

```
Error: Module not found
→ pip install -r requirements.txt

Error: API key invalid
→ Check config_v17.json, make sure key is correct

Error: No data on symbol
→ Symbol might not exist on Bybit, remove from config
```

### Scanner Creates Empty hot_symbols.txt

```
Issue: Scanner running but no opportunities found

Reasons:
1. Market not moving (BTC sideways)
   → Wait for volatility spike

2. RSI filter too strict
   → In scanner_v3.py, adjust RSI threshold

3. Volume requirements too high
   → Lower RVOL_THRESHOLD in scanner_v3.py

Solution: Reduce filters in scanner_v3.py:
  HYPE_THRESHOLD = 3.0  (↓ from 5.0)
  RVOL_MIN = 1.5        (↓ from 2.0)
```

### Bot Entering Too Many Trades

```
Issue: Too many false signals

Solutions:
1. Increase confidence threshold:
   "min_confidence_threshold": 70  (↑ from 60)

2. Increase drop_threshold:
   "drop_threshold": 1.0  (↑ from 0.5%)

3. Disable some indicators in signal_optimizer.py:
   # Reduce weight or skip indicator
```

### Bot Not Entering Enough Trades

```
Issue: Too few signals

Solutions:
1. Decrease confidence threshold:
   "min_confidence_threshold": 50  (↓ from 60)

2. Decrease drop_threshold:
   "drop_threshold": 0.2  (↓ from 0.5%)

3. Add more symbols in config_v17.json
```

### Ichimoku Not Calculating

```
Error: "Ichimoku calculation error"

Reasons:
1. OHLCV data < 52 candles required
   → More data will accumulate after running

2. NaN values in data
   → Normal on first run, will resolve

3. Stalled price data
   → Check exchange connection
```

### Volume Profile Errors

```
Error: "Volume profile calculation failed"

Reasons:
1. Insufficient volume data
   → Requires 100+ candles (lookback period)

2. All same price
   → Normal for new coins, skip or use more data

Solutions:
- Lower lookback period in config_v17.json
- Increase bin count for better resolution
```

---

## 📈 Performance Tuning

### For Volatile Markets (Small Caps)
```json
{
  "drop_threshold": 0.3,
  "take_profit": 1.5,
  "use_dynamic_stops": true,
  "atr_multiplier": 1.5,
  "min_confidence_threshold": 65
}
```

### For Stable Markets (Large Caps)
```json
{
  "drop_threshold": 1.0,
  "take_profit": 3.0,
  "use_dynamic_stops": true,
  "atr_multiplier": 2.0,
  "min_confidence_threshold": 55
}
```

### For Conservative Trading
```json
{
  "drop_threshold": 2.0,
  "take_profit": 5.0,
  "panic_stop": 3.0,
  "min_confidence_threshold": 75
}
```

### For Aggressive Trading
```json
{
  "drop_threshold": 0.1,
  "take_profit": 1.0,
  "panic_stop": 1.0,
  "min_confidence_threshold": 45
}
```

---

## 🔍 Monitoring & Metrics

### Key Metrics to Track

```
1. Win Rate
   = (Profitable Trades / Total Trades) × 100
   Target: > 60%

2. Average Profit
   = Total Profit / Profitable Trades
   Target: > 0.5% per trade

3. False Signal Rate
   = (Losing Trades / Total Trades) × 100
   Target: < 40%

4. Trade Frequency
   = Total Trades / Hours Running
   Target: 2-5 trades per hour (varies by market)

5. Drawdown
   = (Peak Profit - Current Value) / Peak Profit
   Target: < 10%
```

### How to Calculate (Example)

```bash
# Count total trades
grep "PROFIT TAKEN\|STOP OUT" logs/bot.log | wc -l
→ 50 trades

# Count profitable
grep "PROFIT TAKEN" logs/bot.log | wc -l
→ 35 trades

# Win Rate = 35/50 = 70% ✅

# Total profit
tail -1 logs/bot.log | grep "Session"
→ Session: +$245.50

# Average profit = 245.50 / 35 = $7.01 per trade ✅
```

---

## 📞 When Things Go Wrong

### Emergency Stop

```bash
# Stop bot immediately
Ctrl + C  (in terminal with bot)

# Stop scanner
Ctrl + C  (in terminal with scanner)
```

### Restore Previous Version

```bash
# Switch from v17 to v16
python bot.py  (uses indicators_v16.py)

# Or switch back to original config
cp config.json config_v17_backup.json
cp config_backup.json config.json
python bot.py
```

### Check Logs for Errors

```bash
# Show last error
tail -f logs/bot.log | grep "ERROR"

# Show specific time period
grep "14:30" logs/bot.log  # All messages at 14:30

# Count errors
grep "ERROR" logs/bot.log | wc -l
```

---

## 🎓 Learning Resources

### Understanding Ichimoku Cloud
1. **Tenkan (Conversion Line)** = 9-day midpoint
   - Fast moving average
   - Indicates short-term direction

2. **Kijun (Base Line)** = 26-day midpoint
   - Medium-term direction
   - Used for trend confirmation

3. **Cloud (Senkou Spans)** = Future support/resistance
   - Spans A & B form the cloud
   - Thicker cloud = stronger support

4. **Chikou Span** = Lagging line (shifted back 26 periods)
   - Confirms trend if above price
   - Divergence signals potential reversal

### Understanding Volume Profile
1. **POC (Point of Control)** = Highest volume price
   - Most traded price = liquidity zone
   - Support/resistance level

2. **Value Area** = 70% of volume
   - Traders' comfortable range
   - Breaks indicate trend change

3. **Volume Clusters** = Multiple high-volume zones
   - Price attracted to these levels
   - Act as magnets

---

## ✅ Pre-Launch Checklist

- [ ] API keys configured in config_v17.json
- [ ] Symbols list updated (at least 3 symbols)
- [ ] Small slot_size for testing (5-10 USDT)
- [ ] Indicators enabled in config
- [ ] Scanner running (Terminal 1)
- [ ] Bot starting without errors (Terminal 2)
- [ ] Logs showing signal calculations
- [ ] First trade executed or waiting for signal
- [ ] Monitoring setup (watching logs)
- [ ] Emergency stop procedure understood (Ctrl+C)

---

## 📊 Files Version Map

```
File Name              | v16.0 | v17.0 | Status
-----------------------|-------|-------|--------
bot.py                 | ✅    | ⚠️    | Legacy (use bot_v17.py)
bot_v17.py             | -     | ✅    | Current (recommended)
indicators_v16.py      | ✅    | ✅    | Compatible with v16
indicators_v17.py      | -     | ✅    | Current (with Ichimoku)
ichimoku_analyzer.py   | -     | ✅    | New in v17
volume_profile.py      | -     | ✅    | New in v17
signal_optimizer.py    | -     | ✅    | New in v17
config.json            | ✅    | ⚠️    | Use config_v17.json
config_v17.json        | -     | ✅    | Current config

RECOMMENDED:
→ Use bot_v17.py + config_v17.json for latest features
→ Use bot.py + config.json only for legacy compatibility
```

---

## 🚀 Next Steps After Setup

1. **Week 1:** Monitor bot performance, check trade quality
2. **Week 2:** Optimize settings based on your symbols
3. **Week 3:** Consider enabling additional features (if disabled)
4. **Week 4:** Evaluate profitability, adjust confidence threshold

---

## 📞 Support

For issues not covered here:
1. Check **SETUP_CHECKLIST.md** (detailed guide)
2. Review **UPGRADE_GUIDE_v17.md** (if upgrading from v16)
3. Check **TECHNICAL_ARCHITECTURE_v17.md** (deep dive)
4. Review error logs: `tail -f logs/bot.log | grep ERROR`

---

**Status:** ✅ **Production Ready**  
**Last Tested:** 2026-05-11  
**Stability:** High  
**Recommended:** YES ✅

🚀 **Happy trading!**
