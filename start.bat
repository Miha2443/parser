@echo off
REM Launch Streamlit site. http://localhost:8501
REM Stop with Ctrl+C in this window.

setlocal
cd /d "%~dp0"

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

echo Launching Streamlit on http://localhost:8501 ...
echo Stop with Ctrl+C
echo.

".venv\Scripts\python.exe" -m streamlit run app\Home.py

endlocal
