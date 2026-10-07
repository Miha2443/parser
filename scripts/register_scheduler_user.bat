@echo off
REM Current-user compatibility wrapper. Requires an active user session.
REM Canonical task name in both wrappers: parser_etl_realty.
setlocal
if "%~1"=="--unregister" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_realty_task.ps1" -Unregister
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_realty_task.ps1" %*
)
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
