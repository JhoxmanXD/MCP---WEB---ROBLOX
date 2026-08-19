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

El flujo navegable es `/agent` → `/agent/tools` → tool real → draft → Prepare Execution → Execute now → result.

Para workflows de Studio:

- empieza siempre en `/` y entra al Agent Gateway mediante sus enlaces visibles;
- inspecciona sesiones, selección, árbol o búsqueda antes de modificar;
- reutiliza `Recent Instances`/`Recent Results`; no inventes `ref`, `id`, paths ni schemas;
- después de una escritura haz una lectura independiente (`get_instance` o `get_properties`);
- usa recipes para crear, seleccionar y cambiar propiedades;
- el resultado estructurado conserva el candidato de Instance y su path estructurado; el path con puntos es solo `displayPath` humano.

El modo de enlaces está pensado para argumentos cortos. Para source Luau o JSON grande, usa la API raw existente o el modo humano cuando esté disponible.
