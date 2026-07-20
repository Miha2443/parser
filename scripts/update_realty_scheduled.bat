@echo off
REM ────────────────────────────────────────────────────────────────────
REM Runner для Task Scheduler. Вызывается ежедневно в 06:00.
REM
REM Что делает:
REM   - Создаёт лог-файл с датой: data\processed\etl_<YYYY-MM-DD>.log
REM   - Запускает update_realty.py с per-dev обходом квартирографии
REM     при каждом плановом прогоне. Это нужно, чтобы площади квартир
REM     по девелоперам не отставали от агрегатов.
REM   - Уведомление в TDM шлёт сам update_realty.py
REM
REM Запуск вручную (для отладки):
REM   scripts\update_realty_scheduled.bat
REM ────────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion
cd /d "%~dp0\.."

REM Дата YYYY-MM-DD для лога. Использует %DATE% — формат зависит от
REM локали Windows. Универсальный способ через PowerShell:
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i

set LOG_DIR=data\processed
if not exist %LOG_DIR% mkdir %LOG_DIR%
set LOG_FILE=%LOG_DIR%\etl_%TODAY%.log

echo. >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"
echo  Scheduled run: %DATE% %TIME% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

REM Загружаем .env если есть (TDM_BOT_TOKEN/TDM_CHAT_ID и пр.)
if exist .env (
  for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    set "_line=%%a"
    REM Пропускаем комментарии и пустые строки
    if not "!_line:~0,1!"=="#" if not "%%a"=="" set "%%a=%%b"
  )
)

REM Выбираем Python: venv приоритетнее системного py
set PY_EXE=py
if exist ".venv\Scripts\python.exe" set PY_EXE=.venv\Scripts\python.exe

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

REM Запуск с per-dev обходом квартирографии
%PY_EXE% scripts\update_realty.py >> "%LOG_FILE%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo. >> "%LOG_FILE%"
echo Exit code: %EXIT_CODE% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

endlocal & exit /b %EXIT_CODE%
