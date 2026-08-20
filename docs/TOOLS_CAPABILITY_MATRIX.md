# MCP Tools Capability Matrix

Fuente: `MCP tools/list` del bridge local. El gateway no hardcodea handlers; cada tool se genera desde el catálogo.

| Grupo | Tools | Gateway |
|---|---|---|
| Session/status/place | `studio_list_sessions`, `sessions`, `studio_get_session`, `studio_select_session`, `studio_status`, `status`, `studio_get_place_info`, `place` | Completa para tools sin argumentos; parcial cuando requiere `session_id` |
| Tree/selection/find | `studio_get_tree`, `tree`, `studio_get_selection`, `selection`, `studio_set_selection`, `studio_find_instances`, `find` | Completa para lecturas sin argumentos; parcial para refs, arrays y consultas libres |
| Instance/properties/tags | `studio_get_instance`, `instance`, `studio_get_properties`, `properties`, `studio_get_attributes`, `attributes`, `studio_get_tags`, `tags`, `studio_list_services`, `list_services` | Navegable: refs reales usan picker; objects/arrays usan editor recursivo |
| Create/modify | `studio_create_instance`, `create`, `studio_destroy_instance`, `destroy`, `studio_rename_instance`, `rename`, `studio_clone_instance`, `studio_reparent_instance`, `reparent` | Navegable para escalares, objetos/arrays y refs; la ejecución sigue dependiendo del estado real |
| Properties/attributes/tags/batch | `studio_set_properties`, `set_properties`, `studio_set_attributes`, `set_attributes`, `studio_set_tags`, `set_tags`, `studio_add_tag`, `studio_remove_tag`, `studio_batch`, `batch` | Editor typed para metadata confirmada; fallback genérico explícito cuando no existe metadata |
| Scripts | `studio_read_script`, `script_read`, `read`, `studio_create_script`, `script_create`, `create_script`, `studio_replace_script`, `script_replace`, `replace`, `studio_patch_script`, `script_patch`, `patch`, `studio_open_script`, `open`, `studio_list_open_scripts`, `list_open_scripts` | Lecturas sin args completas; escritura parcial por source/patch grande |
| Output/history | `studio_get_output`, `output`, `studio_clear_output_buffer`, `studio_undo`, `undo`, `studio_redo`, `redo`, `studio_can_undo`, `studio_can_redo` | Completa para defaults/lecturas; acciones de escritura requieren confirmación humana |
| Playtest | `studio_playtest`, `playtest` | Parcial: enum `action` se puede navegar, pero requiere validación del estado real |

## Familias semánticas y aliases

El catálogo actual contiene 71 nombres MCP que representan 38 familias semánticas. El agrupamiento se basa en la pareja canónica/alias del schema y en el prefijo `studio_`; no elimina ningún nombre invocable.

Las familias prácticas prioritarias son: sesiones, estado, place, árbol, selección, búsqueda, inspección de instancia, propiedades, atributos, tags, servicios, creación, renombrado, reparent, clonación, lectura de scripts, apertura de scripts, output e historial. Las familias condicionales son las que requieren source grande, patches con hash, valores Roblox estructurados, batches o acciones de playtest. No hay familias artificialmente bloqueadas por el número de aliases.

## Cobertura medida tras el navigator genérico

- Total descubierto: 71.
- MCP names: 71.
- Capability families: 38.
- Protocol navigable names: 71/71; todas tienen página de schema, draft y ruta de ejecución genérica.
- Practical capability families: 19/38 para workflows normales de lectura y edición acotada.
- Conditional capability families: 19/38 cuando requieren source grande, valores estructurados, batch o estado especial.
- La métrica anterior por nombre (`23/71`) se conserva solo como referencia histórica; los aliases ya no inflan la cobertura semántica.
- Blocked: 0 por nombre. Una tool puede quedar `Not ready` si el usuario no proporciona un valor complejo válido.

La matriz es deliberadamente honesta: listar y describir una tool es dinámico para las 71; el gateway representa recursivamente los schemas JSON, mientras que la ejecución todavía requiere valores válidos y un Studio conectado.

## Property metadata y editores typed

El bridge/plugin es la fuente de verdad de los tipos Roblox. `studio_get_properties` devuelve valores `$type` y `propertyMetadata`; con `class_name` puede describir defaults de una clase sin parentar una instancia. MCP-WEB no mantiene un mapa `Part → propiedades`: la herencia se resuelve en Roblox.

Las formas canónicas confirmadas son `$type: Vector3` con `x/y/z`, `$type: Color3` con `r/g/b` normalizados, `$type: CFrame` con `components`, y `$type: EnumItem` con `enumType/name`. Tipos sin decoder de escritura no reciben un editor falso y caen en fallback seguro.
