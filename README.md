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

`config.json` ya está preparado para el deploy actual `https://mcp-web-roblox.onrender.com`. Deben estar abiertos Roblox Studio y el proyecto. `run_relay.bat` comprueba el MCP HTTP local y arranca el bridge existente si no está escuchando:

```powershell
run_relay.bat
```

Después ejecuta `run_relay.bat` o `python -m local.relay_client`.

Si queda un relay oculto ejecutándose, usa `stop_relay.bat`. Este archivo cierra únicamente procesos `local.relay_client`; no cierra Roblox Studio ni el bridge MCP.

## Despliegue

Sube únicamente `web/` a Render siguiendo [web/README_DEPLOY.md](web/README_DEPLOY.md). Copia la URL resultante a `config.json` y comprueba `/api/v1/health.json`, luego `/api/v1/catalog.json`.

Las llamadas GET requieren `rid`, son idempotentes y solo aceptan nombres anunciados por `tools/list`. `args` contiene un objeto JSON; los query params individuales prevalecen sobre sus claves. `rid`, `state`, `args`, `t` y `nonce` no se envían a MCP como argumentos.
