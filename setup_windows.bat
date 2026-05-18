@echo off
REM HYDRA Trading Bot v16.0 - Windows Setup Script
REM Automatic installation for Windows

echo.
echo ====================================
echo HYDRA Trading Bot v16.0 Setup
echo ====================================
echo.

REM Check Python version
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found!
    echo Please install Python 3.8+ from https://www.python.org/
    echo Make sure to check "Add Python to PATH"
    pause
    exit /b 1
)

echo [1/5] Python version OK
echo.

REM Create virtual environment
echo [2/5] Creating virtual environment...
if not exist venv (
    python -m venv venv
    echo Virtual environment created
) else (
    echo Virtual environment already exists
)
echo.

REM Activate virtual environment
echo [3/5] Activating virtual environment...
call venv\Scripts\activate.bat
echo.

REM Install dependencies
echo [4/5] Installing dependencies (this may take 2-3 minutes)...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)
echo.

REM Create .env file if not exists
echo [5/5] Checking configuration...
if not exist .env (
    copy .env.example .env
    echo Created .env file
    echo.
    echo WARNING: Please edit .env with your Bybit API credentials:
    echo   - BYBIT_API_KEY
    echo   - BYBIT_API_SECRET
    echo.
    pause
) else (
    echo .env file already exists
)

REM Create logs directory
if not exist logs mkdir logs
echo Logs directory ready

echo.
echo ====================================
echo SETUP COMPLETE!
echo ====================================
echo.
echo Next steps:
echo 1. Edit .env with your Bybit API credentials
echo 2. Review shared\config.json settings
echo 3. Run bot:
echo    v16\run.bat   - HYDRA v16.0
echo    v17\run.bat   - HYDRA v17.0
echo.
echo For Scanner (optional, run in separate terminal):
echo   run_scanner.bat
echo.
pause
