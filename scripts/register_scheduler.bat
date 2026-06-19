@echo off
REM --------------------------------------------------------------------
REM Register daily auto-run of the realty parsers in Windows Task
REM Scheduler. RUN AS ADMINISTRATOR.
REM
REM What is registered:
REM   - Task "parser_etl_realty"
REM   - Schedule: daily at 06:00 (local time)
REM   - Command:  scripts\update_realty_scheduled.bat
REM   - Logs:     data\processed\etl_<YYYY-MM-DD>.log
REM
REM Usage:
REM   register_scheduler.bat                  - register
REM   register_scheduler.bat --unregister     - remove task
REM
REM After registration check with:
REM   schtasks /Query /TN "parser_etl_realty"
REM
REM Run manually for testing (without waiting until 06:00):
REM   schtasks /Run /TN "parser_etl_realty"
REM --------------------------------------------------------------------

setlocal
set TASK_NAME=parser_etl_realty
set PROJECT_DIR=%~dp0..
for %%I in ("%PROJECT_DIR%") do set PROJECT_DIR=%%~fI
set RUNNER=%PROJECT_DIR%\scripts\update_realty_scheduled.bat

if "%~1"=="--unregister" goto :unregister

REM Permission check
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Run this script AS ADMINISTRATOR
  echo         (right click -^> Run as administrator)
  echo.
  pause
  exit /b 1
)

if not exist "%RUNNER%" (
  echo [ERROR] Runner not found: %RUNNER%
  exit /b 1
)

echo ------------------------------------------------------------
echo  Registering task "%TASK_NAME%"
echo  Schedule: daily at 06:00
echo  Runner:   %RUNNER%
echo ------------------------------------------------------------
echo.

schtasks /Create /SC DAILY /ST 06:00 ^
  /TN "%TASK_NAME%" ^
  /TR "\"%RUNNER%\"" ^
  /RL HIGHEST /F

if errorlevel 1 (
  echo.
  echo [ERROR] Failed to register the task
  exit /b 1
)

echo.
echo [OK] Task "%TASK_NAME%" registered
echo.
echo Check:    schtasks /Query /TN "%TASK_NAME%"
echo Run:      schtasks /Run /TN "%TASK_NAME%"
echo Remove:   %~nx0 --unregister
echo.
endlocal
exit /b 0

:unregister
schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 (
  echo [ERROR] Failed to remove task (maybe already removed)
  exit /b 1
)
echo [OK] Task "%TASK_NAME%" removed
endlocal
exit /b 0
