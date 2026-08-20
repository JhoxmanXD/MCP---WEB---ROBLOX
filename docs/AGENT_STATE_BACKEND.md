# Agent Mode: estado compartido

Los identificadores `V_`, `A_`, `P_` y `R_` son referencias opacas a estado mutable. No deben depender de que la siguiente petición llegue al mismo proceso. `web/agent_state.py` separa el backend de persistencia de las rutas Agent.

## Modos

- `AGENT_STATE_BACKEND=memory` (predeterminado): desarrollo local y pruebas de un solo proceso.
- `AGENT_STATE_BACKEND=redis`: backend Redis-compatible compartido para producción.

Configuración de producción:

```text
AGENT_STATE_BACKEND=redis
AGENT_STATE_URL=<redis-compatible-url>
AGENT_STATE_NAMESPACE=mcp-web:agent:immutable-v1
AGENT_STATE_TTL_SECONDS=3600
AGENT_STATE_LOCK_SECONDS=60
```

La URL se configura como secreto del servicio, nunca en el repositorio. El namespace guarda un único documento JSON atómico en `<namespace>:state`, con `schema_version`, `revision`, `updated_at` y `state`. Draft, View, Action, editor, Prepared y Result tienen locators centrales que apuntan a esa misma key física y a su ruta JSON (`drafts/<id>`, `actions/<id>`, etc.); no hay keys duplicadas que puedan divergir. El estado usa únicamente objetos/listas/strings/números/booleanos/null JSON; no se serializan objetos Python.

Cada petición `/agent/*` adquiere un lock corto, carga el snapshot, ejecuta la ruta y lo guarda con `EX` y CAS de revisión. Antes de publicar una respuesta que renderiza enlaces, se verifica que cada `A_` esté en el snapshot persistido, que deserialice y que sus Draft/View/Editor/Prepared/Result propietarios existan. Esto cubre navegación alternada entre procesos, consumo de acciones, preparación de ejecución y vistas de resultado. El estado se limita por colección para evitar crecimiento ilimitado y las fechas de draft se siguen aplicando en las rutas Agent.

Si Redis no está configurado, no responde, entrega un documento incompatible o hay un conflicto CAS, Agent Mode devuelve un error `503`, `500` o `409` y no cae silenciosamente a `MemoryStore`. Así no se crean enlaces que otro proceso no pueda resolver.

## Diagnóstico

Revisar:

- `/agent/status`
- `/api/v1/health.json`
- `/api/v1/dashboard.json`

Campos relevantes: `agent_state_backend.mode`, `shared`, `connected`, `roundtrip`, `schema_version`, `namespace` y `ttl_seconds`. `roundtrip` se prueba una vez al startup con una key efímera; la URL de conexión y sus credenciales nunca aparecen en las respuestas.
