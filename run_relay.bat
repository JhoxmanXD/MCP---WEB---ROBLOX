@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m local.relay_client
) else (
  python -m local.relay_client
)
if errorlevel 1 pause
