@echo off
setlocal
cd /d "%~dp0\.."

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

set "PYTHONIOENCODING=utf-8"

echo [1/5] python compile checks
"%PY%" scripts\check_python_compile.py || exit /b %ERRORLEVEL%

echo [2/5] update_realty planning checks
"%PY%" scripts\check_update_realty_plan.py || exit /b %ERRORLEVEL%

echo [3/5] realty update status check
"%PY%" scripts\check_realty_update_status.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_realty_update_status_selftest.py || exit /b %ERRORLEVEL%

echo [4/5] realty mart manifest check
"%PY%" -m pipeline.build_realty_marts --check --strict || exit /b %ERRORLEVEL%

echo [5/5] realty dashboard loader smoke
"%PY%" scripts\check_realty_marts_smoke.py || exit /b %ERRORLEVEL%

echo.
echo validate_realty_dashboard: OK
