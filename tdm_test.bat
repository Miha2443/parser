@echo off
REM Проверка TDM-бота: список групп + тестовое сообщение.

setlocal
cd /d %~dp0

if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv не найден. Запусти сначала setup.bat
    pause
    exit /b 1
)

echo === Список групп бота ===
.venv\Scripts\python.exe -m pipeline.tdm_notify --groups
echo.
echo === Тестовое сообщение ===
.venv\Scripts\python.exe -m pipeline.tdm_notify --test
echo.
pause
endlocal
