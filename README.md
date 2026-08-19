# MCP-WEB

Relay bidireccional en memoria entre una API pública y el bridge MCP local de Roblox Studio.

## Arquitectura

`ChatGPT/HTTP → web/ (Render) → jobs → local/relay_client.py → MCP Streamable HTTP local → Roblox Studio`.

El relay no implementa ni copia herramientas. El cliente ejecuta `tools/list` en el bridge existente y publica el catálogo; cada job se ejecuta genéricamente con `session.call_tool(nombre, argumentos)`.

## Instalar y ejecutar el cliente local

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-local.txt
```

Edita `config.json` y coloca la URL de Render en `relay_url`. Deben estar abiertos Roblox Studio, el proyecto, el bridge MCP existente y el cliente local. El bridge HTTP local debe estar iniciado con:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\jhoxm\OneDrive\Documentos\Roblox\tools\roblox-studio-mcp-bridge\scripts\start.ps1" -Transport streamable-http -Background
```

Después ejecuta `run_relay.bat` o `python -m local.relay_client`.

## Despliegue

Sube únicamente `web/` a Render siguiendo [web/README_DEPLOY.md](web/README_DEPLOY.md). Copia la URL resultante a `config.json` y comprueba `/api/v1/health.json`, luego `/api/v1/catalog.json`.

Las llamadas GET requieren `rid`, son idempotentes y solo aceptan nombres anunciados por `tools/list`. `args` contiene un objeto JSON; los query params individuales prevalecen sobre sus claves. `rid`, `state`, `args`, `t` y `nonce` no se envían a MCP como argumentos.
