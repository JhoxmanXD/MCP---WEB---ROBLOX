# MCP-WEB Master Specification

> Documentación maestra del gateway web que conecta ChatGPT normal con el MCP local de Roblox Studio.
>
> Objetivo: permitir que un chat sin contexto previo pueda entender el sistema, operarlo correctamente, diagnosticar fallos y extenderlo sin repetir toda la historia de desarrollo.

---

## 0. Estado de referencia de esta documentación

### URL pública

```text
https://mcp-web-roblox.onrender.com/
```

### Protocolo Agent

```text
AGENT_PROTOCOL_VERSION: immutable-v1
```

### Deployment final validado

```text
DEPLOY_COMMIT:
73b109bcdb8548cb9e2d145952b44673c0033172
```

Release:

```text
73b109b Complete immutable schema navigation and relay recovery
```

Este SHA identifica la release validada al redactar este documento. En releases posteriores, `Agent Status` debe considerarse fuente de verdad.

### Catálogo MCP

```text
tool_count: 71
```

El catálogo es dinámico y proviene del MCP real mediante `tools/list`.

### Transporte MCP local

```text
http://127.0.0.1:8787/mcp
```

Transporte utilizado:

```text
Streamable HTTP
```

### Bridge Roblox local

Ruta de referencia:

```text
C:\Users\jhoxm\OneDrive\Documentos\Roblox\tools\roblox-studio-mcp-bridge
```

Commit local de bridge conservado:

```text
b39972f0ac60383ff920f1875aa1810d7914593d
```

El bridge fue versionado localmente y no tenía remote configurado en el momento de la validación.

### Proyecto MCP-WEB local

```text
C:\Users\jhoxm\OneDrive\Documentos\MCP - WEB
```

Repositorio:

```text
https://github.com/JhoxmanXD/MCP---WEB---ROBLOX.git
```

### Suites verificadas durante la estabilización

```text
MCP-WEB: 25 passed
Bridge: 19 passed
```

Los contadores pueden aumentar en releases futuras.

---

# 1. Propósito

MCP-WEB existe para resolver una restricción específica: permitir que un chat normal de ChatGPT use un MCP local de Roblox Studio sin depender de una conexión MCP nativa directa dentro de ese chat.

El sistema convierte navegación web segura y server-rendered en una capa de invocación MCP.

Cadena:

```text
ChatGPT normal
        │
        │ Web En vivo
        ▼
MCP-WEB público en Render
        │
        │ job queue / Agent Gateway
        ▼
relay_client.py en el PC
        │
        │ MCP Streamable HTTP
        ▼
Roblox Studio MCP
        │
        ▼
Roblox Studio Plugin / DataModel
```

Retorno:

```text
Roblox Studio
        ↓
MCP
        ↓
relay_client.py
        ↓
Render
        ↓
Agent Result View
        ↓
ChatGPT
```

El objetivo de uso no es simplemente enviar comandos aislados, sino permitir ciclos:

```text
OBSERVE
→ REASON
→ ACT
→ OBSERVE
→ VERIFY
→ CONTINUE
```

---

# 2. E2E final confirmado

Se confirmó desde un chat normal, siguiendo navegación web real, el siguiente estado de Roblox Studio:

```text
SOL_MCP_FINAL_TEST
└── WebControlledPart
    └── Anchored = true
```

Verificación real:

```text
Folder:
name: SOL_MCP_FINAL_TEST
className: Folder
ref: rbx:studio_86592268985719:i_397
path: p.SOL_MCP_FINAL_TEST

Part:
name: WebControlledPart
className: Part
ref: rbx:studio_86592268985719:i_398
path: p.SOL_MCP_FINAL_TEST.WebControlledPart
parent: p.SOL_MCP_FINAL_TEST
Anchored: true
```

La lectura independiente confirmó:

- Folder existente;
- clase `Folder`;
- Part existente;
- clase `Part`;
- parent correcto;
- nombre exacto;
- `Anchored: true`.

Ejemplo de trazabilidad de una escritura real:

```text
TOOL:
set_properties

VIEW_ID:
V_60197cc4b058495fa1

DRAFT_REVISION:
2

PREPARE_ID:
P_66ccf0a4bdeb407894

ARGUMENTS_SHA256:
d31e606b33a9e893ff3620176a773e1e179d08ec968201db98c640f8ba99b892

REQUEST_ID:
WEB_AGENT_0fa32d0420de404d
```

La escritura no se consideró correcta por el `success` del setter: se leyó nuevamente Studio y se confirmó `Anchored: true`.

### Nota sobre objetos duplicados

Durante pruebas existió otro Folder vacío con el mismo nombre y otra ref. Por tanto:

```text
NOMBRE != IDENTIDAD ÚNICA
```

Un agente debe reutilizar refs/candidates reales y no asumir que un nombre identifica una sola Instance.

---

# 3. Arquitectura

## 3.1 Web Relay

Tecnología:

```text
Python
FastAPI
Uvicorn
```

Desplegado como Web Service en Render.

Responsabilidades:

- health;
- dashboard;
- catálogo MCP;
- heartbeat;
- jobs;
- resultados;
- estados;
- rutas de lectura;
- Agent Gateway;
- drafts;
- vistas inmutables;
- acciones opacas;
- prepared snapshots;
- result snapshots;
- recipes;
- idempotencia.

El estado principal del prototipo vive en memoria.

## 3.2 Relay local

Proceso:

```text
local/relay_client.py
```

Responsabilidades:

1. conectarse al MCP local;
2. ejecutar `tools/list`;
3. publicar catálogo;
4. mantener heartbeat;
5. restaurar catálogo si Render lo pierde;
6. hacer polling de jobs;
7. ejecutar `call_tool(tool_name, arguments)`;
8. subir resultado/error;
9. reconectar ante fallos transitorios.

## 3.3 Roblox Studio MCP Bridge

El bridge expone MCP en:

```text
http://127.0.0.1:8787/mcp
```

y mantiene el vínculo con el plugin/Studio.

Durante estabilización se corrigió un bug de selector:

Incorrecto:

```json
{"ref":{"id":"rbx:..."}}
```

Correcto:

```json
{"id":"rbx:..."}
```

o una representación de path estructurada válida según el schema.

La regla final es:

```text
resultado de create/find/get
→ candidate reutilizable
→ input válido de set/get/rename/etc.
```

## 3.4 Roblox Studio Plugin

El plugin es la capa que finalmente interactúa con Studio y el DataModel.

El plugin permaneció sin modificaciones durante la corrección final de selectors.

---

# 4. Launcher de uso diario

Archivo:

```text
run_relay.bat
```

Experiencia deseada:

```text
1. Abrir Roblox Studio.
2. Abrir el proyecto.
3. Doble clic en run_relay.bat.
4. Esperar a que aparezcan MCP / relay / Studio conectados.
5. Usar ChatGPT normal.
```

El launcher:

- detecta Python;
- lee `config.json`;
- comprueba Render;
- comprueba el MCP local;
- evita iniciar duplicados;
- inicia MCP si hace falta;
- espera a que quede disponible;
- inicia `relay_client`;
- deja logs visibles.

Estado esperado:

```text
Render online
MCP online
71 tools
Studio connected
```

---

# 5. Recuperación de procesos y sesiones

## 5.1 Problema encontrado

Durante pruebas se observaron procesos `stdio` huérfanos que mantenían otro `SessionManager` para el bridge local.

Resultado:

```text
Studio abierto
pero session_count = 0
```

desde la instancia MCP equivocada.

## 5.2 Corrección

El lifecycle final utiliza `stop.ps1` para limpiar procesos huérfanos y deja una única instancia:

```text
streamable-http
```

Resultado validado:

```text
SESSION RECOVERY: PASS
session_count after restart: 1
recovery time: ~3 segundos
```

El tiempo es orientativo, no una garantía contractual.

## 5.3 Regla operativa

Si `session_count = 0` inmediatamente después de reiniciar infraestructura local:

1. no reinicies Studio de inmediato;
2. espera unos segundos;
3. vuelve a consultar sesiones;
4. comprueba que no existan procesos MCP duplicados/huérfanos.

---

# 6. Catálogo y recuperación después de Render restart

Render Free puede reiniciar, hacer redeploy o perder el estado en memoria.

Mecanismo final:

```text
Render restart
↓
store.catalog vacío
↓
heartbeat del relay detecta catalog_present=false
↓
relay_client vuelve a subir tools/list
↓
catalog restored
```

No es necesario reiniciar manualmente `run_relay.bat` en el flujo normal.

El catálogo publica identificadores útiles:

```text
server_instance_id
catalog_generation
updated_at
studio_connected
tool_count
```

Estos sirven para distinguir generaciones de servidor y detectar respuestas viejas.

---

# 7. Por qué existe el Agent Gateway

La API raw funciona, pero ChatGPT Web mostró limitaciones prácticas al abrir URLs arbitrarias construidas dinámicamente.

Ejemplos problemáticos:

```text
?nonce=<uuid>
?rid=<uuid>
?args=<encoded-json>
```

Por ello el modo de agente se diseñó con:

```text
HTML server-rendered
+
<a href> reales
+
estado server-side
+
URLs opacas
```

ChatGPT solo necesita:

```text
abrir página
→ leer
→ seguir enlace real
```

---

# 8. Agent Gateway

Entrada:

```text
/agent
```

Rutas principales:

```text
/agent/status
/agent/tools
/agent/tool/{tool_name}
/agent/tool/{tool_name}/start
/agent/jobs
/agent/latest
/agent/recipes
/agent/help
```

El modo Agent actual usa protocolo:

```text
immutable-v1
```

---

# 9. Protocolo `immutable-v1`

## 9.1 Problema que resuelve

En una versión previa, una URL mutable como:

```text
/agent/draft/<id>/prepare
```

podía ser presentada por una capa externa con contenido antiguo.

Esto creó el riesgo de ver argumentos de otro estado/draft.

La solución fue eliminar la dependencia de una misma URL para contenido mutable.

## 9.2 Identificadores

Flujo:

```text
/agent/view/V_...
/agent/action/A_...
/agent/prepared/P_...
/agent/result-view/R_...
```

Significado:

```text
V_ = ViewSnapshot inmutable
A_ = ActionToken opaco
P_ = PreparedInvocation inmutable
R_ = ResultView inmutable
```

## 9.3 Draft revision

Cada draft posee una revisión.

Conceptualmente:

```text
DRAFT_ID
revision
arguments
tool
status
```

Cada mutación válida incrementa:

```text
revision += 1
```

## 9.4 ViewSnapshot

Cada View es una fotografía:

```text
VIEW_ID
DRAFT_ID
DRAFT_REVISION
TOOL
ARGUMENTS SNAPSHOT
READY / MISSING
```

Una `V_...` no debe cambiar de significado después de emitirse.

## 9.5 ActionToken

Los enlaces de acción son opacos:

```text
/agent/action/A_...
```

Internamente están asociados a:

```text
draft_id
expected_revision
operation
payload
consumed
```

Si una acción vieja se intenta aplicar a una revisión nueva:

```text
expected_revision != current_revision
```

el gateway debe responder:

```text
STALE DRAFT VIEW
```

y no modificar nada.

## 9.6 Idempotencia de acciones

Visitar dos veces el mismo ActionToken no debe aplicar dos veces la mutación.

Resultado esperado:

```text
GET A_x #1 → aplica
GET A_x #2 → misma resulting view / no segunda mutación
```

---

# 10. Prepare / Execute

## 10.1 Prepare

`Prepare Execution` NO ejecuta MCP.

Crea un snapshot:

```text
PREPARE_ID
DRAFT_ID
DRAFT_REVISION
TOOL
ARGUMENTS
ARGUMENTS_SHA256
```

Ruta:

```text
/agent/prepared/P_...
```

## 10.2 Hash

`ARGUMENTS_SHA256` identifica de forma inequívoca el contenido preparado.

Su función aquí es de integridad/identidad operacional, no autenticación.

## 10.3 Execute

`Execute now` queda vinculado al `PreparedInvocation`, no al draft mutable.

Regla:

```text
job.arguments = prepared.arguments_snapshot
```

Nunca:

```text
job.arguments = current_draft.arguments
```

## 10.4 One-shot

Repetir `Execute now` sobre el mismo prepared no debe crear dos jobs.

Debe devolver/reusar el mismo:

```text
request_id
```

Esto es crítico para escrituras como create/destroy/rename.

---

# 11. Result Views

Los resultados también siguen el modelo inmutable.

Conceptualmente:

```text
R1: pending
→ Refresh Result
R2: running
→ Refresh Result
R3: completed
```

Cada `R_...` representa un snapshot de estado.

No es necesario confiar en que una URL mutable cambie correctamente de contenido.

---

# 12. Flujo Agent completo

```text
/
↓
ChatGPT Agent Gateway
↓
Agent Status
↓
Tools / Recipes
↓
Tool page
↓
Start invocation
↓
V_1
↓
A_1
↓
V_2
↓
A_2
↓
V_3
↓
Prepare
↓
P_1
↓
inspect exact arguments + hash
↓
Execute now
↓
R_1
↓
Refresh
↓
R_2 completed
↓
independent read verification
```

---

# 13. String Composer

El gateway soporta free-text corto mediante navegación.

Charset final:

```text
A-Z
a-z
0-9
_
-
.
/
espacio
```

más símbolos extra.

Acciones:

```text
Append <char>
Backspace
Clear
Finish
```

El valor parcial se guarda server-side.

Tests explícitos confirmaron:

```text
SOL_MCP_FINAL_TEST
WebControlledPart
```

con sensibilidad a mayúsculas/minúsculas y navegación link-only.

## 13.1 Orden de preferencia

Para strings:

```text
1. enum del schema
2. default/example
3. recent values
4. values extraídos de Studio
5. presets Roblox
6. recipe shortcuts
7. String Composer
```

El compositor debe ser fallback, no la interfaz principal para valores ya conocidos.

---

# 14. Number Composer

Para `integer` / `number`:

```text
0-9
-
.
Backspace
Clear
Finish
```

También puede ofrecer quick values:

```text
0
1
-1
10
100
0.5
```

El schema real debe seguir siendo la fuente de validación.

---

# 15. Objects, arrays y unions

El Schema Navigator soporta de forma genérica:

- objetos recursivos básicos;
- arrays de strings;
- arrays de numbers;
- arrays de objects;
- booleanos;
- enum;
- null;
- defaults;
- combinaciones/unions que aparezcan en los schemas soportados.

No debe existir lógica separada para cada una de las 71 tools.

Principio:

```text
inputSchema real
→ renderer/editor genérico
→ InvocationDraft
```

---

# 16. Instance Picker

Objetivo:

```text
nunca hacer que ChatGPT invente rbx refs o paths
```

Fuentes:

```text
Current Selection
Recent Instances
Recent Results
Search Instances
Browse Tree
Services
```

Una candidate puede conservar:

```text
session
name
className
ref
structured path
display path
```

La forma concreta enviada a una tool depende de su `inputSchema`.

---

# 17. Contrato de selectors

Bug histórico corregido:

Incorrecto:

```json
{"ref":{"id":"..."}}
```

Correcto:

```json
{"id":"..."}
```

o path estructurado admitido.

Esto hizo posible:

```text
create
→ returned ref/path
→ set properties
→ read properties
```

y:

```text
find
→ returned ref/path
→ rename
→ find again
```

E2E físico:

```text
CREATE → SET → READ: PASS
Anchored: true
FIND → RENAME → FIND: PASS
```

---

# 18. Refs, paths y durabilidad

## 18.1 Refs

Formato típico:

```text
rbx:studio_<session>:i_<instance>
```

No deben considerarse IDs globales permanentes.

Pueden dejar de ser válidos si:

- Studio reinicia;
- cambia la sesión;
- la Instance se destruye;
- el bridge reconstruye su registry.

## 18.2 Paths

Un display path puede verse como:

```text
p.Model.Part
```

pero no debe tratarse automáticamente como forma serializada válida.

Los nombres Roblox pueden crear ambigüedad en un dot-path.

Cuando el MCP admita structured path, es preferible.

## 18.3 Regla

```text
NO INVENTAR REF
NO INVENTAR PATH
NO CONVERTIR DISPLAY PATH A INPUT SIN CONSULTAR SCHEMA
```

---

# 19. Recent Instances y tool chaining

Los resultados estructurados deben generar candidates reutilizables.

Ejemplo:

```text
create Part
↓
result contiene ref/path/name/className
↓
Recent Instances
↓
Set properties
↓
Get properties
↓
Rename
```

Esto reduce navegación y acerca el comportamiento a un MCP nativo.

---

# 20. Recipes

`/agent/recipes` sirve para workflows de alto valor construidos encima del mismo sistema de Draft/Prepare/Execute.

Ejemplos de recipes deseables/esperadas según tools reales:

```text
Inspect Studio
Inspect Tree
Inspect Selection
Find Instance
Inspect Instance
Create Part
Create Folder
Set Property
Rename Instance
Read Script
Read Output
```

Una recipe no debe ejecutar una implementación paralela.

Debe terminar en:

```text
Recipe
→ InvocationDraft
→ immutable views
→ prepared snapshot
→ execute
```

---

# 21. Rutas públicas

## 21.1 Dashboard

```text
/
```

Muestra información como:

```text
Local relay
Roblox Studio
MCP tools
Pending jobs
Completed jobs
Last activity
```

Enlaces principales:

```text
ChatGPT Agent Gateway
Live Health
Live Catalog
Read Studio Sessions
Latest Result
```

## 21.2 Agent

```text
/agent
/agent/status
/agent/tools
/agent/tool/{tool}
/agent/tool/{tool}/start
/agent/view/V_...
/agent/action/A_...
/agent/prepared/P_...
/agent/result-view/R_...
/agent/jobs
/agent/latest
/agent/recipes
/agent/help
```

## 21.3 Lecturas navegables

```text
/read/health
/read/catalog
/read/sessions
/read/result/{request_id}
/read/latest
```

## 21.4 API raw

```text
/api/v1/health.json
/api/v1/catalog.json
/api/v1/call/{tool_name}
/api/v1/result/{request_id}.json
/api/v1/state/latest.json
/api/v1/state/{state_key}.json
```

## 21.5 Endpoints internos del relay local

```text
POST /api/v1/catalog
POST /api/v1/client/heartbeat
GET  /api/v1/jobs/next
POST /api/v1/jobs/{request_id}/complete
```

Estos no son la interfaz recomendada para un chat normal.

---

# 22. API raw: semántica

## 22.1 Health

```text
GET /api/v1/health.json
```

Comprueba disponibilidad del Web Relay y estado local.

## 22.2 Catalog

```text
GET /api/v1/catalog.json
```

Fuente pública de:

```text
updated_at
studio_connected
tool_count
tools
catalog_generation
server_instance_id
```

## 22.3 Universal call

```text
GET /api/v1/call/{tool_name}
```

Originalmente diseñado para aceptar:

```text
rid
args
query params escalares
```

La API raw se conserva, pero el modo ChatGPT recomendado es Agent Gateway.

## 22.4 Result

```text
GET /api/v1/result/{request_id}.json
```

Estados:

```text
pending
running
completed
error
```

## 22.5 State

```text
GET /api/v1/state/latest.json
GET /api/v1/state/{key}.json
```

Útiles para observabilidad/compatibilidad; no deben reemplazar la vinculación explícita `draft_id/request_id`.

---

# 23. No-cache

Las páginas dinámicas y, desde la release `73b109b`, también los redirects dinámicos del Agent usan headers anti-cache coherentes como:

```text
Cache-Control: no-store, no-cache, must-revalidate, max-age=0
Pragma: no-cache
Expires: 0
```

y, donde corresponde:

```text
CDN-Cache-Control: no-store
Surrogate-Control: no-store
```

Sin embargo:

```text
HEADERS != SOLUCIÓN PRINCIPAL
```

La solución robusta frente a caché externo es:

```text
unique immutable paths
+
revision-bound actions
+
prepared snapshots
```

---

# 24. Catálogo MCP actual: 71 nombres

> Nota: el catálogo es dinámico. Esta lista es un snapshot de referencia. Antes de invocar una tool, el agente debe leer el catálogo actual.

## Sesiones / estado

| # | Tool | Descripción |
|---|---|---|
| 1 | `studio_list_sessions` | Lista sesiones conectadas de Roblox Studio. |
| 2 | `sessions` | Alias de list sessions. |
| 3 | `studio_get_session` | Lee una sesión por id. |
| 4 | `studio_select_session` | Valida/selecciona una sesión. |
| 5 | `studio_status` | Estado del bridge y sesiones. |
| 6 | `status` | Alias de status. |
| 7 | `studio_get_place_info` | Metadata del place actual. |
| 8 | `place` | Alias de place info. |

## Árbol / selección / Instances

| # | Tool | Descripción |
|---|---|---|
| 9 | `studio_get_tree` | Snapshot acotado del DataModel. |
| 10 | `tree` | Alias. |
| 11 | `studio_get_selection` | Lee la selección actual. |
| 12 | `selection` | Alias. |
| 13 | `studio_set_selection` | Reemplaza selección con Instances explícitas. |
| 14 | `studio_get_instance` | Lee una Instance por id/path. |
| 15 | `instance` | Alias. |
| 16 | `studio_find_instances` | Busca Instances por nombre/query. |
| 17 | `find` | Alias. |
| 18 | `studio_get_properties` | Lee propiedades seguras. |
| 19 | `properties` | Alias. |
| 20 | `studio_get_attributes` | Lee attributes. |
| 21 | `attributes` | Alias. |
| 22 | `studio_get_tags` | Lee CollectionService tags. |
| 23 | `tags` | Alias. |
| 24 | `studio_list_services` | Lista services del DataModel. |
| 25 | `list_services` | Alias. |

## Crear / destruir / mover / modificar

| # | Tool | Descripción |
|---|---|---|
| 26 | `studio_create_instance` | Crea una Instance. |
| 27 | `create` | Alias. |
| 28 | `studio_destroy_instance` | Destruye una Instance con confirmación explícita. |
| 29 | `destroy` | Alias. |
| 30 | `studio_rename_instance` | Renombra una Instance. |
| 31 | `rename` | Alias. |
| 32 | `studio_clone_instance` | Clona una Instance. |
| 33 | `studio_reparent_instance` | Mueve una Instance bajo otro parent. |
| 34 | `reparent` | Alias. |
| 35 | `studio_set_properties` | Modifica propiedades validadas. |
| 36 | `set_properties` | Alias. |
| 37 | `studio_set_attributes` | Modifica attributes. |
| 38 | `set_attributes` | Alias. |
| 39 | `studio_set_tags` | Reemplaza tags. |
| 40 | `set_tags` | Alias. |
| 41 | `studio_add_tag` | Añade un tag. |
| 42 | `studio_remove_tag` | Elimina un tag. |
| 43 | `studio_batch` | Ejecuta batch acotado y ordenado. |
| 44 | `batch` | Alias. |

## Scripts

| # | Tool | Descripción |
|---|---|---|
| 45 | `studio_read_script` | Lee source de Script/LocalScript. |
| 46 | `script_read` | Alias. |
| 47 | `read` | Alias compacto. |
| 48 | `studio_create_script` | Crea script con source explícito. |
| 49 | `script_create` | Alias. |
| 50 | `create_script` | Alias. |
| 51 | `studio_replace_script` | Reemplaza source completo. |
| 52 | `script_replace` | Alias. |
| 53 | `replace` | Alias. |
| 54 | `studio_patch_script` | Aplica patches localizados con source hash. |
| 55 | `script_patch` | Alias. |
| 56 | `patch` | Alias compacto. |
| 57 | `studio_open_script` | Abre script en el editor de Studio. |
| 58 | `open` | Alias. |
| 59 | `studio_list_open_scripts` | Lista scripts abiertos. |
| 60 | `list_open_scripts` | Alias. |

## Output / undo / playtest

| # | Tool | Descripción |
|---|---|---|
| 61 | `studio_get_output` | Lee líneas recientes de Output como datos. |
| 62 | `output` | Alias. |
| 63 | `studio_clear_output_buffer` | Limpia buffer de Output. |
| 64 | `studio_undo` | Solicita un Undo. |
| 65 | `undo` | Alias. |
| 66 | `studio_redo` | Solicita un Redo. |
| 67 | `redo` | Alias. |
| 68 | `studio_can_undo` | Consulta si hay Undo disponible. |
| 69 | `studio_can_redo` | Consulta si hay Redo disponible. |
| 70 | `studio_playtest` | Start/stop/inspect playtest. |
| 71 | `playtest` | Alias. |

---

# 25. Aliases y familias de capacidad

Los 71 nombres no representan 71 conceptos completamente distintos.

Ejemplos:

```text
studio_get_tree ↔ tree
studio_find_instances ↔ find
studio_create_instance ↔ create
studio_get_properties ↔ properties
studio_set_properties ↔ set_properties
studio_read_script ↔ script_read ↔ read
studio_create_script ↔ script_create ↔ create_script
studio_replace_script ↔ script_replace ↔ replace
studio_patch_script ↔ script_patch ↔ patch
```

Regla práctica:

```text
catálogo actual
→ escoger variante navegable actual
→ nunca asumir por memoria
```

### Nota histórica de routing

Durante el desarrollo se observó que una variante `studio_*` podía terminar en legacy mientras un alias corto seguía el protocolo immutable. El commit final corrigió el routing principal de `Start invocation`, pero un agente debe mantener la regla defensiva:

```text
si la navegación sale de V_/A_/P_/R_
→ no ejecutar
→ volver a /agent/tools
```

---

# 26. Estado protocol-navigable vs practical

Durante una etapa de desarrollo se midió:

```text
Protocol navigable: 71/71
Practical for ChatGPT Web: 23/71
Partial: 48/71
Blocked: 0
```

Ese número fue una fotografía intermedia, anterior a varias mejoras:

- String Composer completo;
- selectors reutilizables;
- Instance Picker;
- Recent Instances;
- immutable navigation;
- mejoras de chaining.

No existe una métrica final recomputada y validada para "Practical X/71" después de todas las correcciones.

Por ello este documento NO afirma un número práctico final artificial.

Lo que sí está demostrado:

```text
list sessions
inspect place/tree
find
create Folder/Part
reuse ref/path
set properties
read properties
rename/find
independent verification
```

---

# 27. Large text / Luau

Esta es la limitación práctica más importante si se pretende usar MCP-WEB como entorno de desarrollo completo.

## 27.1 Lo que sí funciona bien

- nombres;
- queries;
- propiedades;
- refs;
- paths;
- pequeños arrays/objects;
- patches localizados;
- short source fragments.

## 27.2 Lo que no es práctico mediante links

```text
500 líneas de Luau
JSON enorme
source completo de módulos grandes
```

Aunque el protocolo pueda representar un string largo, navegar cientos/miles de caracteres no es razonable.

## 27.3 Estrategia preferida

Para edición de código:

```text
read current script
↓
obtain source hash
↓
use patch / script_patch
↓
small localized edits
↓
read back
```

La tool `studio_patch_script` está diseñada para patches localizados con validación del source hash, lo cual encaja mejor con Agent Web que un replace completo.

## 27.4 Futuro

Una mejora importante sería implementar un mecanismo de payload/staging que permita a ChatGPT entregar bloques grandes sin construir una query URL ni miles de links.

Debe diseñarse con cuidado para no reintroducir los problemas de navegación/caché.

---

# 28. Errores y recuperación

## 28.1 `CATALOG NOT AVAILABLE`

Causa probable:

- Render acaba de reiniciar;
- relay aún no republicó.

Acción:

```text
esperar
→ Live Catalog
→ comprobar tool_count
```

## 28.2 `MCP OFFLINE`

Comprobar:

```text
127.0.0.1:8787
run_relay.bat
start.ps1
```

## 28.3 `STUDIO OFFLINE`

Comprobar:

- Studio abierto;
- place cargado;
- plugin conectado;
- sesión MCP.

## 28.4 `DRAFT EXPIRED`

Los drafts/views/actions son temporales.

Crear una invocación nueva.

## 28.5 `STALE DRAFT VIEW`

Una página vieja intentó actuar sobre otra revision.

Esto es una protección correcta.

No fuerces la acción.

Usa:

```text
Open Current Draft
```

o reinicia la invocación.

## 28.6 `INVALID ARGUMENT`

No "arregles" manualmente la URL.

Vuelve al schema real y al editor correspondiente.

## 28.7 `JOB FAILED`

Lee el error MCP.

No vuelvas a ejecutar una escritura a ciegas si no sabes si el primer intento alcanzó Studio.

Primero inspecciona el estado real.

---

# 29. Reglas de seguridad operacional

## 29.1 Prepare no es autenticación

El flujo:

```text
draft
→ prepare
→ execute once
```

protege contra:

- clics accidentales;
- prefetch;
- doble ejecución;
- estado stale.

NO protege contra un atacante que pueda acceder al gateway.

## 29.2 Estado actual

El proyecto se construyó como PoC funcional y la autenticación de Internet se pospuso.

Eso significa que, si la URL pública está expuesta y el relay local/Studio están conectados, la superficie de escritura debe considerarse sensible.

## 29.3 Prioridad de hardening

Antes de usarlo de forma más amplia o compartir la URL:

1. añadir autenticación;
2. separar read-only de write;
3. audit log persistente;
4. rate limiting;
5. límites por tool/categoría;
6. protección adicional para destroy/batch/script replace;
7. permitir desactivar escrituras desde el launcher.

No publicar secretos dentro de HTML navegable.

## 29.4 No exponer shell

MCP-WEB no debe crear endpoints de:

```text
shell
cmd
PowerShell arbitrario
eval
exec
arbitrary file execution
```

El relay solamente debe ejecutar tools realmente anunciadas por `tools/list`.

---

# 30. Correctitud de escrituras

Toda escritura debe seguir:

```text
INSPECT
↓
PREPARE
↓
VERIFY SNAPSHOT
↓
EXECUTE ONCE
↓
READ BACK
```

Nunca:

```text
WRITE returned success
→ done
```

Ejemplos:

### Create

```text
create
→ find/get instance
→ verify name/class/parent
```

### Set property

```text
set_properties
→ get_properties
→ verify exact value
```

### Rename

```text
rename
→ find/get
→ verify new name
```

### Reparent

```text
reparent
→ get tree/instance
→ verify parent
```

---

# 31. Destructive operations

Tools como:

```text
destroy
batch
replace script
clear output
undo/redo
playtest
```

pueden producir cambios amplios.

Reglas recomendadas:

1. inspeccionar target;
2. confirmar tool y selector;
3. revisar Prepared;
4. ejecutar una sola vez;
5. verificar estado posterior;
6. en destroy/batch, evitar selecciones ambiguas.

Para tareas grandes, dividir en operaciones pequeñas y verificables.

---

# 32. Manejo de concurrencia

El gateway soporta múltiples drafts/jobs sin depender únicamente de `latest`.

Identificadores:

```text
draft_id
revision
view_id
prepare_id
request_id
result_view_id
```

`/agent/latest` es conveniencia, no fuente de verdad.

No mezclar resultados de dos invocaciones paralelas por confiar en "último resultado".

---

# 33. Estado en memoria

Actualmente drafts/jobs/views/prepared/result views viven en memoria del proceso web.

Consecuencias de un deploy/restart:

- catálogo se restaura automáticamente;
- el relay puede reconectar;
- Studio puede seguir disponible;
- PERO drafts/prepared/actions/jobs no terminados pueden perderse.

Regla:

```text
Render restart durante una operación
→ asumir workflow interrumpido
→ inspeccionar Studio
→ iniciar nueva invocación
```

No reintentar una escritura automáticamente sin verificar si alcanzó Studio.

---

# 34. Render Free / cold start

Una instancia gratuita puede dormir por inactividad.

Consecuencias:

- primera petición lenta;
- timeout superficial de navegador;
- relay esperando;
- catálogo tardando unos segundos en volver.

Recomendación:

```text
abrir /
→ esperar ONLINE
→ Live Health
→ Live Catalog
→ empezar Agent workflow
```

No diagnosticar un cold start de pocos segundos como bug del MCP.

---

# 35. Diagnóstico por markers

`Agent Status` debe mostrar:

```text
DEPLOY_COMMIT
RENDER_INSTANCE_ID
AGENT_PROTOCOL_VERSION
```

Esto es importante porque una capa externa puede mostrar contenido viejo.

Antes de una prueba delicada:

```text
confirmar DEPLOY_COMMIT
confirmar immutable-v1
```

Si el build no coincide con el esperado, no continuar.

---

# 36. Uso desde un chat nuevo

Prompt recomendado:

```text
Tengo un MCP-WEB de Roblox disponible en:

https://mcp-web-roblox.onrender.com/

Usa únicamente acceso Web En vivo. No uses Work ni Codex.

Empieza abriendo solamente la página principal y entra a ChatGPT Agent Gateway siguiendo exclusivamente enlaces HTML reales.

Antes de trabajar confirma:
- AGENT_PROTOCOL_VERSION = immutable-v1
- local_client_online = true
- mcp_connected = true
- studio_connected = true
- catálogo MCP disponible

Usa únicamente tools y schemas reales del catálogo.

Durante Agent Mode:
- usa solo rutas /agent/view/V_..., /agent/action/A_..., /agent/prepared/P_... y /agent/result-view/R_...;
- no construyas manualmente URLs, query params, refs, paths, rid, schemas ni argumentos;
- después de cada mutación usa la nueva View;
- antes de Execute revisa que Prepared muestre exactamente los argumentos deseados;
- si aparece una página legacy /agent/draft/... o una vista stale/inconsistente, detente y vuelve al flujo actual;
- reutiliza Recent Instances/Results cuando sea posible;
- después de cualquier escritura realiza una lectura independiente para verificar el resultado.

Ahora cumple esta petición en Roblox Studio:
<MI PETICIÓN>
```

---

# 37. Ejemplo de misión

Petición:

```text
Inspecciona Workspace.
Crea un Folder llamado GameplayTest si no existe.
Dentro crea una Part llamada Trigger.
Déjala Anchored=true.
Verifica independientemente el resultado.
```

Proceso ideal:

```text
Agent Gateway
↓
tree/find
↓
si GameplayTest existe → reuse
si no → create Folder
↓
verify Folder
↓
create Part with parent candidate
↓
verify Part
↓
set_properties Anchored=true
↓
get_properties
↓
confirm true
```

Nunca crear duplicados si el objeto deseado ya existe salvo que el usuario lo pida.

---

# 38. Buenas prácticas para Roblox

## 38.1 Inspeccionar antes de crear

Antes de crear:

```text
find
tree
selection
```

Evita duplicados y targets incorrectos.

## 38.2 Servidor autoritativo

MCP-WEB solo es transporte. Las decisiones de arquitectura Roblox siguen requiriendo buenas prácticas:

- servidor autoritativo;
- cliente no confiable;
- validación de remotes;
- límites de rate;
- persistencia segura;
- playtest posterior.

## 38.3 No asumir que una escritura equivale a validación

Después de cambios de scripts/Instances, hacer:

```text
read
output
playtest
```

cuando corresponda.

---

# 39. Mejoras futuras prioritarias

Esta sección recoge mejoras que NO son necesarias para demostrar el E2E actual, pero harían el sistema más robusto y parecido a un MCP nativo.

## Prioridad A — Seguridad

### A1. Autenticación real

Actualmente la URL pública es una superficie de control.

Añadir:

- autenticación por usuario;
- sesión;
- secreto rotatorio o mecanismo equivalente;
- separación read/write.

La solución debe ser compatible con navegación de ChatGPT y no depender de secretos visibles en links.

### A2. Modo read-only

Añadir un switch:

```text
READ ONLY
WRITES ENABLED
```

Idealmente controlable desde `run_relay.bat` o config local.

### A3. Confirmación reforzada de destructivas

Para:

```text
destroy
batch
replace_script
```

añadir confirmaciones específicas y, si es posible, resumen de impacto.

---

## Prioridad B — Persistencia

### B1. Persistir audit log

Aunque drafts sigan en memoria, conservar:

```text
timestamp
tool
arguments hash
request_id
result
```

en una store ligera.

### B2. Sobrevivir redeploy

Si el sistema se vuelve de uso diario crítico, considerar Redis/PostgreSQL únicamente para:

- jobs;
- prepared snapshots;
- audit;
- locks.

No hace falta para el PoC.

---

## Prioridad C — Texto grande

### C1. Payload Slots

Diseñar un mecanismo:

```text
payload_id
content
content_hash
TTL
```

y permitir que tools de scripts referencien el payload.

Problema a resolver: cómo entrega ChatGPT el contenido sin fabricar URLs enormes ni depender de formularios inaccesibles.

### C2. Patches primero

Optimizar `patch` con helpers para:

- buscar contexto;
- source hash;
- replace localizado;
- inserción controlada.

Esto puede cubrir gran parte de la edición de código sin source completo.

---

## Prioridad D — UX Agent

### D1. Capability families

Mostrar por defecto familias y esconder aliases:

```text
71 MCP names
≈ N capacidades semánticas
```

manteniendo aliases disponibles.

### D2. Result → Next Action

Desde una Instance result:

```text
Inspect
Get properties
Set property
Rename
Reparent
Open script
```

como enlaces directos.

### D3. Better Recipes

Recipes para:

```text
Create Model hierarchy
Set common physics properties
Inspect selected hierarchy
Find + edit script
Run playtest + read output
```

### D4. Breadcrumbs fuertes

Mostrar siempre:

```text
DEPLOY_COMMIT
PROTOCOL
VIEW_ID
DRAFT_REVISION
TOOL
```

---

## Prioridad E — Observabilidad

### E1. Audit dashboard

Ver:

```text
last 100 requests
duration
tool
success/error
session
```

### E2. Latency breakdown

Separar:

```text
ChatGPT→Render
Render queue
PC polling
MCP
Studio
upload
```

### E3. Health grades

En vez de un booleano único:

```text
WEB: OK
RELAY: OK
MCP: OK
STUDIO: OK
CATALOG: OK
```

---

## Prioridad F — Durabilidad de refs

Refs actuales están ligados a sesión/registry.

Mejoras posibles:

- descriptor con ref + structured path + class + name;
- fallback controlado a path solamente si el schema lo permite;
- detección de stale ref;
- refresh candidate automático mediante lectura segura.

Nunca convertir este fallback en "adivinar".

---

# 40. Cosas a tener especialmente en cuenta

## 40.1 Nunca confiar solo en `latest`

En workflows largos usa `request_id` / `R_...`.

## 40.2 No confundir alias con tool distinta

`find` y `studio_find_instances` pueden ser la misma capacidad.

## 40.3 No asumir unicidad por nombre

Puede haber dos Folders llamados igual.

## 40.4 No reutilizar refs después de reinicio de Studio sin validar

Vuelve a inspeccionar.

## 40.5 No ejecutar si Prepared no coincide

Aunque el usuario "sepa" qué quería hacer.

## 40.6 No arreglar un schema inventando JSON

Abrir tool page y leer `inputSchema`.

## 40.7 No ignorar `isError`

Una respuesta MCP con datos parciales puede seguir ser error.

## 40.8 No publicar el sistema como servicio multiusuario sin auth

El diseño actual es personal/PoC.

## 40.9 Mantener el bridge local versionado

El commit:

```text
b39972f0ac60383ff920f1875aa1810d7914593d
```

contiene correcciones críticas del bridge/lifecycle.

Crear un remote/backup privado para ese repo sería una mejora importante.

## 40.10 Respaldar documentación con el código

En futuras releases actualizar conjuntamente:

```text
PROJECT_STATE.md
CHATGPT_USAGE.md
docs/INSTANCE_TARGET_CONTRACT.md
docs/TOOLS_CAPABILITY_MATRIX.md
MCP_WEB_MASTER_SPEC.md
```

---

# 41. Checklist de release futura

Antes de desplegar otra versión:

```text
[ ] pytest MCP-WEB
[ ] tests bridge
[ ] compileall
[ ] git diff --check
[ ] run_relay.bat
[ ] 71/current tools
[ ] session_count >= 1
[ ] Agent Status markers
[ ] Start invocation → V_
[ ] actions → A_
[ ] Prepare → P_
[ ] result → R_
[ ] stale action rejected
[ ] repeated execute idempotent
[ ] catalog restore after restart
[ ] create → verify
[ ] set → read
[ ] find → reuse
[ ] docs updated
```

Después del deploy:

```text
[ ] DEPLOY_COMMIT correcto
[ ] immutable-v1
[ ] relay connected
[ ] MCP connected
[ ] Studio connected
[ ] catalog restored
[ ] test read
[ ] test prepared snapshot
```

---

# 42. Checklist diario

```text
[ ] Roblox Studio abierto
[ ] proyecto correcto abierto
[ ] run_relay.bat abierto
[ ] dashboard ONLINE
[ ] local relay connected
[ ] Studio connected
[ ] tool_count > 0
[ ] immutable-v1
```

Después ChatGPT puede comenzar.

---

# 43. Troubleshooting rápido

| Síntoma | Causa probable | Acción |
|---|---|---|
| Render tarda mucho | Cold start Free | Esperar y reabrir health |
| `tool_count=0` | Catálogo perdido tras restart | Esperar auto-restore; comprobar relay |
| `studio_connected=false` | Studio/plugin/session | Revisar Studio y bridge |
| `session_count=0` | Lifecycle/session temporal | Esperar ~3 s; revisar procesos MCP |
| `/agent/draft/...` | Legacy/stale route | Volver a Agent Gateway; no ejecutar |
| `STALE DRAFT VIEW` | Acción de revision vieja | Abrir Current View |
| Prepared tiene args erróneos | Vista/cadena incorrecta | No ejecutar; reiniciar invocación |
| Job `pending` mucho tiempo | relay no recibe job | Revisar run_relay/logs |
| ref no resuelve | stale ref/session | Find/get nuevamente |
| script grande imposible | límite link-only | Preferir patch / futuro payload |
| doble nombre en tree | nombres no únicos | usar ref/path real |

---

# 44. Contrato de un agente correcto

Un chat que use MCP-WEB correctamente debe comportarse así:

```text
1. VERIFY PLATFORM
2. DISCOVER REAL TOOL
3. READ REAL SCHEMA
4. INSPECT TARGET
5. CREATE CURRENT IMMUTABLE VIEW
6. BUILD ARGUMENTS THROUGH LINKS
7. PREPARE
8. VERIFY SNAPSHOT
9. EXECUTE ONCE
10. READ RESULT
11. INDEPENDENTLY VERIFY
12. CONTINUE
```

Debe detenerse ante:

```text
legacy route
stale view
unexpected arguments
missing tool
offline Studio
ambiguous target
invalid selector
```

---

# 45. Definición de éxito

MCP-WEB debe considerarse funcional cuando un chat normal puede demostrar:

```text
ChatGPT
→ Render
→ PC
→ MCP
→ Roblox Studio
→ PC
→ Render
→ ChatGPT
```

y además realizar:

```text
READ
+
WRITE
+
INDEPENDENT READBACK
```

Esto ya fue demostrado con el workflow:

```text
SOL_MCP_FINAL_TEST
└── WebControlledPart
    └── Anchored = true
```

---

# 46. Resumen ejecutivo

Estado:

```text
Web Relay: funcional
Relay local: funcional
MCP discovery: funcional
71 tools: publicadas dinámicamente
Studio session: funcional
Catalog auto-restore: funcional
Selector reuse: funcional
Agent Gateway: funcional
Short String Composer: funcional
immutable-v1: funcional
Prepare snapshot: funcional
One-shot execute: funcional
Independent verification: funcional
```

Limitaciones principales:

```text
auth todavía pendiente
estado web en memoria
Render Free/cold start
texto grande mediante links no práctico
refs dependientes de sesión
necesidad de verificar aliases/routing real
```

Meta de uso diario:

```text
Abrir Studio
→ run_relay.bat
→ abrir ChatGPT normal
→ dar una misión
→ ChatGPT inspecciona
→ ejecuta tools reales
→ verifica
```

---

# 47. Fuente de verdad

Cuando haya contradicción entre este documento y el sistema en ejecución, usar este orden:

```text
1. Agent Status del deployment actual
2. /agent/tools y schemas reales
3. /read/catalog
4. resultados reales de Studio
5. código de la release desplegada
6. esta documentación
```

Nunca ejecutar una escritura basándose únicamente en documentación vieja si el schema vivo dice otra cosa.

---

# 48. Documentos relacionados

Recomendados dentro del proyecto:

```text
README.md
PROJECT_STATE.md
CHATGPT_USAGE.md
docs/INSTANCE_TARGET_CONTRACT.md
docs/TOOLS_CAPABILITY_MATRIX.md
MCP_WEB_CHATGPT_BOOTSTRAP.md
MCP_WEB_MASTER_SPEC.md
```

`MCP_WEB_CHATGPT_BOOTSTRAP.md` es el archivo que se debe entregar primero a un chat nuevo.

`MCP_WEB_MASTER_SPEC.md` es la referencia técnica completa para troubleshooting, mantenimiento y evolución.

---

# 49. Release de estabilización `73b109b` — Schema Navigator completo y recuperación del relay

> Esta sección define el estado técnico actual de la release pública `73b109bcdb8548cb9e2d145952b44673c0033172`. Las secciones anteriores que describan como pendientes los bugs de object/array editor, redirect anti-cache, View snapshots o relay cleanup deben tratarse como historia ya corregida.

## 49.1 Release pública

```text
DEPLOY_COMMIT:
73b109bcdb8548cb9e2d145952b44673c0033172

COMMIT:
73b109b Complete immutable schema navigation and relay recovery

AGENT_PROTOCOL_VERSION:
immutable-v1

tool_count:
71
```

Validación pública:

```text
relay: true
mcp: true
studio: true
tools: 71
catalog_generation: 1
```

## 49.2 Root causes corregidas

1. faltaba editor recursivo genérico para objetos, arrays y valores anidados;
2. redirects `303` del Agent no tenían headers anti-cache;
3. Views/pickers podían leer recent values/candidates modificados;
4. cleanup del adaptador MCP podía interrumpir la reconexión mediante `ExceptionGroup`.

Los cuatro puntos quedaron corregidos.

## 49.3 Schema Navigator immutable recursivo

Soporta edición navegable basada en schema para:

```text
object
object + properties
object + additionalProperties:true
object + additionalProperties:<schema>
array<string>
array<number>
array<object>
nested arrays
nested objects
string
number
integer
boolean
enum
null / unions soportadas
```

Principio:

```text
inputSchema real
→ renderer recursivo
→ ActionTokens opacos
→ Draft revision
→ nueva ViewSnapshot
```

### Object editor

Puede ofrecer, según schema:

```text
Edit object
Add field
Edit field
Remove field
Clear object
```

Esto resuelve:

```text
studio_create_instance.properties
studio_set_properties.values
```

### Array editor

Puede ofrecer:

```text
Add item
Edit item
Remove item
Remove last
Clear
```

y editar recursivamente `array<object>`.

Esto hace práctico:

```text
studio_batch.operations
```

## 49.4 Keys y values dinámicos

Para `additionalProperties`, las keys arbitrarias cortas pueden construirse mediante navegación server-rendered.

Los values se editan según el schema/contrato aplicable, no mediante un único editor ciego.

## 49.5 Tipos Roblox

No inventar serializaciones para `Vector3`, `Color3`, `CFrame`, `EnumItem`, `UDim2`, etc.

La representación válida depende de:

```text
inputSchema
normalización de la tool
contrato del MCP/bridge
```

## 49.6 Instance Picker

`Choose Roblox Instance` se reserva para argumentos que realmente representen selectors/Instances.

Un object abierto de propiedades no debe recibir un descriptor de Instance solo por ser un object.

## 49.7 Stable View snapshots

Se estabilizaron:

```text
ActionTokens
Prepare action
recent string values visibles
recent refs
picker candidates
editor snapshots
```

Stale protection:

```text
expected_revision != draft.revision
→ STALE DRAFT VIEW
→ no mutar
```

Replay protection:

```text
A_ consumido
→ no repetir operación
→ reutilizar resulting_url
```

## 49.8 Picker snapshots estables

Un picker antiguo conserva sus candidates. Candidates nuevos requieren nueva View/Picker snapshot.

## 49.9 Redirects dinámicos con anti-cache

Los redirects dinámicos Agent usan:

```text
Cache-Control: no-store, no-cache, must-revalidate, max-age=0
Pragma: no-cache
Expires: 0
CDN-Cache-Control: no-store
Surrogate-Control: no-store
```

Validación:

```text
GET /agent/tool/studio_find_instances/start
→ 303
→ /agent/view/V_...
→ anti-cache headers PASS
```

## 49.10 Legacy routes

Las rutas históricas pueden seguir registradas por compatibilidad, pero el flujo `immutable-v1` final no enlaza `/agent/draft/...`.

Contrato vigente:

```text
V_ / A_ / P_ / R_
```

## 49.11 Prepared exact snapshot

`Prepare Execution` conserva:

```text
PREPARE_ID
DRAFT_ID
DRAFT_REVISION
TOOL
ARGUMENTS
ARGUMENTS_SHA256
```

`Execute now` usa `prepared.arguments_snapshot`.

## 49.12 Execute one-shot y Result Views

Repetir Execute sobre el mismo Prepared reutiliza el job/result ya creado.

Result Views siguen:

```text
R_001 pending
→ Refresh
→ R_002 running
→ Refresh
→ R_003 completed
```

## 49.13 TTL y bounded state

Limpieza TTL para:

```text
drafts
views
actions
prepared
editors
```

## 49.14 Relay recovery

Comportamiento final:

```text
MCP local falla
→ capturar fallo
→ cleanup seguro/idempotente
→ backoff
→ reconectar
→ rediscover tools
→ republicar catálogo si hace falta
→ continuar polling
```

Validación: relay real conectado y 71 tools descubiertas.

## 49.15 Tests finales

```text
MCP-WEB:
32 passed

Bridge:
19 passed

compileall:
PASS

git diff --check:
PASS
```

Bridge, plugin, MCP y contrato de 71 tools no fueron modificados en este pass.

## 49.16 Validación pública del Schema Navigator

```text
studio_create_instance → object editor PASS
studio_set_properties  → object editor PASS
studio_batch           → array editor PASS
```

Prepared alcanzado:

```text
/agent/prepared/P_8db86f6eec1b44af86
```

Caso representativo:

```text
SOL_MCP + Folder
PASS
```

## 49.17 Qué puede hacer ahora ChatGPT Web

### Construcción y diseño

```text
crear Parts/Models/Folders
editar propiedades estructuradas
tamaño/posición/orientación según contrato real
material/color según representación soportada
physics flags
reparent
clone
destroy
batch
```

### Inspección

```text
sessions
place
tree
selection
find
instance
properties
attributes
tags
services
output
```

### Programación

```text
read script
create script
patch script
replace script
open script
list open scripts
```

Para código grande sigue siendo preferible:

```text
read
→ source hash
→ patch localizado
→ readback
```

La navegación link-only sigue siendo poco práctica para cientos/miles de líneas.

### Testing

```text
playtest
read output
undo/redo cuando proceda
independent verification
```

## 49.18 Flujo canónico actual

```text
/
↓
/agent
↓
Agent Status
↓
confirmar:
  DEPLOY_COMMIT = 73b109bcdb8548cb9e2d145952b44673c0033172
  immutable-v1
  relay/MCP/Studio connected
  catalog available
↓
READ-ONLY INSPECT
↓
ASK ALL MISSING DECISIONS ONCE
↓
LOCK PLAN
↓
/agent/tools
↓
tool + schema real
↓
Start invocation
↓
303 + anti-cache
↓
/agent/view/V_...
↓
A_... edits
↓
new V_...
↓
Prepare
↓
/agent/prepared/P_...
↓
verify exact snapshot/hash
↓
Execute once
↓
/agent/result-view/R_...
↓
Refresh if needed
↓
independent read verification
```

## 49.19 Acceptance state

```text
Start → V_                             PASS
Redirect anti-cache                   PASS
Action replay                         PASS
Stale View rejection                  PASS
Stable View actions                   PASS
Stable recent-string snapshot         PASS
Stable Picker candidates              PASS
String nested                         PASS
Number nested                         PASS
Boolean nested                        PASS
Enum nested                           PASS
additionalProperties object           PASS
Nested object                         PASS
Array scalar                          PASS
Array<object>                         PASS
Nested object/array                   PASS
create.properties practical           PASS
set_properties.values practical       PASS
batch.operations practical            PASS
Prepare exact snapshot                PASS
Execute one-shot                      PASS
Result View immutable                 PASS
Legacy not linked by immutable flow   PASS
Relay reconnect after MCP failure     PASS
Full MCP-WEB suite                    PASS — 32 passed
Bridge suite                          PASS — 19 passed
Public deploy                         PASS
```

## 49.20 Limitaciones que siguen siendo reales

```text
autenticación pública pendiente
estado Agent principalmente in-memory
Render puede reiniciar/dormir
refs dependen de sesión/registry
texto fuente muy grande por links sigue siendo poco práctico
las tools solo pueden hacer lo que anuncien sus schemas/bridge
```

## 49.21 Estado final

```text
PUBLIC DEPLOY_COMMIT:
73b109bcdb8548cb9e2d145952b44673c0033172

COMMIT:
73b109b Complete immutable schema navigation and relay recovery

AGENT_PROTOCOL_VERSION:
immutable-v1

PUBLIC ROUTING:
Start 303 → /agent/view/V_... PASS

REDIRECT ANTI-CACHE:
PASS

SCHEMA NAVIGATOR:
recursive immutable object/array editor PASS

create.properties:
PASS

set_properties.values:
PASS

batch.operations:
PASS

STABLE VIEW/PICKER SNAPSHOTS:
PASS

LEGACY LINKS FROM IMMUTABLE AGENT:
ELIMINATED

RELAY RECOVERY:
PASS

MCP-WEB TESTS:
32 passed

BRIDGE TESTS:
19 passed

PUSH TO main:
PASS
```

## 49.18 Resolución typed de propiedades Roblox

La serialización Roblox permanece en `studio-plugin/src/Main.plugin.lua`, mediante `encode`/`decode`. `studio_set_properties.values` y `studio_create_instance.properties` comparten `setProperties`, por lo que usan el mismo contrato canónico.

El bridge expone metadata aditiva en `studio_get_properties` y permite `class_name + names` para describir defaults de una clase sin parentar una instancia. La resolución de `Part.Size`, `SpawnLocation.Position` y propiedades heredadas se realiza por `typeof` de Roblox, no mediante un mapa duplicado en el gateway.

Representaciones canónicas:

```json
{ "$type": "Vector3", "x": 1, "y": 2, "z": 3 }
```

```json
{ "$type": "Color3", "r": 0.3, "g": 0.7, "b": 0.2 }
```

```json
{ "$type": "EnumItem", "enumType": "Material", "name": "Grass" }
```

El Schema Navigator usa editores typed para metadata conocida y conserva el editor genérico únicamente como fallback explícito. `create_instance.properties`, `set_properties.values` y propiedades dentro de `batch` comparten el mismo dispatch immutable, con protección stale/replay y snapshots Prepared exactos.
