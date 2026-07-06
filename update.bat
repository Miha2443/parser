@echo off
REM Manual data update. Same as the daily cron does.

setlocal
cd /d %~dp0

if exist .venv\Scripts\python.exe (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

"%PY%" scripts\update_realty.py %*

endlocal
