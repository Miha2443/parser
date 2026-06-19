@echo off
REM Manual data update. Same as the daily cron does.

setlocal
cd /d %~dp0

if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

.venv\Scripts\python.exe scripts\update_realty.py %*

endlocal
