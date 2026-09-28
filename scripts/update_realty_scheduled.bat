@echo off
REM ────────────────────────────────────────────────────────────────────
REM Runner для Task Scheduler. Вызывается ежедневно в 06:00.
REM
REM Что делает:
REM   - Лог wrapper: data\processed\etl_<date-time-guid>.log
REM   - Канонический лог и JSON истории создаёт update_realty.py в logs/ и data/processed/realty_update_runs/.
REM   - Запускает update_realty.py с per-dev обходом квартирографии
REM     при каждом плановом прогоне. Это нужно, чтобы площади квартир
REM     по девелоперам не отставали от агрегатов.
REM   - Уведомление в TDM шлёт сам update_realty.py
REM
REM Запуск вручную (для отладки):
REM   scripts\update_realty_scheduled.bat
REM ────────────────────────────────────────────────────────────────────

setlocal DisableDelayedExpansion
cd /d "%~dp0\.."

REM Дата YYYY-MM-DD для лога. Использует %DATE% — формат зависит от
REM локали Windows. Универсальный способ через PowerShell:
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "[DateTime]::Now.ToString('yyyyMMdd_HHmmss_ffffff') + '_' + [Guid]::NewGuid().ToString('N')"') do set "RUN_STAMP=%%i"
if not defined RUN_STAMP exit /b 2

set LOG_DIR=data\processed
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG_FILE=%LOG_DIR%\etl_%RUN_STAMP%.log"

echo. >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"
echo  Scheduled run: %DATE% %TIME% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

REM Загружаем .env если есть (TDM_BOT_TOKEN/TDM_CHAT_ID и пр.)
if exist .env (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
    REM for /f skips comments starting with #; delayed expansion stays off for secrets with !.
    if not "%%a"=="" set "%%a=%%b"
  )
)

REM Выбираем Python: venv приоритетнее системного py
set PY_EXE=py
if exist ".venv\Scripts\python.exe" set PY_EXE=.venv\Scripts\python.exe

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

REM Запуск с per-dev обходом квартирографии
"%PY_EXE%" scripts\update_realty.py %* >> "%LOG_FILE%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo. >> "%LOG_FILE%"
echo Exit code: %EXIT_CODE% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

REM Keep 20 wrapper logs; exact generated names only, never unrelated etl files.
powershell -NoProfile -Command "Get-ChildItem -LiteralPath 'data\processed' -File -Filter 'etl_*.log' | Where-Object { $_.Name -match '^etl_(\d{8}_\d{6}_\d{6}_[0-9a-f]{32}|\d{4}-\d{2}-\d{2})\.log$' } | Sort-Object Name -Descending | Select-Object -Skip 20 | ForEach-Object { Remove-Item -LiteralPath $_.FullName -ErrorAction SilentlyContinue }"

endlocal & exit /b %EXIT_CODE%
