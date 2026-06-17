@echo off
REM ────────────────────────────────────────────────────────────────────
REM Регистрация ежедневной задачи В ТЕКУЩЕМ ПОЛЬЗОВАТЕЛЕ.
REM НЕ требует прав администратора.
REM
REM ⚠️ ОГРАНИЧЕНИЕ: задача будет работать ТОЛЬКО когда юзер залогинен.
REM     Если на ночь выключаешь компьютер или выходишь из системы —
REM     задача не сработает в 06:00.
REM
REM Альтернатива для гарантированной работы → register_scheduler.bat
REM (требует админа, но работает даже когда юзер не залогинен).
REM
REM Использование:
REM   register_scheduler_user.bat              регистрация
REM   register_scheduler_user.bat --unregister удаление
REM ────────────────────────────────────────────────────────────────────

setlocal
set TASK_NAME=parser_etl_realty_user
set PROJECT_DIR=%~dp0..
for %%I in ("%PROJECT_DIR%") do set PROJECT_DIR=%%~fI
set RUNNER=%PROJECT_DIR%\scripts\update_realty_scheduled.bat

if "%~1"=="--unregister" goto :unregister

if not exist "%RUNNER%" (
  echo [ERROR] Не найден runner: %RUNNER%
  exit /b 1
)

echo ─────────────────────────────────────────────────────────────
echo  Регистрация задачи "%TASK_NAME%" (текущий пользователь)
echo  Расписание: ежедневно в 06:00
echo  Runner:    %RUNNER%
echo ─────────────────────────────────────────────────────────────
echo.

REM /SC DAILY /ST 06:00 без /RL HIGHEST — обычные права
schtasks /Create /SC DAILY /ST 06:00 ^
  /TN "%TASK_NAME%" ^
  /TR "\"%RUNNER%\"" ^
  /F

if errorlevel 1 (
  echo.
  echo [ERROR] Не удалось зарегистрировать задачу
  exit /b 1
)

echo.
echo [OK] Задача "%TASK_NAME%" зарегистрирована
echo.
echo ⚠️ Задача сработает только когда ты залогинен.
echo    Если выключаешь компьютер на ночь — не сработает.
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
  echo [ERROR] Не удалось удалить задачу
  exit /b 1
)
echo [OK] Задача "%TASK_NAME%" удалена
endlocal
exit /b 0
