@echo off
chcp 65001 >nul 2>&1
REM Ручной запуск обновления всех источников.
REM Аналог того что Task Scheduler делает в 06:00.

setlocal
cd /d %~dp0

if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv не найден. Запусти сначала setup.bat
    pause
    exit /b 1
)

.venv\Scripts\python.exe scripts\update_realty.py %*

endlocal
