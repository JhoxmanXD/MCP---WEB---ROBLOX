# PROJECT_STATE

## IMPLEMENTED

- Relay FastAPI en `web/`, dashboard y endpoints de health, catálogo, jobs, resultados y estados.
- Idempotencia por `request_id`, cola en memoria, reserva `running`, completado/error y heartbeat.
- Conversión de escalares y `args` JSON; query params explícitos prevalecen.
- Restricción de ejecución a tools anunciadas por `tools/list`.
- Cliente local genérico MCP Streamable HTTP en `local/`.
- Configuración, batch de arranque, README y tests.

## TESTED

- `7 passed` con `python -m pytest -q`.
- `python -m compileall -q web local` correcto.
- Smoke test con `uvicorn app:app --host 127.0.0.1 --port 8999`: health respondió HTTP 200 y headers no-cache.

## REAL MCP TEST

- Conexión real actual a `http://127.0.0.1:8787/mcp` correcta.
- `tools/list`: 71 tools.
- El adaptador local propio también descubrió 71 tools.
- `studio_list_sessions` respondió correctamente, pero `session_count=0`; no fue posible ejecutar una lectura contra Roblox Studio en esta ejecución.

## PENDING

- Desplegar `web/` en Render y sustituir `REPLACE-ME`.
- Prueba E2E pública con un job de lectura.

## KNOWN LIMITATIONS

- Memoria únicamente; reiniciar Render borra jobs, catálogo y estados.
- PoC sin autenticación.
- El bridge MCP HTTP existente debe estar iniciado localmente en `127.0.0.1:8787/mcp`.

## HOW TO RUN

`pip install -r requirements-local.txt`; editar `config.json`; arrancar el bridge existente; ejecutar `run_relay.bat`.

## HOW TO DEPLOY

Render con Root Directory `web`, Build `pip install -r requirements.txt`, Start `uvicorn app:app --host 0.0.0.0 --port $PORT`.
