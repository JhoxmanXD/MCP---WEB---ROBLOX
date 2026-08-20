# Desplegar MCP-WEB en Render

1. Crea un repositorio con este proyecto y conecta el repositorio a Render.
2. Selecciona **New Web Service** y configura:

```text
Runtime: Python
Root Directory: web
Build Command: pip install -r requirements.txt
Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
```

3. No añadas base de datos ni variables obligatorias.
4. Tras el deploy, la URL pública será algo como `https://mcp-web-xxxx.onrender.com`.
5. Comprueba:

```text
https://mcp-web-xxxx.onrender.com/api/v1/health.json
https://mcp-web-xxxx.onrender.com/api/v1/catalog.json
```

6. Copia esa URL sin la ruta final en `config.json` como `relay_url`, inicia el bridge MCP local y ejecuta `run_relay.bat`.

Por defecto el estado se guarda en memoria para desarrollo local. Para Agent Mode en producción, configura un backend Redis-compatible compartido antes de usar varios workers o depender de enlaces después de un reinicio. Los endpoints de catálogo, llamadas, resultados y estado envían `no-store`.

## Estado compartido de Agent Mode

Añade estas variables en Render (o en el entorno del servicio):

```text
AGENT_STATE_BACKEND=redis
AGENT_STATE_URL=<URL-privada-de-Redis-compatible>
AGENT_STATE_NAMESPACE=mcp-web:agent:immutable-v1
```

`AGENT_STATE_URL` debe ser un secreto del proveedor de estado; no se guarda en Git. El backend almacena un documento JSON versionado (`agent-state-v1`), aplica TTL y serializa las mutaciones con un lock distribuido más una comprobación CAS. Si el backend configurado no responde, las rutas `/agent/*` fallan cerrado con `503` y no continúan usando memoria local.

El estado visible en `/agent/status`, `/api/v1/health.json` y `/api/v1/dashboard.json` incluye `agent_state_backend`, `shared`, `connected`, `schema_version`, `namespace` y `ttl_seconds` (sin exponer la URL ni credenciales). El TTL compartido predeterminado es 3600 segundos y se renueva con cada petición Agent. Para desarrollo local no hace falta configurar nada: el valor predeterminado es `AGENT_STATE_BACKEND=memory`.
