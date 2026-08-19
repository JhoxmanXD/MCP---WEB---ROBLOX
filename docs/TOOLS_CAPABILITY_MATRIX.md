# MCP Tools Capability Matrix

Fuente: `MCP tools/list` del bridge local. El gateway no hardcodea handlers; cada tool se genera desde el catálogo.

| Grupo | Tools | Gateway |
|---|---|---|
| Session/status/place | `studio_list_sessions`, `sessions`, `studio_get_session`, `studio_select_session`, `studio_status`, `status`, `studio_get_place_info`, `place` | Completa para tools sin argumentos; parcial cuando requiere `session_id` |
| Tree/selection/find | `studio_get_tree`, `tree`, `studio_get_selection`, `selection`, `studio_set_selection`, `studio_find_instances`, `find` | Completa para lecturas sin argumentos; parcial para refs, arrays y consultas libres |
| Instance/properties/tags | `studio_get_instance`, `instance`, `studio_get_properties`, `properties`, `studio_get_attributes`, `attributes`, `studio_get_tags`, `tags`, `studio_list_services`, `list_services` | Parcial: necesita refs/objects/arrays reales |
| Create/modify | `studio_create_instance`, `create`, `studio_destroy_instance`, `destroy`, `studio_rename_instance`, `rename`, `studio_clone_instance`, `studio_reparent_instance`, `reparent` | Parcial: draft y tipos simples funcionan; refs/parents requieren selector de estado real |
| Properties/attributes/tags/batch | `studio_set_properties`, `set_properties`, `studio_set_attributes`, `set_attributes`, `studio_set_tags`, `set_tags`, `studio_add_tag`, `studio_remove_tag`, `studio_batch`, `batch` | Parcial: objects/arrays necesitan compositor más rico |
| Scripts | `studio_read_script`, `script_read`, `read`, `studio_create_script`, `script_create`, `create_script`, `studio_replace_script`, `script_replace`, `replace`, `studio_patch_script`, `script_patch`, `patch`, `studio_open_script`, `open`, `studio_list_open_scripts`, `list_open_scripts` | Lecturas sin args completas; escritura parcial por source/patch grande |
| Output/history | `studio_get_output`, `output`, `studio_clear_output_buffer`, `studio_undo`, `undo`, `studio_redo`, `redo`, `studio_can_undo`, `studio_can_redo` | Completa para defaults/lecturas; acciones de escritura requieren confirmación humana |
| Playtest | `studio_playtest`, `playtest` | Parcial: enum `action` se puede navegar, pero requiere validación del estado real |

## Cobertura medida tras el navigator genérico

- Total descubierto: 71.
- Protocol navigable: 71/71; todas tienen página de schema, draft y ruta de ejecución genérica.
- Practical for ChatGPT Web: 23/71 con argumentos vacíos o primitivos simples; 48/71 son parciales por refs, objects/arrays complejos o texto grande.
- Blocked: 0 por nombre. Una tool puede quedar `Not ready` si el usuario no proporciona un valor complejo válido.

La matriz es deliberadamente honesta: listar y describir una tool es dinámico para las 71; representar cómodamente cada schema complejo y seleccionar refs reales requiere ampliar el selector de Studio.
