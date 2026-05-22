@echo off
REM Точка входа для Windows Task Scheduler.
REM Рекомендуемое расписание: ежедневно в 06:00 МСК.
REM
REM Регистрация (одной командой в cmd от имени админа):
REM   schtasks /Create /SC DAILY /ST 06:00 /TN "parser_etl" /TR "C:\cloud\scripts\update_all.bat" /RL HIGHEST /F

setlocal
cd /d %~dp0\..
if not exist data\processed mkdir data\processed
py pipeline\orchestrator.py >> data\processed\etl.log 2>&1
exit /b %ERRORLEVEL%
