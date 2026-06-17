@echo off
chcp 65001 >nul 2>&1
REM ────────────────────────────────────────────────────────────────────
REM Setup на чистом Windows-компе.
REM
REM Запускать ОТ ИМЕНИ АДМИНИСТРАТОРА для регистрации Task Scheduler.
REM
REM Использование:
REM   setup.bat                       полный setup
REM   setup.bat --no-scrape           без первичного сбора (сайт сразу)
REM   setup.bat --no-scheduler        без регистрации cron
REM   setup.bat --no-start            без автозапуска сайта в конце
REM ────────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion
cd /d %~dp0

set DO_SCRAPE=1
set DO_SCHEDULER=1
set DO_START=1
:parse_args
if "%~1"=="" goto args_done
if "%~1"=="--no-scrape"    set DO_SCRAPE=0
if "%~1"=="--no-scheduler" set DO_SCHEDULER=0
if "%~1"=="--no-start"     set DO_START=0
shift
goto parse_args
:args_done

echo.
echo ============================================================
echo  Setup parser dashboard
echo  Папка: %CD%
echo ============================================================
echo.

REM === Шаг 1: проверка Python ===
echo [1/8] Проверка Python...
where py >nul 2>nul
if errorlevel 1 (
    where python >nul 2>nul
    if errorlevel 1 goto :no_python
    set PY=python
) else (
    set PY=py
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto :old_python
%PY% --version
echo.
goto :step2

:no_python
echo [ERROR] Python не установлен. Скачай с https://www.python.org/
echo         Нужен Python 3.10+. Поставь галочку «Add to PATH».
pause
exit /b 1

:old_python
echo [ERROR] Нужен Python 3.10 или новее.
%PY% --version
pause
exit /b 1

:step2
REM === Шаг 2: проверка Chrome ===
echo [2/8] Проверка Chrome...
set CHROME_OK=0
REM Используем PowerShell — он умеет искать chrome.exe в разных местах
REM без проблем со скобками в путях.
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "@('C:\Program Files\Google\Chrome\Application\chrome.exe','C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',$env:LOCALAPPDATA+'\Google\Chrome\Application\chrome.exe') | Where-Object { Test-Path $_ } | Select-Object -First 1"') do (
    if not "%%i"=="" set CHROME_OK=1
)
if !CHROME_OK!==1 (
    echo Chrome обнаружен.
) else (
    echo [WARN] Chrome не найден. Selenium-парсеры nashdom, erzrf не заработают.
    echo        Скачай: https://www.google.com/chrome/
    set /p _continue=Продолжить без Chrome? [y/N]:
    if /i not "!_continue!"=="y" exit /b 1
)
echo.

REM === Шаг 3: venv ===
echo [3/8] Создание виртуального окружения .venv ...
if exist .venv (
    echo .venv уже существует, пропускаю.
) else (
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Не удалось создать venv
        pause
        exit /b 1
    )
)
set VENV_PY=%CD%\.venv\Scripts\python.exe
echo Используем: %VENV_PY%
echo.

REM === Шаг 4: зависимости ===
echo [4/8] Установка зависимостей (2-5 минут)...
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Не удалось установить зависимости
    pause
    exit /b 1
)
echo Зависимости установлены.
echo.

REM === Шаг 5: .env ===
echo [5/8] Проверка .env...
if not exist .env (
    if exist .env.example (
        copy .env.example .env >nul
        echo .env создан из .env.example.
        echo.
        echo [ВАЖНО] Открой .env и впиши секреты:
        echo   TDM_BOT_TOKEN     - токен бота TDM
        echo   TDM_WORKSPACE_ID  - ID пространства
        echo   TDM_GROUP_ID      - ID чата куда слать уведомления
        echo.
        echo Узнать ID групп после установки:
        echo   tdm_test.bat
        echo.
        set /p _continue=Открыть .env в блокноте? [Y/n]:
        if /i not "!_continue!"=="n" notepad .env
    ) else (
        echo [WARN] .env.example отсутствует, .env не создан
    )
) else (
    echo .env уже есть.
)
echo.

REM === Шаг 6: первичный сбор данных ===
if !DO_SCRAPE!==1 (
    echo [6/8] Первичный сбор данных update_realty.py...
    echo Это займёт ~40-60 минут. Можешь свернуть окно.
    echo.
    "%VENV_PY%" scripts\update_realty.py
    echo.
) else (
    echo [6/8] Первичный сбор данных пропущен --no-scrape
    echo.
)

REM === Шаг 7: Task Scheduler ===
if !DO_SCHEDULER!==1 (
    echo [7/8] Регистрация задачи в Task Scheduler...
    net session >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Setup запущен НЕ от админа.
        echo        Регистрирую задачу в режиме «текущий пользователь».
        echo        Будет работать только когда ты залогинен.
        echo.
        call scripts\register_scheduler_user.bat
        echo.
        echo Для надёжной задачи запусти от админа: scripts\register_scheduler.bat
    ) else (
        call scripts\register_scheduler.bat
    )
) else (
    echo [7/8] Регистрация Task Scheduler пропущена --no-scheduler
)
echo.

REM === Шаг 8: запуск сайта ===
echo ============================================================
echo  УСТАНОВКА ЗАВЕРШЕНА
echo ============================================================
echo.
echo Дальше:
echo   - Запустить сайт:      start.bat
echo   - Обновить данные:     update.bat
echo   - Тест TDM-бота:       tdm_test.bat
echo.
echo Сайт будет обновляться каждый день в 06:00.
echo.
if !DO_START!==1 (
    set /p _start=Запустить сайт сейчас? [Y/n]:
    if /i not "!_start!"=="n" call start.bat
)
endlocal
exit /b 0
