@echo off
REM ============================================================
REM  Setup parser dashboard on a clean Windows machine.
REM  Run as Administrator for Task Scheduler registration.
REM
REM  Usage:
REM    setup.bat                  full setup
REM    setup.bat --no-scrape      skip first data download
REM    setup.bat --no-scheduler   skip cron registration
REM    setup.bat --no-start       skip launching site at the end
REM ============================================================

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
echo  Folder: %CD%
echo ============================================================
echo.

REM --- Step 1: Python ---
echo [1/8] Python check...
where py >nul 2>nul
if errorlevel 1 (
    where python >nul 2>nul
    if errorlevel 1 goto no_python
    set PY=python
) else (
    set PY=py
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto old_python
%PY% --version
echo.
goto step2

:no_python
echo [ERROR] Python not installed. Get it from https://www.python.org/
echo         Need Python 3.10+. Check "Add to PATH" during install.
pause
exit /b 1

:old_python
echo [ERROR] Need Python 3.10 or newer.
%PY% --version
pause
exit /b 1

:step2
REM --- Step 2: Chrome ---
echo [2/8] Chrome check...
set CHROME_OK=0
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "@('C:\Program Files\Google\Chrome\Application\chrome.exe','C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',$env:LOCALAPPDATA+'\Google\Chrome\Application\chrome.exe') | Where-Object { Test-Path $_ } | Select-Object -First 1"') do (
    if not "%%i"=="" set CHROME_OK=1
)
if !CHROME_OK!==1 (
    echo Chrome found.
) else (
    echo [WARN] Chrome not found. Selenium parsers will not work.
    echo        Get it from https://www.google.com/chrome/
    set /p _continue=Continue without Chrome? [y/N]:
    if /i not "!_continue!"=="y" exit /b 1
)
echo.

REM --- Step 3: venv ---
echo [3/8] Creating .venv ...
if exist .venv (
    echo .venv already exists, skipping.
) else (
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] venv creation failed
        pause
        exit /b 1
    )
)
set VENV_PY=%CD%\.venv\Scripts\python.exe
echo Using: %VENV_PY%
echo.

REM --- Step 4: dependencies ---
echo [4/8] Installing dependencies [2-5 min]...
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    pause
    exit /b 1
)
echo Dependencies installed.
echo.

REM --- Step 5: .env ---
echo [5/8] .env check...
if not exist .env (
    if exist .env.example (
        copy .env.example .env >nul
        echo .env created from .env.example.
        echo.
        echo [IMPORTANT] Open .env and fill in secrets:
        echo   TDM_BOT_TOKEN     - bot token
        echo   TDM_WORKSPACE_ID  - workspace id
        echo   TDM_GROUP_ID      - chat id for notifications
        echo.
        echo To find IDs run after setup:  tdm_test.bat
        echo.
        set /p _continue=Open .env in notepad? [Y/n]:
        if /i not "!_continue!"=="n" notepad .env
    ) else (
        echo [WARN] .env.example missing, .env not created
    )
) else (
    echo .env already exists.
)
echo.

REM --- Step 6: first scrape ---
if !DO_SCRAPE!==1 (
    echo [6/8] First data scrape via update_realty.py ...
    echo This takes 40-60 min. You can minimize the window.
    echo.
    "%VENV_PY%" scripts\update_realty.py
    echo.
) else (
    echo [6/8] First scrape skipped --no-scrape
    echo.
)

REM --- Step 7: Task Scheduler ---
if !DO_SCHEDULER!==1 (
    echo [7/8] Registering Task Scheduler...
    net session >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Setup is NOT running as admin.
        echo        Registering per-user task. It only fires while you are logged in.
        echo.
        call scripts\register_scheduler_user.bat
        echo.
        echo For robust task run as admin:  scripts\register_scheduler.bat
    ) else (
        call scripts\register_scheduler.bat
    )
) else (
    echo [7/8] Task Scheduler skipped --no-scheduler
)
echo.

REM --- Step 8: launch site ---
echo ============================================================
echo  SETUP COMPLETE
echo ============================================================
echo.
echo Next:
echo   - Launch site:        start.bat
echo   - Refresh data:       update.bat
echo   - Test TDM bot:       tdm_test.bat
echo.
echo Site will refresh daily at 06:00.
echo.
if !DO_START!==1 (
    set /p _start=Launch site now? [Y/n]:
    if /i not "!_start!"=="n" call start.bat
)
endlocal
exit /b 0
