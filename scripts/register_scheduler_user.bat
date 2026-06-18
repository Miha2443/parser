@echo off
REM --------------------------------------------------------------------
REM Register daily task FOR CURRENT USER.
REM Does NOT require administrator rights.
REM
REM LIMITATION: the task only runs when the user is logged in.
REM   If the computer is turned off at night or the user logs out -
REM   the task will NOT run at 06:00.
REM
REM Alternative for guaranteed execution: register_scheduler.bat
REM (requires admin, but runs even when user is not logged in).
REM
REM Usage:
REM   register_scheduler_user.bat              - register
REM   register_scheduler_user.bat --unregister - remove
REM --------------------------------------------------------------------

setlocal
set TASK_NAME=parser_etl_realty_user
set PROJECT_DIR=%~dp0..
for %%I in ("%PROJECT_DIR%") do set PROJECT_DIR=%%~fI
set RUNNER=%PROJECT_DIR%\scripts\update_realty_scheduled.bat

if "%~1"=="--unregister" goto :unregister

if not exist "%RUNNER%" (
  echo [ERROR] Runner not found: %RUNNER%
  exit /b 1
)

echo ------------------------------------------------------------
echo  Registering task "%TASK_NAME%" (current user)
echo  Schedule: daily at 06:00
echo  Runner:   %RUNNER%
echo ------------------------------------------------------------
echo.

REM /SC DAILY /ST 06:00 without /RL HIGHEST - regular rights
schtasks /Create /SC DAILY /ST 06:00 ^
  /TN "%TASK_NAME%" ^
  /TR "\"%RUNNER%\"" ^
  /F

if errorlevel 1 (
  echo.
  echo [ERROR] Failed to register the task
  exit /b 1
)

echo.
echo [OK] Task "%TASK_NAME%" registered
echo.
echo NOTE: The task only runs when you are logged in.
echo       If you turn off the computer at night - it will NOT run.
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
  echo [ERROR] Failed to remove task
  exit /b 1
)
echo [OK] Task "%TASK_NAME%" removed
endlocal
exit /b 0
