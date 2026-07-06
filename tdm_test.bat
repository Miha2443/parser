@echo off
REM TDM bot check: list groups + send test message.

setlocal
cd /d "%~dp0"

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

echo === Bot groups ===
".venv\Scripts\python.exe" -m pipeline.tdm_notify --groups
echo.
echo === Test message ===
".venv\Scripts\python.exe" -m pipeline.tdm_notify --test
echo.
pause
endlocal
