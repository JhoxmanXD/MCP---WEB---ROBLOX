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
- Agent Gateway navegable añadido localmente: tools dinámicas, ViewSnapshots inmutables, prepare/execute one-shot, resultados y documentación de uso.
- String Composer navegable para strings cortos: charset completo, valores rápidos, valores recientes, backspace, clear y finish.
- Instance Picker muestra nombre y className; la discovery de Studio usa el Workspace real bajo `p`.
- Agent state cache-safe: revisiones de draft, ViewSnapshot inmutables, ActionTokens opacos revision-bound, PreparedInvocation con snapshot/hash y ResultView inmutables.
- Navigator genérico recursivo para objetos, arrays, valores anidados, enums, booleanos y números; incluye edición de claves arbitrarias, propiedades, listas de objetos y snapshots nuevos tras cada mutación.
- Redirects de Start/Action con `no-store`, `no-cache`, `CDN-Cache-Control` y `Surrogate-Control`; views congelan acciones, candidatos y valores recientes.
- Relay endurecido: la limpieza de una conexión MCP fallida no puede matar el bucle de reconexión si el SDK lanza `ExceptionGroup`.
- Resolución typed de propiedades: el bridge/plugin expone `propertyMetadata` derivada de `typeof`, y `studio_get_properties` acepta `class_name` para consultar defaults de una instancia temporal no parentada.
- Agent Gateway typed: Vector2/Vector3/Color3/CFrame/UDim/UDim2/NumberRange/BrickColor, EnumItem picker y fallback seguro para tipos sin decoder/editor escribible.
- El mismo dispatch se aplica a `studio_create_instance.properties`, `studio_set_properties.values` y objetos nested de `studio_batch`.
- Build markers visibles en Agent, health y dashboard: `DEPLOY_COMMIT`, `RENDER_INSTANCE_ID` y `AGENT_PROTOCOL_VERSION=immutable-v1`.
- Contrato de Instance auditado contra `tools/list`; candidatos recientes conservan `ref`, path estructurado y `displayPath`.
- El MCP server del bridge corrige la composición de selectors: normaliza snapshots con `id/ref + path` y evita anidarlos como `ref: {id: ...}`.

## TESTED

- Suite local completa con `C:\Python314\python.exe -m pytest -q`.
- `python -m compileall -q web local` correcto.
- Smoke test con `uvicorn app:app --host 127.0.0.1 --port 8999`: health respondió HTTP 200 y headers no-cache.
- Render real: `/api/v1/health.json` HTTP 200 y `/` HTTP 200.
- Render recibió heartbeat: `local_client_online=true`, `mcp_connected=true`, `studio_connected=true`, `tool_count=71`.
- Tests de recuperación de catálogo: catálogo existente no se reenvía; catálogo ausente se restaura; fallo temporal no termina el cliente.
- Suite MCP-WEB actual: cubre redirects immutable, snapshots estables, picker congelado, objetos/arrays recursivos y los schemas reales de create/properties/batch.
- Suite MCP-WEB typed: cubre Vector3, Color3, EnumItem, batch nested, snapshots Prepared, stale actions y rechazo de componentes Color3 fuera de rango.
- Suite bridge actual: 19 tests pasan.
- Bridge versionado localmente sin remote: commit `b39972f0ac60383ff920f1875aa1810d7914593d`.
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
- La reproducción de composición confirmó el fallo anterior: create/find devolvían el descriptor, pero get/set recibían el selector envuelto bajo `ref`; el fix está en el MCP server, no en MCP-WEB ni en el plugin.
- Lifecycle E2E confirmado: el problema de `session_count=0` tras reinicio era un proceso `stdio` huérfano con otro `SessionManager`; `stop.ps1` ahora limpia también esos procesos para que `8787` y `8788` pertenezcan a una sola instancia.
- E2E Agent Gateway confirmado con Studio real: create `MCP_WEB_CHAIN_TEST`, set `Anchored=true`, lectura independiente, rename a `MCP_WEB_CHAIN_TEST_RENAMED` y find final.
- E2E Agent Gateway con navegación solo por href: creó `SOL_MCP_FINAL_TEST`, creó `WebControlledPart` dentro, aplicó `Anchored=true` y verificó find/properties de forma independiente.
- E2E cache-safe con navegación solo por href: creó `SOL_MCP_CACHE_FINAL`, creó `CacheSafePart` dentro, aplicó `Anchored=true`, verificó parent/properties y registró snapshots Prepared distintos.

## PENDING

- Deploy manual del commit final de MCP-WEB en Render para publicar `/agent/*`.

## KNOWN LIMITATIONS

- Memoria únicamente; reiniciar Render borra jobs, catálogo y estados.
- PoC sin autenticación.
- El bridge MCP HTTP existente debe estar iniciado localmente en `127.0.0.1:8787/mcp`.
- En este place, el plugin expone `Workspace` con el path real `p`; por eso la escritura usó `{"path":["p"]}`. La instancia permanece deliberadamente en Studio como evidencia.

## HOW TO RUN

`pip install -r requirements-local.txt`; abrir Roblox Studio y el proyecto; ejecutar `run_relay.bat`.

## HOW TO DEPLOY

Render con Root Directory `web`, Build `pip install -r requirements.txt`, Start `uvicorn app:app --host 0.0.0.0 --port $PORT`.

## AGENT LIFECYCLE

El Agent Gateway conserva actualmente el estado en `MemoryStore`, dentro del proceso web. Cada `ViewSnapshot`, `ActionToken`, editor, Prepared y Result queda ligado a su `draft_id`; las acciones además registran `view_id`, `editor_id` o el snapshot propietario cuando corresponde.

El TTL es rodante de 3600 segundos por draft. Views y acciones comparten el deadline del draft y la limpieza elimina juntos todos sus descendientes, de modo que una acción no puede expirar antes que la View que la publicó. Si un identificador Agent ya no existe, el gateway devuelve `410 AGENT STATE EXPIRED` con enlaces para iniciar una invocación nueva.

Cada creación y lookup de acción registra `action_id`, `draft_id`, `view_id`, revisión esperada, operación, store id, process id e instance id sin incluir argumentos sensibles. Esto permite distinguir token no registrado, estado expirado y aislamiento de proceso.

El modo local conserva `MemoryStore`. Para producción existe `web/agent_state.py`: `AGENT_STATE_BACKEND=redis` usa un namespace Redis-compatible, documento JSON `agent-state-v1`, TTL nativo, lock distribuido y CAS de revisión. El snapshot incluye el lifecycle Agent y el mínimo de jobs, catálogo y heartbeat necesarios para que otro worker resuelva la cadena. `agent_state_backend` expone modo, shared, connected, schema y namespace sin secretos; si el backend compartido configurado no está disponible, Agent falla cerrado con `503`.
