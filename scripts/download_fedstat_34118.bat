@echo off
REM Strict live download for Fedstat 34118 part1/part2 with a saved log.

setlocal
cd /d "%~dp0\.."

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

"%PY%" scripts\download_fedstat_34118.py
exit /b %ERRORLEVEL%
