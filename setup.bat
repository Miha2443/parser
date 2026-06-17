@echo off
REM ────────────────────────────────────────────────────────────────────
REM Setup на чистом Windows-компе.
REM
REM Что делает (по шагам):
REM   1. Проверка Python (нужен 3.10+)
REM   2. Проверка Chrome (для Selenium-парсеров)
REM   3. Создание venv в .venv\
REM   4. pip install -r requirements.txt
REM   5. Создание .env из .env.example если его нет
REM   6. Первичный сбор данных (update_realty.py — ~40-60 мин)
REM   7. Регистрация Task Scheduler на ежедневный запуск в 06:00
REM   8. Запуск Streamlit-сайта
REM
REM Запускать ОТ ИМЕНИ АДМИНИСТРАТОРА (нужно для Task Scheduler).
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
    if errorlevel 1 (
        echo [ERROR] Python не установлен. Скачай с https://www.python.org/
        echo         Нужен Python 3.10+. Поставь галочку «Add to PATH».
        pause
        exit /b 1
    )
    set PY=python
) else (
    set PY=py
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Нужен Python 3.10 или новее.
    %PY% --version
    pause
    exit /b 1
)
%PY% --version
echo.

REM === Шаг 2: проверка Chrome ===
echo [2/8] Проверка Chrome...
set CHROME_OK=0
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set CHROME_OK=1
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set CHROME_OK=1
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set CHROME_OK=1
if %CHROME_OK%==0 (
    echo [WARN] Chrome не найден в стандартных местах. Selenium-парсеры
    echo        (nashdom, erzrf) НЕ заработают.
    echo        Скачай с https://www.google.com/chrome/
    echo        После установки запусти setup.bat ещё раз.
    set /p _continue=Продолжить без Chrome? (y/N):
    if /i not "!_continue!"=="y" exit /b 1
) else (
    echo Chrome обнаружен.
)
echo.

REM === Шаг 3: venv ===
echo [3/8] Создание виртуального окружения (.venv\)...
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
echo [4/8] Установка зависимостей (это займёт 2-5 минут)...
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
        echo Узнать ID групп: после установки запусти
        echo   .venv\Scripts\python.exe -m pipeline.tdm_notify --groups
        echo.
        set /p _continue=Откроем .env в блокноте сейчас? (Y/n):
        if /i not "!_continue!"=="n" notepad .env
    ) else (
        echo [WARN] .env.example отсутствует, .env не создан
    )
) else (
    echo .env уже есть.
)
echo.

REM === Шаг 6: первичный сбор данных ===
if %DO_SCRAPE%==1 (
    echo [6/8] Первичный сбор данных (update_realty.py)...
    echo Это займёт ~40-60 минут. Можешь свернуть окно.
    echo.
    "%VENV_PY%" scripts\update_realty.py
    echo.
) else (
    echo [6/8] Первичный сбор данных пропущен (--no-scrape).
    echo.
)

REM === Шаг 7: Task Scheduler ===
if %DO_SCHEDULER%==1 (
    echo [7/8] Регистрация задачи в Task Scheduler...
    REM Проверка прав
    net session >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Setup запущен НЕ от админа — пропускаю Task Scheduler.
        echo        Запусти scripts\register_scheduler.bat от админа отдельно.
    ) else (
        call scripts\register_scheduler.bat
    )
) else (
    echo [7/8] Регистрация Task Scheduler пропущена (--no-scheduler).
)
echo.

REM === Шаг 8: запуск сайта ===
echo ============================================================
echo  УСТАНОВКА ЗАВЕРШЕНА
echo ============================================================
echo.
echo Дальше:
echo   - Запустить сайт:        start.bat
echo   - Обновить данные:       update.bat
echo   - Тест TDM-бота:         tdm_test.bat
echo   - Список групп TDM:      .venv\Scripts\python.exe -m pipeline.tdm_notify --groups
echo.
echo Сайт будет автоматически обновляться каждый день в 06:00.
echo.
if %DO_START%==1 (
    set /p _start=Запустить сайт сейчас? (Y/n):
    if /i not "!_start!"=="n" call start.bat
)
endlocal
exit /b 0
