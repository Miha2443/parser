@echo off
chcp 65001 >nul 2>&1
REM Запуск Streamlit-сайта. Двойной клик или из cmd.
REM Сайт откроется в браузере по умолчанию (http://localhost:8501).
REM
REM Закрытие: Ctrl+C в этом окне.

setlocal
cd /d %~dp0

if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv не найден. Запусти сначала setup.bat
    pause
    exit /b 1
)

echo Запуск Streamlit на http://localhost:8501 ...
echo Закрытие: Ctrl+C
echo.

.venv\Scripts\python.exe -m streamlit run app\Home.py

endlocal
