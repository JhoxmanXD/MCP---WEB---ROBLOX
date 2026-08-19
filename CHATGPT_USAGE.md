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
Usa acceso Web En vivo. Empieza abriendo únicamente la página principal y sigue los enlaces HTML del ChatGPT Agent Gateway.
Antes de modificar Studio, inspecciona el estado necesario. Después de cada cambio, verifica el resultado con una lectura independiente.
No construyas URLs /api/v1 manualmente si existe un enlace navegable equivalente.
```

El flujo navegable es `/agent` → `/agent/tools` → tool real → draft → Prepare Execution → Execute now → result.

El modo de enlaces está pensado para argumentos cortos. Para source Luau o JSON grande, usa la API raw existente o el modo humano cuando esté disponible.
