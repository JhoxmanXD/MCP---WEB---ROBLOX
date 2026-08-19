@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo =========================================
echo         MCP-WEB ROBLOX LAUNCHER
echo =========================================
echo.

set "PYTHON_CMD="
python -c "import sys; print(sys.executable)" >nul 2>&1 && set "PYTHON_CMD=python"
if not defined PYTHON_CMD py -c "import sys; print(sys.executable)" >nul 2>&1 && set "PYTHON_CMD=py"
if not defined PYTHON_CMD (
    echo ERROR: No se encontro un Python funcional. Instala Python y vuelve a intentarlo.
    pause
    exit /b 1
)

if not exist "config.json" (
    echo ERROR: No existe config.json en %CD%.
    pause
    exit /b 1
)

for /f "usebackq delims=" %%R in (`powershell -NoProfile -Command "(Get-Content -Raw 'config.json' | ConvertFrom-Json).relay_url"`) do set "RELAY_URL=%%R"
if not defined RELAY_URL set "RELAY_URL=desconocida"
echo [WEB] !RELAY_URL!

set "WEB_READY="
for /l %%N in (1,1,5) do (
    if not defined WEB_READY (
        powershell -NoProfile -Command "$r=Invoke-WebRequest -UseBasicParsing -Uri 'https://mcp-web-roblox.onrender.com/api/v1/health.json' -TimeoutSec 5 -ErrorAction SilentlyContinue; if($r.StatusCode -eq 200){exit 0}else{exit 1}" >nul 2>&1
        if not errorlevel 1 set "WEB_READY=1"
        if not defined WEB_READY timeout /t 2 /nobreak >nul
    )
)
if defined WEB_READY (
    echo [WEB] Render online
) else (
    echo [WEB] Render no respondio; continuando para permitir que despierte.
)

echo [MCP] Checking 127.0.0.1:8787...
set "MCP_READY="
powershell -NoProfile -Command "$t=Test-NetConnection 127.0.0.1 -Port 8787 -WarningAction SilentlyContinue; if($t.TcpTestSucceeded){exit 0}else{exit 1}" >nul 2>&1
if not errorlevel 1 set "MCP_READY=1"
if defined MCP_READY (
    echo [MCP] Ya se encuentra activo.
) else (
    echo [MCP] No esta activo; iniciando Roblox Studio MCP...
    powershell -ExecutionPolicy Bypass -File "C:\Users\jhoxm\OneDrive\Documentos\Roblox\tools\roblox-studio-mcp-bridge\scripts\start.ps1" -Transport streamable-http -Background
    for /l %%N in (1,1,20) do (
        if not defined MCP_READY (
            powershell -NoProfile -Command "$t=Test-NetConnection 127.0.0.1 -Port 8787 -WarningAction SilentlyContinue; if($t.TcpTestSucceeded){exit 0}else{exit 1}" >nul 2>&1
            if not errorlevel 1 set "MCP_READY=1"
            if not defined MCP_READY timeout /t 1 /nobreak >nul
        )
    )
    if not defined MCP_READY (
        echo ERROR: No se pudo iniciar Roblox Studio MCP.
        pause
        exit /b 1
    )
)

echo [MCP] Online
echo [RELAY] Starting...
echo.
echo Esperando conexion con Roblox Studio...
echo.
%PYTHON_CMD% -m local.relay_client
set "RELAY_EXIT=%ERRORLEVEL%"
echo.
echo ERROR: relay_client termino con exit code !RELAY_EXIT!.
pause
exit /b !RELAY_EXIT!
