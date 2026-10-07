@echo off
REM Rebuild fast Streamlit realty marts from data/raw/realty.

setlocal
cd /d %~dp0\..

if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -m pipeline.build_realty_marts %*
) else (
    python -m pipeline.build_realty_marts %*
)

endlocal
