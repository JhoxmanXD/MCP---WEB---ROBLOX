# Instance target contract

Source: live `tools/list` from the local MCP bridge after the selector audit.

The bridge exposes selector inputs as `anyOf: object|string`; the object is intentionally open because the plugin accepts either an issued id/ref or a structured path. A producer snapshot includes `ref` and a human display path. The bridge server now normalizes a full snapshot to its stable id and places the normalized selector at the command root.

| Tool | Required args | Selector argument | Exact selector schema | Accepted representations | Output instance representation | Reusable directly |
|---|---|---|---|---|---|---|
| `studio_create_instance` | `class_name` | `parent` optional | object|string|null | id/ref object, structured path object, string | `{path,name,className,ref,childCount}` | YES as parent/selector |
| `studio_find_instances` | `query` | none | — | — | array of `{path,name,className,ref,childCount}` | YES |
| `studio_get_instance` | `ref` | `ref` | object|string | id/ref object, structured path object, string | descriptor | YES |
| `studio_get_properties` | `ref` | `ref` | object|string | id/ref object, structured path object, string | `{instance: descriptor, properties}` | YES |
| `studio_set_properties` | `ref`, `values` | `ref` | object|string | id/ref object, structured path object, string | `{instance: descriptor, changed}` | YES |
| `studio_rename_instance` | `ref`, `name` | `ref` | object|string | id/ref object, structured path object, string | descriptor | YES |
| `studio_reparent_instance` | `ref`, `parent` | `ref`, `parent` | object|string | id/ref object, structured path object, string | descriptor | YES |
| `studio_clone_instance` | `ref` | `ref`, `parent` optional | object|string and object|string|null | id/ref object, structured path object, string | descriptor | YES |
| `studio_set_selection` | `instances` | `instances[]` | array of object|string | id/ref object, structured path object, string | `{count}` | YES |
| `studio_destroy_instance` | `ref` | `ref` | object|string | id/ref object, structured path object, string | descriptor | YES |
| `studio_read_script` | `ref` | `ref` | object|string | id/ref object, structured path object, string | descriptor/source/hash | YES |
| `studio_open_script` | `ref` | `ref` | object|string | id/ref object, structured path object, string | plugin result, usually descriptor | YES |

`studio_set_properties` receives `values` as an open object. `studio_get_properties` receives optional `names: string[] | null`. All listed tools also accept optional `session_id` and `timeout`.

Canonical gateway candidate:

```json
{
  "ref": "rbx:studio_...:i_...",
  "path": ["p", "Model", "Part"],
  "displayPath": "p.Model.Part",
  "name": "Part",
  "className": "Part",
  "session_id": "studio_..."
}
```

The gateway adapts this candidate from the destination schema rather than branching on the 71 tool names. The MCP server keeps compatibility with old id/path callers and preserves the textual result alongside `structuredContent`.

Result pages also register candidates from completed Agent Gateway executions, not only from discovery recipes. This makes `create → result → picker → modify` composable.

Selector normalization is deliberately flat at the command root:

```json
Incorrect: {"ref":{"id":"rbx:studio_...:i_..."}}
Correct:   {"id":"rbx:studio_...:i_..."}
```

The normal workflow is `create/find result → InstanceCandidate → picker → destination schema`. A candidate may be reused by its stable `id`/`ref` or structured `path`; the gateway converts the selected candidate into the exact schema required by the destination tool.
