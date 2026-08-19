# PROJECT_STATE

## IMPLEMENTED

- Relay FastAPI en `web/`, dashboard y endpoints de health, catálogo, jobs, resultados y estados.
- Idempotencia por `request_id`, cola en memoria, reserva `running`, completado/error y heartbeat.
- Conversión de escalares y `args` JSON; query params explícitos prevalecen.
- Restricción de ejecución a tools anunciadas por `tools/list`.
- Cliente local genérico MCP Streamable HTTP en `local/`.
- Configuración, batch de arranque, README y tests.
- `config.json` actualizado al deploy real `https://mcp-web-roblox.onrender.com`.
- `run_relay.bat` comprueba el puerto MCP 8787 y arranca el bridge existente si hace falta.
- El heartbeat devuelve `catalog_present` y `catalog_tool_count`; el cliente republica el catálogo solo cuando falta o está incompleto.
- Agent Gateway navegable añadido localmente: tools dinámicas, drafts, wizard primitivo, prepare/execute one-shot, resultados y documentación de uso.

## TESTED

- `7 passed` con `python -m pytest -q`.
- `python -m compileall -q web local` correcto.
- Smoke test con `uvicorn app:app --host 127.0.0.1 --port 8999`: health respondió HTTP 200 y headers no-cache.
- Render real: `/api/v1/health.json` HTTP 200 y `/` HTTP 200.
- Render recibió heartbeat: `local_client_online=true`, `mcp_connected=true`, `studio_connected=true`, `tool_count=71`.
- Tests de recuperación de catálogo: catálogo existente no se reenvía; catálogo ausente se restaura; fallo temporal no termina el cliente.
- Suite actual: 17 tests pasan.
- Catálogo público: 71 tools e incluye `studio_list_sessions`.

## REAL MCP TEST

- Conexión real actual a `http://127.0.0.1:8787/mcp` correcta.
- `tools/list`: 71 tools.
- El adaptador local propio también descubrió 71 tools.
- `studio_list_sessions`: sesión real `studio_86592268985719`, place `86592268985719`, `Place1`.
- READ E2E `LUNA_READ_001` con `studio_list_sessions`: completed; `session_count=1`.
- WRITE E2E inicial `LUNA_WRITE_001` falló por parent inválido; `LUNA_WRITE_002` confirmó el mismo detalle.
- WRITE E2E correcto `LUNA_WRITE_003`: creó `Part` `MCP_WEB_E2E_TEST`, ref `rbx:studio_86592268985719:i_369`, path real `p.MCP_WEB_E2E_TEST`.
- Verificación independiente `LUNA_VERIFY_004` con `studio_find_instances`: devolvió la instancia creada.

## PENDING

- Desplegar `web/` en Render y sustituir `REPLACE-ME`.
- Prueba E2E pública con un job de lectura.
- Deploy manual del commit de trabajo actual para publicar `/agent/*`.

## KNOWN LIMITATIONS

- Memoria únicamente; reiniciar Render borra jobs, catálogo y estados.
- PoC sin autenticación.
- El bridge MCP HTTP existente debe estar iniciado localmente en `127.0.0.1:8787/mcp`.
- En este place, el plugin expone `Workspace` con el path real `p`; por eso la escritura usó `{"path":["p"]}`. La instancia permanece deliberadamente en Studio como evidencia.

## HOW TO RUN

`pip install -r requirements-local.txt`; abrir Roblox Studio y el proyecto; ejecutar `run_relay.bat`.

## HOW TO DEPLOY

Render con Root Directory `web`, Build `pip install -r requirements.txt`, Start `uvicorn app:app --host 0.0.0.0 --port $PORT`.
