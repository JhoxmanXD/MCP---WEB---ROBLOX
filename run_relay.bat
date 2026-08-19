@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$tcp = Test-NetConnection 127.0.0.1 -Port 8787 -WarningAction SilentlyContinue; if (-not $tcp.TcpTestSucceeded) { powershell -ExecutionPolicy Bypass -File 'C:\Users\jhoxm\OneDrive\Documentos\Roblox\tools\roblox-studio-mcp-bridge\scripts\start.ps1' -Transport streamable-http -Background; Start-Sleep -Seconds 3 }"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m local.relay_client
) else (
  python -m local.relay_client
)
if errorlevel 1 pause
