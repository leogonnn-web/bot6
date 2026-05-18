# HYDRA Trading Bot v17.0 - Production-Ready Architecture

## 📁 New Modular Structure

```
bot4-main/
├── src/                          # Main source code
│   ├── api/                      # Exchange clients
│   │   ├── __init__.py
│   │   └── bybit_client.py       # Bybit V5 API client
│   ├── core/                     # Trading logic
│   │   ├── __init__.py
│   │   ├── bot.py                # Main trading bot (state machine)
│   │   └── scanner.py            # Market scanner
│   ├── indicators/               # Technical analysis
│   │   ├── __init__.py
│   │   └── matrix.py             # All indicators (RSI, EMA, MACD, etc.)
│   └── database/                 # Data persistence
│       ├── __init__.py
│       └── models.py             # SQLite database models
├── shared/                       # Shared utilities (config, logger, utils)
├── docs/                         # Documentation (moved from root)
├── archive/                      # Old logs and files
├── main.py                       # Bot entry point
├── run_scanner.py                # Scanner entry point
├── run_bot.bat                   # Windows bot launcher
├── run_scanner.bat               # Windows scanner launcher
└── requirements.txt              # Python dependencies
```

## 🚀 Quick Start

### Run Bot
```bash
# Windows
run_bot.bat

# Or directly
python main.py
```

### Run Scanner
```bash
# Windows
run_scanner.bat

# Or directly
python run_scanner.py
```

## 📦 Module Overview

### src/api/bybit_client.py
- Pure Bybit V5 API client
- REST + WebSocket support
- No business logic - only exchange communication

### src/core/bot.py
- Main trading bot with state machine
- States: IDLE, SCANNING, BUYING, IN_POSITION, EXITING
- WebSocket stream integration
- Risk management and position handling

### src/core/scanner.py
- Market scanner for hot symbols
- HYPE/DUMP detection
- RSI, EMA, RVOL analysis
- Integration with bot via hot_symbols.txt

### src/indicators/matrix.py
- Complete technical analysis suite
- RSI, EMA, MACD, Stochastic, ATR
- Ichimoku Cloud
- Volume Profile & POC
- Signal Optimizer (aggregation & conflict resolution)

### src/database/models.py
- SQLite database for trade logging
- Session statistics (PnL, win rate)
- FIFO trade matching

## 🔧 Configuration

Edit `.env` file:
```
BYBIT_API_KEY=your_key_here
BYBIT_API_SECRET=your_secret_here
LOG_LEVEL=INFO
```

Edit `shared/config.json` for trading parameters.

## 📊 Architecture Benefits

✅ **Separation of Concerns** - Each module has single responsibility
✅ **Testability** - Isolated components easy to unit test
✅ **Maintainability** - Clear structure, easy to navigate
✅ **Scalability** - Easy to add new indicators or exchanges
✅ **Production-Ready** - Clean code, proper error handling

## 🔄 Migration Notes

- Old v16/v17 folders removed
- Documentation moved to `docs/`
- Old logs moved to `archive/`
- All imports updated to use new structure
- Batch files updated for new entry points
