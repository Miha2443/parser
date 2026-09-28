@echo off
REM Compatibility wrapper. Default: logged-in current user, elevated run level.
REM Use -LogonMode Password explicitly for logged-out execution (credentials required).
setlocal
if "%~1"=="--unregister" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_realty_task.ps1" -Unregister
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_realty_task.ps1" -RunLevel Highest %*
)
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
