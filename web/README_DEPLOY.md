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

El estado se guarda en memoria y se pierde al reiniciar el servicio. Los endpoints de catálogo, llamadas, resultados y estado envían `no-store`.
