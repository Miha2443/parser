@echo off
REM Полный прогон по источникам недвижимости + архивирование.
REM Запускать из корня проекта или просто двойным кликом.
REM
REM Использование:
REM   update_realty.bat              :: все источники
REM   update_realty.bat monitoring   :: только мониторинг
REM   update_realty.bat erzrf        :: только erzrf (top + cards)
REM   update_realty.bat --skip-kvart-per-dev   :: без долгого per-dev обхода
REM
REM Архивирование старых файлов происходит автоматически после прогона.

setlocal
cd /d %~dp0\..
py scripts\update_realty.py %*
endlocal
