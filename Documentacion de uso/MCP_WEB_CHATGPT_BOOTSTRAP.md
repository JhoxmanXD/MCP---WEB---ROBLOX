# MCP-WEB ChatGPT Bootstrap

> Guía corta y autocontenida para que un chat nuevo de ChatGPT pueda conectarse al MCP-WEB de Roblox Studio, entender la misión completa antes de modificar nada y ejecutarla con el mínimo de ida y vuelta posible.

## 1. Qué es este sistema

`MCP-WEB` es un gateway web que permite que un chat normal de ChatGPT con **Web En vivo** use, de forma indirecta, el MCP local de Roblox Studio.

Cadena real:

```text
ChatGPT normal
→ Web En vivo
→ MCP-WEB en Render
→ relay_client.py en el PC
→ Roblox Studio MCP
→ Roblox Studio
→ relay_client.py
→ Render
→ ChatGPT
```

El sistema fue verificado end-to-end con lectura y escritura reales sobre Roblox Studio.

## 2. URL de entrada

Empieza siempre desde:

```text
https://mcp-web-roblox.onrender.com/
```

No empieces construyendo URLs internas manualmente.

## 3. Protocolo esperado

El Agent Gateway debe anunciar:

```text
AGENT_PROTOCOL_VERSION: immutable-v1
```

El deployment final validado al redactar este documento corresponde a:

```text
DEPLOY_COMMIT:
73b109bcdb8548cb9e2d145952b44673c0033172
```

Release estabilizada:

```text
73b109b Complete immutable schema navigation and relay recovery
```

El commit puede cambiar en releases futuras. Si cambia, usa el que muestre públicamente `Agent Status` como fuente de verdad.

El flujo de agente correcto usa rutas opacas e inmutables:

```text
/agent/view/V_...
/agent/action/A_...
/agent/prepared/P_...
/agent/result-view/R_...
```

Si el flujo principal termina en:

```text
/agent/draft/d_...
```

trátalo como legacy y no ejecutes escrituras desde esa página.

## 4. Estado mínimo antes de trabajar

En la página principal / Agent Status confirma:

```text
local_client_online: true
mcp_connected: true
studio_connected: true
tool_count: 71
AGENT_PROTOCOL_VERSION: immutable-v1
```

La cantidad de tools es dinámica y viene del MCP real mediante `tools/list`. Si en el futuro cambia, el catálogo publicado es la fuente de verdad.

---

# 5. MODO PREFLIGHT OBLIGATORIO — HACER LAS PREGUNTAS CORRECTAS UNA SOLA VEZ

Esta es la regla principal para reducir iteraciones.

Antes de cualquier escritura, el chat debe hacer dos cosas:

```text
A. VALIDACIÓN + INSPECCIÓN SOLO LECTURA
B. UNA ÚNICA RONDA DE PREGUNTAS COMPLETA
```

No debe empezar a crear, borrar, mover, renombrar, reparentar, modificar propiedades o editar scripts mientras falten decisiones importantes del usuario.

## 5.1 Primero inspeccionar, sin modificar

Antes de preguntar cosas que Studio puede responder por sí mismo, usa únicamente lectura para descubrir:

- Place actual.
- `Workspace` y estructura relevante.
- selección actual, si importa.
- objetos existentes con nombres relacionados con la misión.
- refs/paths reales de esos objetos.
- propiedades actuales que influyan en la tarea.
- scripts existentes relacionados.
- duplicados o restos de pruebas.
- estado de Output si la misión trata de un bug.

Objetivo:

```text
NO preguntar al usuario algo que pueda descubrirse de forma segura leyendo Studio.
```

## 5.2 Después hacer UNA sola batería de preguntas

Una vez entendida la situación real, identifica **todos los datos que todavía faltan** y pregúntalos juntos en un único mensaje.

No hagas una pregunta, ejecutes un poco, luego preguntes otra cosa que ya podías haber previsto.

Agrupa únicamente las preguntas relevantes para la misión.

### A. Resultado final / criterio de terminado

Preguntar si falta claridad sobre:

- qué debe existir al terminar;
- qué comportamiento debe tener;
- qué debe dejar de existir;
- qué condiciones exactas definen `DONE`.

### B. Qué conservar / qué puede destruirse

Para limpiezas, reemplazos o refactors, confirmar explícitamente:

- objetos que **deben conservarse**;
- objetos que **sí pueden eliminarse**;
- si la lista de eliminación es cerrada (`allowlist`);
- qué hacer con objetos adicionales inesperados;
- si se permite sobrescribir/reemplazar objetos ya existentes.

Para operaciones destructivas NO uses frases ambiguas como:

```text
borra todo lo viejo
limpia las pruebas
elimina lo innecesario
```

sin convertirlas antes en targets concretos.

### C. Reutilizar, modificar o crear nuevo

Si ya existe algo parecido, confirmar si se debe:

```text
REUSE
MODIFY
REPLACE
CREATE NEW
```

Si hay duplicados, no decidas solo por nombre. Usa refs/paths reales y pregunta cuál conservar si la intención no es evidente.

### D. Jerarquía / parent

Si se crearán objetos, confirmar cuando falte:

- parent deseado;
- jerarquía final;
- si debe quedar directamente en `Workspace` o dentro de Model/Folder/etc.

No inventes `Workspace` como selector interno. Resuelve la Instance real mediante el gateway.

### E. Nombres exactos

Confirmar nombres exactos cuando el usuario no los haya dado.

Si el usuario sí los dio, respétalos exactamente, incluyendo:

```text
Mayúsculas
minúsculas
espacios
_
-
```

No preguntar de nuevo por un nombre ya especificado claramente.

### F. Geometría y transformaciones

Para Parts, Models, plataformas, islas, edificios, triggers, etc., preguntar de una vez lo que falte:

- tamaño / dimensiones;
- posición aproximada o exacta;
- orientación/rotación;
- altura;
- separación entre objetos;
- forma (`Part`, `MeshPart`, varias Parts, etc.) si importa.

Si el usuario dice algo abierto como:

```text
crea una isla
```

no inventes automáticamente tamaño, altura y estilo si esas decisiones importan para el resultado.

### G. Apariencia

Si aplica, preguntar conjuntamente:

- color;
- material;
- transparencia;
- textura/estilo;
- aspecto simple de prototipo vs acabado visual.

No bloquear una tarea puramente funcional por detalles visuales irrelevantes.

### H. Física / colisión

Cuando corresponda, aclarar:

- `Anchored`;
- `CanCollide`;
- `CanTouch`;
- `CanQuery`;
- gravedad/comportamiento físico;
- si el jugador debe poder caminar encima, atravesarlo o activarlo.

### I. Gameplay / comportamiento

Para sistemas interactivos preguntar, si falta:

- qué activa el sistema;
- qué hace exactamente;
- quién puede activarlo;
- cooldown;
- estados;
- qué ocurre en errores/casos límite;
- si debe ser server-authoritative;
- persistencia, si aplica.

Pregunta por **comportamiento deseado**, no obligues al usuario a diseñar la implementación técnica si puede decidirla el agente.

### J. Scripts

Para scripts, aclarar de una vez:

- comportamiento final;
- dónde debe vivir el script;
- Server Script / LocalScript / ModuleScript si la decisión importa y no puede inferirse;
- eventos/remotes existentes que debe reutilizar;
- si puede crear nuevos remotes/módulos;
- qué interfaces existentes no debe romper.

No preguntes al usuario cómo escribir el código si puede resolverlo el agente.

### K. Playtest y validación

Confirmar, si no es obvio:

- si debe hacer playtest después;
- qué resultado debe comprobar;
- si basta validación estructural o se requiere comportamiento en runtime;
- qué errores/output deben considerarse bloqueo.

### L. Publicación / guardado

No publiques ni hagas operaciones externas adicionales salvo solicitud explícita.

Si la misión implica algo que puede afectar una experiencia publicada, aclara el alcance antes.

## 5.3 No preguntar lo que ya está respondido

Si la petición ya especifica:

```text
nombre
parent
tamaño
color
Anchored
qué borrar
qué conservar
```

NO vuelvas a preguntar esos datos.

Las preguntas deben ser únicamente las decisiones que realmente faltan.

## 5.4 Si el usuario dice "decide tú"

Puedes elegir valores razonables para decisiones no destructivas y reversibles.

Antes de ejecutar, resume una sola vez las decisiones que vas a tomar.

Para acciones destructivas, pérdida de datos, publicación o cambios difíciles de revertir, no asumas silenciosamente.

## 5.5 Entregar un Plan Cerrado antes de escribir

Después de recibir las respuestas, resume brevemente:

```text
OBJETIVO FINAL
QUÉ SE CONSERVA
QUÉ SE ELIMINA
QUÉ SE CREA/MODIFICA
PROPIEDADES CLAVE
VALIDACIONES FINALES
```

Si el usuario confirma o si su respuesta ya hace el plan inequívoco, procede sin seguir preguntando.

## 5.6 Cuándo sí volver a preguntar durante la ejecución

Solo si aparece algo que NO era razonablemente previsible, por ejemplo:

- dos targets reales igualmente válidos y no hay criterio para elegir;
- la estructura de Studio contradice lo acordado;
- una acción requerida destruiría algo no autorizado;
- una tool/schema no permite el plan acordado;
- el usuario pidió una característica técnicamente incompatible;
- aparece un bloqueo de seguridad/protocolo;
- falta una decisión nueva que cambia materialmente el resultado.

No volver a preguntar por detalles menores que puedas resolver de forma segura y reversible.

---

# 6. Reglas obligatorias para ChatGPT durante MCP-WEB

1. Usa **Web En vivo**.
2. Empieza únicamente desde la página principal.
3. Sigue solamente enlaces HTML reales que exponga el gateway.
4. No inventes URLs, query params, `rid`, refs, paths, schemas ni argumentos.
5. Antes de modificar Studio, completa el Preflight anterior.
6. Usa el catálogo real para conocer el schema de cada tool.
7. Reutiliza `Recent Instances`, refs y resultados reales cuando estén disponibles.
8. Después de cada mutación sigue la nueva `/agent/view/V_...`.
9. Antes de ejecutar, abre `Prepare Execution` y revisa el snapshot.
10. Ejecuta únicamente si la página `/agent/prepared/P_...` muestra exactamente la tool y argumentos deseados.
11. Después de cualquier escritura, verifica el resultado mediante una lectura independiente.
12. Si recibes una página stale, legacy, inconsistente o con argumentos inesperados, detente y vuelve a la vista actual del Agent Gateway.
13. Para operaciones destructivas, usa targets/ref/path concretos y una allowlist; nunca una interpretación amplia.
14. No consideres un `success` de escritura como prueba suficiente.

# 7. Flujo recomendado completo

```text
/
→ ChatGPT Agent Gateway
→ Agent Status
→ VALIDAR immutable-v1
→ INSPECCIÓN SOLO LECTURA
→ detectar estado real + duplicados + constraints
→ UNA RONDA de preguntas faltantes
→ resumir Plan Cerrado
→ Tools o Recipes
→ seleccionar tool real
→ Start invocation
→ /agent/view/V_...
→ configurar argumentos mediante enlaces
→ Prepare Execution
→ /agent/prepared/P_...
→ verificar snapshot + hash
→ Execute now
→ /agent/result-view/R_...
→ Refresh Result si hace falta
→ completed
→ lectura independiente de verificación
→ repetir solo si el plan requiere más pasos
→ verificación final contra criterios acordados
```

# 8. Qué comprobar en `Prepared`

Antes de una escritura, confirma:

```text
PREPARE_ID: P_...
DRAFT_REVISION: ...
TOOL: ...
ARGUMENTS_SHA256: ...
ARGUMENTS:
...
```

El snapshot preparado es inmutable. `Execute now` debe ejecutar esos argumentos congelados, no el estado mutable de un draft.

Si cualquier argumento es distinto de lo esperado:

```text
NO EJECUTAR.
```

# 9. Strings cortos

El gateway dispone de un `String Composer` navegable para construir valores cortos mediante enlaces.

Soporta al menos:

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

y símbolos adicionales.

Úsalo para nombres arbitrarios cortos.

Los valores recientes y presets deben preferirse cuando existan; el compositor carácter por carácter es el fallback.

# 10. Instances, refs y paths

Nunca inventes una referencia de Roblox.

Ejemplos reales tienen forma:

```text
rbx:studio_...:i_...
```

Los paths de presentación pueden verse como:

```text
p.Model.Part
```

pero el contrato interno puede requerir un path estructurado.

Usa:

```text
Instance Picker
Recent Instances
Recent Results
Current Selection
Tree
Find
```

y deja que el gateway adapte el selector al schema real de la tool.

Los refs no deben tratarse como permanentes entre reinicios de Studio. Si una referencia deja de ser válida, vuelve a descubrir la Instance.

# 11. Escrituras

La escritura correcta sigue:

```text
View actual
→ configurar argumentos
→ Prepare
→ comprobar snapshot exacto
→ Execute once
→ leer resultado
→ verificar de forma independiente
```

Ejemplo de verificación final real ya conseguida:

```text
SOL_MCP_FINAL_TEST
└── WebControlledPart
    └── Anchored = true
```

La lectura independiente confirmó el Folder, la Part, el parent y `Anchored: true`.

# 12. Herramientas y aliases

El catálogo publica actualmente 71 nombres MCP y contiene aliases.

Ejemplos:

```text
studio_get_tree ↔ tree
studio_find_instances ↔ find
studio_create_instance ↔ create
studio_get_properties ↔ properties
studio_set_properties ↔ set_properties
studio_read_script ↔ script_read ↔ read
studio_patch_script ↔ script_patch ↔ patch
```

No asumas que un alias y su nombre `studio_*` tienen distinto comportamiento MCP. El catálogo real y el flujo HTML actual son la fuente de verdad.

Si una variante te lleva a una ruta legacy, no la ejecutes: vuelve a `/agent/tools` y usa una variante que permanezca en el flujo `immutable-v1`, siempre verificando que corresponde a la misma capacidad/schema.

# 13. Lecturas rápidas

Existen rutas navegables útiles:

```text
/read/health
/read/catalog
/read/sessions
/read/latest
```

Estas sirven para comprobaciones rápidas y lecturas seguras.

Para trabajo general y escrituras, usa preferiblemente:

```text
/agent
```

# 14. Si algo falla

### `tool_count: 0`

Espera unos segundos. Después de un reinicio de Render, el relay local debe detectar que el catálogo falta y republicar las tools automáticamente.

Si no se recupera:

- comprueba que `run_relay.bat` siga abierto;
- comprueba el MCP local;
- vuelve a `Live Health` / `Live Catalog`.

### `studio_connected: false`

Comprueba:

- Roblox Studio abierto;
- proyecto abierto;
- plugin/bridge activo;
- `run_relay.bat` activo.

### `session_count: 0`

Espera unos segundos y vuelve a consultar. El bridge final limpia procesos `stdio` huérfanos y mantiene una única instancia `streamable-http`; la recuperación observada fue de aproximadamente 3 segundos.

### `STALE DRAFT VIEW`

No reutilices el enlace viejo. Sigue `Open Current Draft` o el enlace equivalente que entregue una nueva `V_...`.

### Página legacy `/agent/draft/...`

No ejecutes escrituras desde ella. Regresa al Agent Gateway y reinicia la invocación desde los enlaces actuales.

### Markers de build contradictorios

Si distintas páginas muestran commits/protocolos distintos:

- no asumas inmediatamente que existen dos instancias de Render;
- trata la respuesta incompatible como posiblemente stale/legacy;
- no ejecutes desde ella;
- vuelve a Agent Status y al flujo immutable actual;
- si el problema persiste de forma reproducible, detente y repórtalo.

# 15. Limitación importante: texto grande

El modo navegable funciona bien para:

- inspección;
- búsqueda;
- creación de Instances;
- refs/paths;
- propiedades;
- nombres cortos;
- operaciones estructuradas;
- patches pequeños.

No es práctico introducir cientos de líneas de Luau carácter por carácter mediante links.

Para scripts grandes:

1. prioriza `patch` / `script_patch` si el schema real permite una edición localizada;
2. usa operaciones estructuradas pequeñas;
3. si hace falta reemplazar source completo, puede ser necesario un modo humano/raw o una futura capa de payloads.

No simules soporte práctico donde no existe.

# 16. Prompt listo para pegar en un chat nuevo

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

IMPORTANTE: antes de modificar Roblox Studio, entra en MODO PREFLIGHT.

1. Haz primero toda la inspección SOLO LECTURA que necesites para entender el estado real del proyecto, targets existentes, duplicados, refs/paths, propiedades y scripts relevantes.
2. Después identifica TODO lo que todavía falte decidir para completar mi petición correctamente.
3. Hazme UNA SOLA ronda de preguntas, agrupando todas las decisiones necesarias de una vez.
4. No me preguntes datos que ya te di ni cosas que puedas descubrir leyendo Studio.
5. Para una tarea destructiva pregunta/define una allowlist exacta de qué puede borrarse y qué debe conservarse.
6. Para creación visual pregunta en esa misma ronda, si falta: parent, tamaño, posición, orientación, material, color, física y comportamiento relevante.
7. Para sistemas/scripts pregunta por el comportamiento final, integración, restricciones y validación, no por detalles de implementación que puedas decidir tú.
8. Si digo "decide tú", elige valores razonables para decisiones reversibles y resume tus decisiones antes de ejecutar.
9. Después de mis respuestas, resume un PLAN CERRADO con el criterio exacto de DONE y procede sin seguir preguntando, salvo que aparezca una contradicción real, riesgo destructivo nuevo o bloqueo técnico imprevisible.

Usa únicamente tools y schemas reales del catálogo.

Durante Agent Mode:
- usa solo rutas /agent/view/V_..., /agent/action/A_..., /agent/prepared/P_... y /agent/result-view/R_...;
- no construyas manualmente URLs, query params, refs, paths, rid, schemas ni argumentos;
- después de cada mutación usa la nueva View;
- antes de Execute revisa que Prepared muestre exactamente los argumentos deseados;
- si aparece una página legacy /agent/draft/... o una vista stale/inconsistente, detente y vuelve al flujo actual;
- reutiliza Recent Instances/Results cuando sea posible;
- después de cualquier escritura realiza una lectura independiente para verificar el resultado;
- no consideres una escritura terminada únicamente porque devolvió success.

Ahora cumple esta petición en Roblox Studio:
<ESCRIBE AQUÍ LA MISIÓN>
```

# 17. Ejemplo del comportamiento esperado

Usuario:

```text
Quiero limpiar Workspace y hacer una isla con SpawnLocation encima.
```

ChatGPT NO debería empezar borrando ni debería preguntar una cosa por turno.

Primero inspecciona de solo lectura y después pregunta en una sola ronda, por ejemplo:

```text
Ya inspeccioné Workspace. Antes de modificarlo necesito cerrar estas decisiones:

1. Limpieza: encontré A, B, C... ¿elimino exactamente X/Y/Z y conservo Camera/Terrain/etc.?
2. Island: ¿una sola Part o composición? ¿tamaño aproximado?
3. Posición/altura: ¿centro del mapa y a qué altura?
4. Apariencia: ¿material/color o prototipo simple?
5. Física: ¿Anchored=true y CanCollide=true?
6. SpawnLocation: ¿reutilizo el actual y lo muevo, o creo uno nuevo?
7. Validación: ¿quieres además playtest para comprobar spawn?
```

Después de recibir las respuestas, el chat debe poder ejecutar todo el plan sin volver a interrumpir al usuario por decisiones previsibles.

# 18. Regla final

Para cualquier misión real:

```text
VALIDATE
→ READ-ONLY INSPECT
→ ASK ALL MISSING DECISIONS ONCE
→ LOCK PLAN
→ ACT
→ VERIFY
```

Nunca:

```text
ACT
→ descubrir que faltaba una decisión
→ preguntar
→ ACT
→ volver a preguntar otra cosa previsible
```

---

Para detalles de arquitectura, rutas, lifecycle, catálogo completo, seguridad, troubleshooting y mejoras futuras, consulta `MCP_WEB_MASTER_SPEC.md`.

---

# 19. Release final estabilizada del Agent Gateway — 2026-08-19

Esta sección sustituye como estado operativo actual a la auditoría previa de la release `08e86ce...`.

Release pública validada:

```text
DEPLOY_COMMIT:
73b109bcdb8548cb9e2d145952b44673c0033172

AGENT_PROTOCOL_VERSION:
immutable-v1

tool_count:
71
```

Durante la validación pública final también se confirmó:

```text
local_client_online: true
mcp_connected: true
studio_connected: true
```

El `RENDER_INSTANCE_ID` puede cambiar entre reinicios/deploys y nunca debe hardcodearse. `Agent Status` y los markers vivos siguen siendo la fuente de verdad.

## 19.1 Estado del protocolo

El flujo canónico sigue siendo:

```text
GET /agent/tool/{tool_name}/start
→ crea d_... internamente
→ crea V_...
→ 303 See Other
→ /agent/view/V_...
```

Una página `/agent/view/V_...` puede mostrar `DRAFT_ID: d_...` y eso es NORMAL. Lo legacy es navegar directamente a `/agent/draft/d_...`.

El flujo Agent de la release final no enlaza rutas `/agent/draft/...`.

## 19.2 Redirects Agent endurecidos contra caché

La release `73b109b` corrigió el bug de redirects `303` sin headers anti-cache.

Los redirects dinámicos del Agent ahora deben incluir protección coherente del tipo:

```text
Cache-Control: no-store, no-cache, must-revalidate, max-age=0
Pragma: no-cache
Expires: 0
CDN-Cache-Control: no-store
Surrogate-Control: no-store
```

Validación pública:

```text
Start invocation:
303 → /agent/view/V_...

Headers anti-cache:
PASS
```

## 19.3 Schema Navigator immutable completo

La limitación anterior donde `properties`, `values` u `operations` solo mostraban presets parciales quedó corregida.

La release final incorpora editor immutable recursivo para:

```text
object
additionalProperties
array
array<object>
nested object/array
string
number
integer
boolean
enum
null / unions soportadas por el renderer
```

Las mutaciones siguen:

```text
V_...
→ A_...
→ nueva V_...
```

No regresan a `/agent/draft/...`.

### Objetos abiertos

Para schemas como:

```json
{
  "type": "object",
  "additionalProperties": true
}
```

el navegador puede construir claves dinámicas y editar sus valores mediante navegación server-rendered.

Esto hace prácticos:

```text
studio_create_instance.properties
studio_set_properties.values
```

sin construir JSON manualmente.

### Arrays de objects

El renderer puede editar arrays estructurados y objetos anidados.

Esto hace práctico:

```text
studio_batch.operations
```

mediante operaciones navegables de agregar/editar/eliminar items y fields, según el schema real.

## 19.4 Tipos Roblox: no inventar representación

El editor estructurado NO autoriza a inventar formatos para `Vector3`, `Color3`, `CFrame`, `EnumItem`, `UDim2` u otros tipos Roblox.

Regla:

```text
tool schema real
+ contrato real del MCP/bridge
→ representación válida
```

## 19.5 Instance Picker solo para selectors de Instance

`Choose Roblox Instance` no es un editor universal de objetos.

Un argumento abierto como `values` no debe recibir un descriptor de Instance solo porque ambos sean objetos.

## 19.6 Views, actions y candidates estables

La estabilización corrigió los problemas donde una misma View podía reconstruir enlaces auxiliares desde estado reciente.

Ahora se congelan de forma coherente:

```text
ActionTokens asociados
recent string values visibles
recent refs / picker candidates
snapshots necesarios para el editor
```

Se mantienen:

```text
expected_revision != draft.revision
→ STALE DRAFT VIEW
→ no mutar
```

y protección contra replay de `A_...`.

## 19.7 Prepared y Result

Flujo:

```text
View actual
→ configurar argumentos
→ Prepare Execution
→ /agent/prepared/P_...
→ comprobar TOOL + DRAFT_REVISION + ARGUMENTS_SHA256 + ARGUMENTS
→ Execute now
→ /agent/result-view/R_...
```

`Execute now` usa el `arguments_snapshot` congelado del Prepared.

## 19.8 Relay local endurecido

La release final endureció el lifecycle del relay para que una desconexión temporal del MCP local no termine el proceso por un `ExceptionGroup` durante `adapter.close()`.

Comportamiento objetivo actual:

```text
MCP local se desconecta temporalmente
→ relay detecta el fallo
→ cleanup seguro
→ backoff
→ reconecta
→ vuelve a descubrir/publicar tools si hace falta
→ continúa polling
```

## 19.9 TTL y limpieza

La release final añadió/fortaleció limpieza TTL para:

```text
drafts
views
actions
prepared
editors
```

## 19.10 Suites finales

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

Bridge, plugin, MCP y contrato base de 71 tools no fueron modificados durante este pass.

## 19.11 Validación pública representativa

```text
Start invocation                   PASS
303 → /agent/view/V_...            PASS
redirect anti-cache                PASS
legacy /agent/draft in Agent links ELIMINADO
studio_create_instance object edit PASS
studio_set_properties object edit  PASS
studio_batch array edit            PASS
Prepared route /agent/prepared/P_  PASS
relay/MCP/Studio connected         PASS
tool_count 71                      PASS
```

También se validó un caso representativo `SOL_MCP + Folder`.

## 19.12 Qué puede hacer ahora un chat normal

Con Web En vivo y el catálogo MCP actual, el sistema está diseñado para workflows como:

```text
INSPECT
→ FIND
→ CREATE
→ SET PROPERTIES
→ REPARENT
→ RENAME
→ DESTROY
→ BATCH
→ READ/PATCH SCRIPTS
→ PLAYTEST
→ READ OUTPUT
→ VERIFY
```

Para construcción/diseño, el renderer estructurado ya puede expresar objetos y arrays complejos que antes bloqueaban tamaño, posición, material, color y batches, siempre usando la representación exacta aceptada por el MCP.

Para programación siguen siendo preferibles `studio_read_script` y `studio_patch_script` para cambios localizados. El source muy grande mediante navegación link-only sigue siendo poco práctico.

## 19.13 Flujo recomendado actual

```text
/
→ ChatGPT Agent Gateway
→ Agent Status
→ confirmar DEPLOY_COMMIT = 73b109bcdb8548cb9e2d145952b44673c0033172
→ confirmar immutable-v1 + relay/MCP/Studio
→ inspección solo lectura
→ una sola ronda de decisiones faltantes
→ plan cerrado
→ Tools
→ tool real + schema real
→ Start invocation
→ /agent/view/V_...
→ editar argumentos mediante V_/A_
→ Prepare Execution
→ /agent/prepared/P_...
→ verificar snapshot exacto
→ Execute now
→ /agent/result-view/R_...
→ Refresh Result si hace falta
→ lectura independiente
→ continuar hasta DONE
```

## 19.14 Estado canónico actual

```text
PUBLIC DEPLOY_COMMIT:
73b109bcdb8548cb9e2d145952b44673c0033172

AGENT_PROTOCOL_VERSION:
immutable-v1

TOOLS:
71

SCHEMA NAVIGATOR:
recursive immutable object/array editing enabled

REDIRECT ANTI-CACHE:
enabled / publicly validated

LEGACY /agent/draft LINKS FROM IMMUTABLE AGENT:
removed

RELAY RECOVERY:
hardened and validated

TESTS:
MCP-WEB 32 passed
Bridge 19 passed
```

## 19.15 Propiedades Roblox typed

El bridge/plugin es la fuente de verdad de la metadata. Para una instancia existente, `studio_get_properties` devuelve el valor estructurado y `propertyMetadata`; para `studio_create_instance`, `studio_get_properties` puede describir una clase mediante `class_name` sin parentar una instancia temporal.

El gateway usa esa metadata para dispatch typed en `properties`, `values` y objetos nested de `batch`:

- `Vector3`: `X/Y/Z`, JSON `$type: Vector3` con `x/y/z`.
- `Color3`: `R/G/B`, JSON `$type: Color3` con valores `0–1`.
- `EnumItem`: picker de valores reales, JSON `$type: EnumItem` con `enumType/name`.
- `CFrame`, `Vector2`, `UDim`, `UDim2`, `NumberRange` y `BrickColor`: typed solo cuando la metadata confirma el tipo y existe decoder escribible.

La herencia se resuelve en Roblox mediante la clase real. Como continuidad segura durante el reinicio de un plugin antiguo, MCP-WEB tiene además un fallback auditado limitado a `BasePart.Size`, `Position`, `Color`, `Material`, `Anchored` y `CanCollide`, heredado por clases conocidas como `Part` y `SpawnLocation`; fuera de ese conjunto no adivina. Cuando no existe metadata ni contrato auditado, o el tipo no tiene decoder/editor escribible, se muestra un fallback explícito y no se inventa una serialización.
