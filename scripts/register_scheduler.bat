@echo off
REM ────────────────────────────────────────────────────────────────────
REM Регистрация ежедневного автозапуска парсеров недвижимости
REM в Windows Task Scheduler. Запускать ОТ ИМЕНИ АДМИНИСТРАТОРА.
REM
REM Что регистрирует:
REM   - Задача "parser_etl_realty"
REM   - Расписание: ежедневно в 06:00 (МСК / локальное время)
REM   - Команда:    scripts\update_realty_scheduled.bat
REM   - Логи:       data\processed\etl_<YYYY-MM-DD>.log
REM
REM Использование:
REM   register_scheduler.bat                  ← регистрация
REM   register_scheduler.bat --unregister     ← удаление задачи
REM
REM После регистрации можно проверить:
REM   schtasks /Query /TN "parser_etl_realty"
REM
REM Запустить вручную для теста (без ожидания 06:00):
REM   schtasks /Run /TN "parser_etl_realty"
REM ────────────────────────────────────────────────────────────────────

setlocal
set TASK_NAME=parser_etl_realty
set PROJECT_DIR=%~dp0..
for %%I in ("%PROJECT_DIR%") do set PROJECT_DIR=%%~fI
set RUNNER=%PROJECT_DIR%\scripts\update_realty_scheduled.bat

if "%~1"=="--unregister" goto :unregister

REM Проверка прав
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Запусти этот скрипт ОТ ИМЕНИ АДМИНИСТРАТОРА
  echo         (правый клик -^> Run as administrator)
  echo.
  pause
  exit /b 1
)

if not exist "%RUNNER%" (
  echo [ERROR] Не найден runner: %RUNNER%
  exit /b 1
)

echo ─────────────────────────────────────────────────────────────
echo  Регистрация задачи "%TASK_NAME%"
echo  Расписание: ежедневно в 06:00
echo  Runner:    %RUNNER%
echo ─────────────────────────────────────────────────────────────
echo.

schtasks /Create /SC DAILY /ST 06:00 ^
  /TN "%TASK_NAME%" ^
  /TR "\"%RUNNER%\"" ^
  /RL HIGHEST /F

if errorlevel 1 (
  echo.
  echo [ERROR] Не удалось зарегистрировать задачу
  exit /b 1
)

echo.
echo [OK] Задача "%TASK_NAME%" зарегистрирована
echo.
echo Проверить:    schtasks /Query /TN "%TASK_NAME%"
echo Запустить:    schtasks /Run /TN "%TASK_NAME%"
echo Удалить:      %~nx0 --unregister
echo.
endlocal
exit /b 0

:unregister
schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 (
  echo [ERROR] Не удалось удалить задачу (возможно уже удалена)
  exit /b 1
)
echo [OK] Задача "%TASK_NAME%" удалена
endlocal
exit /b 0
