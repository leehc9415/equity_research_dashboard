@echo off
rem PC daily run (scheduled task "stockDB_daily", weekdays 20:15; see setup_task.ps1).
rem Kiwoom keys work only from IPs registered on the Kiwoom portal, so Korean prices are collected here
rem and only the Kiwoom tables are uploaded; GitHub Actions loads them and rebuilds the dashboard.
rem   1. Kiwoom daily bars (KRX, AL = last trade incl. after-hours/NXT) + regular-session bars (REG, 1-minute bars)
rem   2. upload prices_kiwoom / kiwoom_listing to the market-data-state release
rem Needs user environment variables: KIWOOM_APPKEY, KIWOOM_SECRETKEY, GH_TOKEN (this repository, Contents read/write)
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not defined STOCKDB_DATA set "STOCKDB_DATA=%USERPROFILE%\Documents\stockDB\data"
if not defined STOCKDB_PY set "STOCKDB_PY=%USERPROFILE%\Documents\stockDB\.venv\Scripts\python.exe"
set "GITHUB_REPOSITORY=leehc9415/equity_research_dashboard"
set "LOGDIR=%STOCKDB_DATA%\..\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\daily.log"
echo ===== %date% %time% >> "%LOG%"
if not exist "%STOCKDB_PY%" (
  echo [!] Python not found: %STOCKDB_PY% >> "%LOG%"
  exit /b 1
)
"%STOCKDB_PY%" -m src.prices.run_prices --only kr >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [!] run_prices failed, exit %errorlevel% >> "%LOG%"
  exit /b 1
)
"%STOCKDB_PY%" cloud_state.py push-kiwoom --db "%STOCKDB_DATA%\market.duckdb" --archive "%STOCKDB_DATA%\kiwoom-state.zip" >> "%LOG%" 2>&1
echo upload exit %errorlevel% >> "%LOG%"
exit /b %errorlevel%
