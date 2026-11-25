@echo off
rem PC daily runs (scheduled tasks registered by setup_task.ps1):
rem   run_daily.bat close   weekdays 15:40 (stockDB_close): regular-session bars (REG, 1-minute bars up to the
rem                         closing auction) + interim Kiwoom daily bars -> upload -> dashboard updates ~16:37
rem   run_daily.bat         weekdays 20:15 (stockDB_daily): final Kiwoom daily bars (KRX, AL = last trade incl.
rem                         after-hours/NXT, record only), REG for any day the 15:40 run missed, indicators -> upload
rem Kiwoom keys work only from IPs registered on the Kiwoom portal, so Korean prices are collected here and only
rem the Kiwoom tables are uploaded; GitHub Actions loads them and rebuilds the dashboard.
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
echo ===== %date% %time% %1 >> "%LOG%"
if not exist "%STOCKDB_PY%" (
  echo [!] Python not found: %STOCKDB_PY% >> "%LOG%"
  exit /b 1
)
if /i "%1"=="close" (
  "%STOCKDB_PY%" -m src.prices.kiwoom_kr daily --venue close >> "%LOG%" 2>&1
) else (
  "%STOCKDB_PY%" -m src.prices.run_prices --only kr >> "%LOG%" 2>&1
)
if errorlevel 1 (
  echo [!] collection failed >> "%LOG%"
  exit /b 1
)
"%STOCKDB_PY%" cloud_state.py push-kiwoom --db "%STOCKDB_DATA%\market.duckdb" --archive "%STOCKDB_DATA%\kiwoom-state.zip" >> "%LOG%" 2>&1
echo upload exit %errorlevel% >> "%LOG%"
exit /b %errorlevel%
