@echo off
setlocal
cd /d "%~dp0\.."

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo [1/6] python compile checks
"%PY%" scripts\check_python_compile.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_windows_wrappers.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_selenium_download_wait.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_kvart_resume.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_nashdom_contract.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_nashdom_atomic_json.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_erzrf_atomic_outputs.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_erzrf_downloader_contract.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_erzrf_parsers.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_realty_mart_require.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_realty_mart_manifest_pickle.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_realty_mart_atomic_write.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_realty_manifest_atomic_write.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_realty_status_atomic_write.py || exit /b %ERRORLEVEL%

echo [2/6] streamlit page runtime smoke
"%PY%" scripts\check_streamlit_pages_smoke.py || exit /b %ERRORLEVEL%

echo [3/6] update_realty planning checks
"%PY%" scripts\check_update_realty_plan.py || exit /b %ERRORLEVEL%
"%PY%" scripts\check_realty_status_summary.py || exit /b %ERRORLEVEL%

echo [4/6] realty update status check
"%PY%" scripts\check_realty_update_status.py --quiet-warnings || exit /b %ERRORLEVEL%
"%PY%" scripts\check_realty_update_status_selftest.py || exit /b %ERRORLEVEL%

echo [5/6] realty mart manifest check
"%PY%" -m pipeline.build_realty_marts --check --strict || exit /b %ERRORLEVEL%

echo [6/6] realty dashboard loader smoke
"%PY%" scripts\check_realty_marts_smoke.py || exit /b %ERRORLEVEL%

echo.
echo validate_realty_dashboard: OK
