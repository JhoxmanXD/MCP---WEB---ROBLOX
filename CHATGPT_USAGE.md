# Uso diario con ChatGPT Web

1. Abre Roblox Studio y el proyecto.
2. Ejecuta `run_relay.bat`.
3. En una conversación nueva de ChatGPT Web, abre primero:

```text
https://mcp-web-roblox.onrender.com/
```

Prompt recomendado:

```text
Tengo un MCP-WEB de Roblox disponible en https://mcp-web-roblox.onrender.com/.
Usa acceso Web En vivo. Empieza únicamente desde la página principal y sigue solamente los enlaces HTML reales del ChatGPT Agent Gateway.
Usa las tools reales del gateway; no inventes endpoints, referencias, ids, paths ni schemas.
Antes de modificar Studio, inspecciona el estado necesario. Después de cada cambio, verifica el resultado con una lectura independiente.
No construyas URLs /api/v1 manualmente si existe un enlace navegable equivalente. Reutiliza Recent Instances/Recent Results y los candidatos que devuelvan las ejecuciones.
```

El flujo navegable es `/agent` → `/agent/tools` → tool real → `ViewSnapshot` → Prepare Execution → Execute now → `ResultView`. Cada mutación devuelve una nueva URL inmutable; sigue el redirect/enlace nuevo y no vuelvas a usar la View anterior.

El Agent Gateway usa URLs inmutables generadas por el servidor para views, actions, prepared invocations y result views. Sigue siempre el nuevo enlace `Open Current Draft` o la nueva View que devuelva cada acción; no reutilices una página anterior después de una mutación ni edites URLs.
Comprueba también `DEPLOY_COMMIT` y `AGENT_PROTOCOL_VERSION` visibles en cada página; si una página no los muestra, trátala como una respuesta legacy/cacheada y vuelve a empezar desde `/`.

Los objetos y arrays se editan desde sus enlaces visibles `Edit object`, `Add field`, `Edit array` y `Add item`. El editor es recursivo: permite construir propiedades, valores anidados y listas de objetos sin inventar URLs ni ids. Los redirects de Agent llevan headers `no-store` para evitar que una respuesta vieja de Render/CDN reemplace una View nueva.

Para workflows de Studio:

- empieza siempre en `/` y entra al Agent Gateway mediante sus enlaces visibles;
- inspecciona sesiones, selección, árbol o búsqueda antes de modificar;
- reutiliza `Recent Instances`/`Recent Results`; no inventes `ref`, `id`, paths ni schemas;
- después de una escritura haz una lectura independiente (`get_instance` o `get_properties`);
- usa recipes para crear, seleccionar y cambiar propiedades;
- para cualquier string corto, abre `Open String Composer (argument)` y pulsa los caracteres visibles; no construyas rutas de append;
- para objetos/arrays, usa únicamente `Edit object`/`Edit array` y sus acciones visibles; termina cada valor antes de volver al editor padre;
- antes de ejecutar inspecciona `PREPARE_ID`, `DRAFT_REVISION`, `ARGUMENTS_SHA256` y `ARGUMENTS` de la página Prepared;
- para resultados pendientes sigue el enlace `Refresh Result` y usa la nueva Result View; no dependas de `/agent/latest` ni refresques una URL de resultado antigua;
- el resultado estructurado conserva el candidato de Instance y su path estructurado; el path con puntos es solo `displayPath` humano.

El modo de enlaces está pensado para argumentos cortos. Para source Luau o JSON grande, usa la API raw existente o el modo humano cuando esté disponible.

## Propiedades Roblox typed

Cuando el bridge publica metadata de la propiedad, el gateway muestra un editor typed en lugar del fallback genérico:

- `Vector3`: componentes `X`, `Y`, `Z`, serializados como `{\"$type\":\"Vector3\",\"x\":...,\"y\":...,\"z\":...}`.
- `Color3`: componentes `R`, `G`, `B` normalizados entre `0` y `1`.
- `EnumItem`: picker con los valores reales del enum, por ejemplo `Material → Grass`.
- `CFrame`, `Vector2`, `UDim`, `UDim2`, `NumberRange` y `BrickColor`: editor typed cuando el bridge confirma el tipo y su representación escribible.

La resolución usa el valor actual para `studio_set_properties` y metadata por `class_name` para `studio_create_instance`; para mantener usable el gateway mientras un plugin antiguo se reinicia, solo existe un fallback auditado para `BasePart` (`Size`, `Position`, `Color`, `Material`, `Anchored`, `CanCollide`) y sus descendientes conocidos (`Part`, `SpawnLocation`, `MeshPart`, etc.). No se adivinan otras propiedades. Si no hay metadata ni contrato auditado, aparece explícitamente el editor genérico de fallback. `create_instance.properties`, `set_properties.values` y operaciones typed dentro de `batch` usan el mismo dispatch.
