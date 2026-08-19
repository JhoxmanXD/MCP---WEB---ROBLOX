@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo =========================================
echo       MCP-WEB RELAY STOPPER
echo =========================================
echo.
echo Buscando relay_client ocultos...

powershell -NoProfile -ExecutionPolicy Bypass -Command "$targets = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -match 'local\.relay_client' }); if ($targets.Count -eq 0) { Write-Host '[RELAY] No hay procesos relay_client activos.'; exit 0 }; foreach ($target in $targets) { Write-Host ('[RELAY] Cerrando PID ' + $target.ProcessId); Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Host ('[RELAY] Procesos cerrados: ' + $targets.Count)"

echo.
echo Roblox Studio y el MCP existente no fueron modificados.
pause
