from __future__ import annotations

import json
import asyncio
import time
from datetime import datetime, timezone
from html import escape
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

try:
    from .models import Job
except ImportError:  # Render runs `uvicorn app:app` from web/
    from models import Job


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def schema_type(schema: dict[str, Any]) -> str:
    if isinstance(schema.get("type"), str):
        return schema["type"]
    for option in schema.get("anyOf", []):
        if option.get("type") not in {"null", None}:
            return option.get("type", "value")
    return "value"


def nullable(schema: dict[str, Any]) -> bool:
    return any(option.get("type") == "null" for option in schema.get("anyOf", [])) or schema.get("type") == "null"


def href(path: str, label: str) -> str:
    return f"<a href='{escape(path)}'>{escape(label)}</a>"


def agent_page(title: str, body: str) -> HTMLResponse:
    response = HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title><style>body{{font-family:system-ui,sans-serif;max-width:1000px;margin:30px auto;padding:0 18px;color:#172033}}a{{color:#0759b5;margin-right:12px}}pre{{white-space:pre-wrap;background:#f2f5f9;padding:14px;border-radius:8px}}.card{{border:1px solid #d9e0ea;padding:14px;margin:10px 0;border-radius:8px}}.missing{{color:#9a2c00}}code{{background:#eef2f7;padding:2px 4px}}</style></head><body>{body}</body></html>")
    response.headers.update({"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"})
    return response


def register_agent_routes(app, store, current_studio_connected):
    DRAFT_TTL_SECONDS = 3600

    def purge_drafts() -> None:
        cutoff = time.time() - DRAFT_TTL_SECONDS
        for draft_id, draft in list(store.drafts.items()):
            try:
                if datetime.fromisoformat(draft.get("last_access", draft["created_at"])).timestamp() < cutoff:
                    del store.drafts[draft_id]
            except (KeyError, ValueError):
                del store.drafts[draft_id]

    def catalog_tools() -> dict[str, dict[str, Any]]:
        return {item.get("name"): item for item in store.catalog.tools if isinstance(item, dict) and item.get("name")}

    def tool_or_404(name: str) -> dict[str, Any]:
        tool = catalog_tools().get(name)
        if not tool:
            raise HTTPException(404, "Tool not found in current catalog")
        return tool

    def draft_or_404(draft_id: str) -> dict[str, Any]:
        purge_drafts()
        draft = store.drafts.get(draft_id)
        if not draft:
            raise HTTPException(404, "Draft not found or expired")
        return draft

    def required(schema: dict[str, Any]) -> list[str]:
        return list(schema.get("required", []))

    def missing(draft: dict[str, Any]) -> list[str]:
        return [name for name in required(draft["schema"]) if name not in draft["arguments"]]

    async def discover(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        if tool_name not in catalog_tools():
            return None
        request_id = "WEB_AGENT_DISCOVER_" + uuid4().hex[:12]
        store.create_job(Job(request_id=request_id, tool=tool_name, arguments=arguments))
        deadline = asyncio.get_running_loop().time() + 8
        while asyncio.get_running_loop().time() < deadline:
            job = store.jobs[request_id]
            if job.status in {"completed", "error"}:
                if job.status == "completed":
                    collect_refs(job.result)
                return job.model_dump(mode="json")
            await asyncio.sleep(0.25)
        return store.jobs[request_id].model_dump(mode="json")

    def collect_refs(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("ref") or value.get("path"):
                candidate = {key: value[key] for key in ("ref", "path", "name", "className", "childCount") if key in value}
                if candidate and candidate not in store.recent_refs:
                    store.recent_refs.append(candidate)
            for child in value.values(): collect_refs(child)
        elif isinstance(value, list):
            for child in value: collect_refs(child)

    def draft_links(draft: dict[str, Any], name: str, schema: dict[str, Any]) -> str:
        base = f"/agent/draft/{draft['draft_id']}/arg/{quote(name, safe='')}/set/"
        links = []
        if "enum" in schema:
            links += [href(base + quote(json.dumps(value), safe=''), f"Set {value}") for value in schema["enum"]]
        typ = schema_type(schema)
        if typ == "object":
            links.append(href(f"/agent/draft/{draft['draft_id']}/arg/{quote(name, safe='')}/object", "Edit object"))
            if name in {"values", "properties", "attributes"}:
                quick_object = quote(json.dumps({"Anchored": True}), safe="")
                links.append(href(base + quick_object, "Set Anchored=true"))
        elif typ == "array":
            links.append(href(f"/agent/draft/{draft['draft_id']}/arg/{quote(name, safe='')}/array", "Edit array"))
        elif typ == "boolean":
            links += [href(base + "true", "Set true"), href(base + "false", "Set false")]
        elif typ in {"integer", "number"}:
            values = [0, 1, -1, 10, 100, 0.5] if typ == "number" else [0, 1, -1, 10, 100]
            links += [href(base + quote(json.dumps(value), safe=''), str(value)) for value in values]
            links.append(href(f"/agent/draft/{draft['draft_id']}/arg/{quote(name, safe='')}/number", "Number composer"))
        elif typ == "string":
            values = [schema.get("default"), "Workspace", "Part", "Folder", "Model", "Script", "Name", "Parent"]
            links += [href(base + quote(json.dumps(value), safe=''), f"Set {value}") for value in values if value is not None]
            links += [href(f"/agent/draft/{draft['draft_id']}/arg/{quote(name, safe='')}/token/{quote(token, safe='')}", f"Append {token}") for token in ["a", "b", "0", "_", ".", "/"]]
        if name.lower() in {"ref", "parent", "target", "instance", "selection", "script", "object"} or typ == "object":
            links.append(href(f"/agent/draft/{draft['draft_id']}/arg/{quote(name, safe='')}/picker", "Choose Roblox Instance"))
        if nullable(schema):
            links.append(href(base + "null", "Set null"))
        links.append(href(f"/agent/draft/{draft['draft_id']}/arg/{quote(name, safe='')}/clear", "Clear"))
        return " ".join(links)

    @app.get("/agent", response_class=HTMLResponse)
    async def agent_home():
        purge_drafts()
        body = "<h1>ChatGPT Agent Gateway</h1><p>A server-rendered, schema-driven navigation layer over the live MCP catalog.</p><p>STATUS: {}</p><p>{}{}{}{}{}{}</p>".format(
            "READY" if current_studio_connected() else "STUDIO OFFLINE",
            href("/agent/status", "Status"), href("/agent/tools", "Tools"), href("/agent/jobs", "Jobs"), href("/agent/latest", "Latest"), href("/agent/recipes", "Recipes"), href("/agent/help", "Help"))
        return agent_page("ChatGPT Agent Gateway", body)

    @app.get("/agent/status", response_class=HTMLResponse)
    async def agent_status():
        heartbeat = store.heartbeat.model_dump(mode="json") if store.heartbeat else {}
        body = f"<h1>Agent Status</h1><p>local_client_online: {str(store.online()).lower()}</p><p>mcp_connected: {str(bool(heartbeat.get('mcp_connected') and store.online())).lower()}</p><p>studio_connected: {str(current_studio_connected()).lower()}</p><p>tool_count: {store.catalog.tool_count}</p><p>{href('/agent', 'Agent Home')} {href('/read/health', 'Live Health')}</p>"
        return agent_page("Agent Status", body)

    @app.get("/agent/tools", response_class=HTMLResponse)
    async def agent_tools():
        tools = catalog_tools()
        items = "".join(f"<li>{href('/agent/tool/' + quote(name, safe=''), name)} — {escape(str(tool.get('description', '')))}</li>" for name, tool in tools.items())
        body = f"<h1>Agent Tools</h1><p>tool_count: {len(tools)} · catalog_generation: {store.catalog.catalog_generation} · updated_at: {escape(store.catalog.updated_at)} · studio_connected: {str(current_studio_connected()).lower()}</p><ol>{items}</ol><p>{href('/agent', 'Agent Home')} {href('/read/catalog', 'Live Catalog')}</p>"
        return agent_page("Agent Tools", body)

    @app.get("/agent/tool/{tool_name}", response_class=HTMLResponse)
    async def agent_tool(tool_name: str):
        tool = tool_or_404(tool_name)
        schema = tool.get("inputSchema", {})
        body = f"<h1>{escape(tool_name)}</h1><p>{escape(str(tool.get('description', '')))}</p><p>required: {escape(', '.join(schema.get('required', [])) or 'none')}</p><pre>{escape(json.dumps(schema, ensure_ascii=False, indent=2))}</pre><p>{href('/agent/tool/' + quote(tool_name, safe='') + '/start', 'Start invocation')} {href('/agent/tools', 'Back to tools')}</p>"
        return agent_page(tool_name, body)

    @app.get("/agent/tool/{tool_name}/start")
    async def agent_start(tool_name: str):
        tool = tool_or_404(tool_name)
        draft_id = "d_" + uuid4().hex[:16]
        store.drafts[draft_id] = {"draft_id": draft_id, "tool_name": tool_name, "schema": tool.get("inputSchema", {}), "arguments": {}, "created_at": now(), "last_access": now(), "status": "draft", "executed": False, "request_id": None, "execution_token": None}
        return RedirectResponse(f"/agent/draft/{draft_id}", status_code=303)

    @app.get("/agent/draft/{draft_id}", response_class=HTMLResponse)
    async def agent_draft(draft_id: str):
        draft = draft_or_404(draft_id); draft["last_access"] = now(); miss = missing(draft)
        fields = []
        for name, schema in draft["schema"].get("properties", {}).items():
            value = draft["arguments"].get(name, "<missing>")
            fields.append(f"<div class='card'><strong>{escape(name)}</strong> ({escape(schema_type(schema))})<br>value: <code>{escape(str(value))}</code><br>{draft_links(draft, name, schema)}</div>")
        ready = not miss
        action = href(f"/agent/draft/{draft_id}/prepare", "Prepare Execution") if ready else "Complete required arguments first"
        body = f"<h1>Invocation Draft</h1><p>DRAFT_ID: <code>{escape(draft_id)}</code></p><p>TOOL: {escape(draft['tool_name'])}</p><p>READY: {str(ready).lower()}</p><p class='missing'>Missing required: {escape(', '.join(miss) or 'none')}</p>{''.join(fields)}<h2>Current arguments</h2><pre>{escape(json.dumps(draft['arguments'], ensure_ascii=False, indent=2))}</pre><p>{action} {href('/agent/tools', 'Tools')} {href('/agent', 'Agent Home')}</p>"
        return agent_page("Invocation Draft", body)

    @app.get("/agent/draft/{draft_id}/arg/{name}/set/{value}")
    async def set_arg(draft_id: str, name: str, value: str):
        draft = draft_or_404(draft_id)
        try: parsed = json.loads(value)
        except ValueError: parsed = value
        draft["arguments"][name] = parsed; draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}", status_code=303)

    @app.get("/agent/draft/{draft_id}/arg/{name}/token/{token}")
    async def append_token(draft_id: str, name: str, token: str):
        draft = draft_or_404(draft_id); draft["arguments"][name] = str(draft["arguments"].get(name, "")) + token; draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}", status_code=303)

    @app.get("/agent/draft/{draft_id}/arg/{name}/clear")
    async def clear_arg(draft_id: str, name: str):
        draft = draft_or_404(draft_id); draft["arguments"].pop(name, None); draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}", status_code=303)

    def set_nested(value: Any, path: list[str], new_value: Any) -> Any:
        if not path:
            return new_value
        head, *tail = path
        if isinstance(value, list):
            index = int(head)
            while len(value) <= index:
                value.append(None)
            value[index] = set_nested(value[index], tail, new_value)
            return value
        if not isinstance(value, dict):
            value = {}
        value[head] = set_nested(value.get(head), tail, new_value)
        return value

    @app.get("/agent/draft/{draft_id}/arg/{name}/object", response_class=HTMLResponse)
    async def edit_object(draft_id: str, name: str):
        draft = draft_or_404(draft_id); schema = draft["schema"].get("properties", {}).get(name, {})
        value = draft["arguments"].get(name, {})
        field_items = []
        for field, field_schema in schema.get("properties", {}).items():
            path_url = quote(json.dumps([name, field]), safe="")
            value_url = quote(json.dumps(field_schema.get("default", "")), safe="")
            field_items.append(f"<li><strong>{escape(field)}</strong> ({escape(schema_type(field_schema))}) — {href(f'/agent/draft/{draft_id}/field/{path_url}/set/{value_url}', 'Set default/value')}</li>")
        fields = "".join(field_items)
        return agent_page("Object Editor", f"<h1>Object: {escape(name)}</h1><pre>{escape(json.dumps(value, indent=2))}</pre><ul>{fields or '<li>No declared fields</li>'}</ul>{href('/agent/draft/' + draft_id, 'Back to draft')}")

    @app.get("/agent/draft/{draft_id}/arg/{name}/array", response_class=HTMLResponse)
    async def edit_array(draft_id: str, name: str):
        draft = draft_or_404(draft_id); values = draft["arguments"].get(name, [])
        encoded_name = quote(name, safe="")
        links = f"{href(f'/agent/draft/{draft_id}/array/{encoded_name}/add/0', 'Add 0')} {href(f'/agent/draft/{draft_id}/array/{encoded_name}/add/1', 'Add 1')} {href(f'/agent/draft/{draft_id}/array/{encoded_name}/remove-last', 'Remove last')}"
        return agent_page("Array Editor", f"<h1>Array: {escape(name)}</h1><pre>{escape(json.dumps(values, indent=2))}</pre><p>{links} {href('/agent/draft/' + draft_id, 'Finish array')}</p>")

    @app.get("/agent/draft/{draft_id}/field/{path}/set/{value}")
    async def set_field(draft_id: str, path: str, value: str):
        draft = draft_or_404(draft_id); keys = json.loads(path); raw = json.loads(value)
        draft["arguments"] = set_nested(draft["arguments"], keys, raw); draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}", status_code=303)

    @app.get("/agent/draft/{draft_id}/array/{name}/add/{value}")
    async def array_add(draft_id: str, name: str, value: str):
        draft = draft_or_404(draft_id); values = draft["arguments"].setdefault(name, [])
        if not isinstance(values, list): values = []; draft["arguments"][name] = values
        try: values.append(json.loads(value))
        except ValueError: values.append(value)
        draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}/arg/{quote(name, safe='')}/array", status_code=303)

    @app.get("/agent/draft/{draft_id}/array/{name}/remove-last")
    async def array_remove_last(draft_id: str, name: str):
        draft = draft_or_404(draft_id); values = draft["arguments"].get(name, [])
        if isinstance(values, list) and values: values.pop()
        draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}/arg/{quote(name, safe='')}/array", status_code=303)

    @app.get("/agent/draft/{draft_id}/arg/{name}/number", response_class=HTMLResponse)
    async def number_editor(draft_id: str, name: str):
        draft = draft_or_404(draft_id); current = str(draft["arguments"].get(name, ""))
        actions = "".join(href(f"/agent/draft/{draft_id}/arg/{quote(name, safe='')}/number/add/{quote(token, safe='')}", token) for token in ["0","1","2","3","4","5","6","7","8","9","-","."]) + " " + href(f"/agent/draft/{draft_id}/arg/{quote(name, safe='')}/number/backspace", "Backspace") + " " + href(f"/agent/draft/{draft_id}/arg/{quote(name, safe='')}/number/clear", "Clear") + " " + href(f"/agent/draft/{draft_id}/arg/{quote(name, safe='')}/number/finish", "Finish")
        return agent_page("Number Composer", f"<h1>Number Composer</h1><p>Current value: <code>{escape(current)}</code></p><p>{actions}</p>{href('/agent/draft/' + draft_id, 'Back to draft')}")

    @app.get("/agent/draft/{draft_id}/arg/{name}/number/add/{token}")
    async def number_add(draft_id: str, name: str, token: str):
        draft = draft_or_404(draft_id); current = str(draft["arguments"].get(name, ""))
        if token != "." or "." not in current: current += token
        draft["arguments"][name] = current; draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}/arg/{quote(name, safe='')}/number", status_code=303)

    @app.get("/agent/draft/{draft_id}/arg/{name}/number/backspace")
    async def number_backspace(draft_id: str, name: str):
        draft = draft_or_404(draft_id); draft["arguments"][name] = str(draft["arguments"].get(name, ""))[:-1]; draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}/arg/{quote(name, safe='')}/number", status_code=303)

    @app.get("/agent/draft/{draft_id}/arg/{name}/number/clear")
    async def number_clear(draft_id: str, name: str):
        draft = draft_or_404(draft_id); draft["arguments"].pop(name, None); draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}/arg/{quote(name, safe='')}/number", status_code=303)

    @app.get("/agent/draft/{draft_id}/arg/{name}/number/finish")
    async def number_finish(draft_id: str, name: str):
        draft = draft_or_404(draft_id); raw = str(draft["arguments"].get(name, ""))
        try: draft["arguments"][name] = float(raw) if "." in raw else int(raw)
        except ValueError: draft["arguments"].pop(name, None)
        draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}", status_code=303)

    @app.get("/agent/draft/{draft_id}/arg/{name}/picker", response_class=HTMLResponse)
    async def instance_picker(draft_id: str, name: str):
        draft = draft_or_404(draft_id)
        for candidate in store.recent_refs:
            candidate.setdefault("source", "recent")
        encoded_name = quote(name, safe="")
        candidate_links = []
        for candidate in store.recent_refs[-50:]:
            candidate_url = quote(json.dumps(candidate), safe="")
            label = str(candidate.get("name", candidate.get("path", candidate.get("ref", "candidate"))))
            candidate_links.append(f"<li>{href(f'/agent/draft/{draft_id}/arg/{encoded_name}/pick/{candidate_url}', label)} — {escape(str(candidate.get('className', '')))} — {escape(str(candidate.get('path', candidate.get('ref', ''))))}</li>")
        links = "".join(candidate_links)
        return agent_page("Roblox Instance Picker", f"<h1>Roblox Instance Picker</h1><p>Recent references:</p><ul>{links or '<li>No recent references yet. Run a read recipe first.</li>'}</ul>{href('/agent/recipes', 'Discover Studio State')} {href('/agent/draft/' + draft_id, 'Back to draft')}")

    @app.get("/agent/draft/{draft_id}/arg/{name}/pick/{candidate}")
    async def pick_instance(draft_id: str, name: str, candidate: str):
        draft = draft_or_404(draft_id); draft["arguments"][name] = json.loads(candidate); draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}", status_code=303)

    @app.get("/agent/draft/{draft_id}/prepare", response_class=HTMLResponse)
    async def prepare(draft_id: str):
        draft = draft_or_404(draft_id)
        if missing(draft): return agent_page("Draft Not Ready", f"<h1>Not ready</h1><p>Missing: {escape(', '.join(missing(draft)))}</p>{href('/agent/draft/' + draft_id, 'Back to draft')}")
        if draft.get("executed"): return RedirectResponse(f"/agent/result/{draft['request_id']}", status_code=303)
        draft["execution_token"] = uuid4().hex; draft["last_access"] = now()
        body = f"<h1>READY TO EXECUTE</h1><p>Tool: {escape(draft['tool_name'])}</p><pre>{escape(json.dumps(draft['arguments'], ensure_ascii=False, indent=2))}</pre><p>{href('/agent/execute/' + draft_id + '/' + draft['execution_token'], 'Execute now')} {href('/agent/draft/' + draft_id, 'Back')}</p>"
        return agent_page("Prepare Execution", body)

    @app.get("/agent/execute/{draft_id}/{token}")
    async def execute(draft_id: str, token: str):
        draft = draft_or_404(draft_id)
        if draft.get("executed"): return RedirectResponse(f"/agent/result/{draft['request_id']}", status_code=303)
        if token != draft.get("execution_token") or missing(draft): raise HTTPException(400, "Invalid or incomplete execution")
        request_id = "WEB_AGENT_" + uuid4().hex[:16]
        draft.update({"executed": True, "status": "executed", "request_id": request_id, "execution_token": None, "last_access": now()})
        store.create_job(Job(request_id=request_id, tool=draft["tool_name"], arguments=draft["arguments"]))
        return RedirectResponse(f"/agent/result/{request_id}", status_code=303)

    @app.get("/agent/result/{request_id}", response_class=HTMLResponse)
    async def agent_result(request_id: str):
        job = store.jobs.get(request_id)
        if not job: return agent_page("Result Not Found", f"<h1>Result not found</h1><p>{escape(request_id)}</p>{href('/agent', 'Agent Home')}")
        refresh = href(f"/agent/result/{request_id}", "Refresh Result") if job.status in {"pending", "running"} else ""
        body = f"<h1>Agent Result</h1><p>REQUEST_ID: <code>{escape(request_id)}</code></p><p>TOOL: {escape(job.tool)}</p><p>STATUS: {escape(job.status)}</p><p>created_at: {escape(job.created_at)}<br>started_at: {escape(str(job.started_at))}<br>completed_at: {escape(str(job.completed_at))}</p><pre>{escape(json.dumps(job.result if job.status == 'completed' else job.error, ensure_ascii=False, indent=2, default=str))}</pre><p>{refresh} {href('/agent', 'Agent Home')} {href('/agent/latest', 'Latest')}</p>"
        return agent_page("Agent Result", body)

    @app.get("/agent/jobs", response_class=HTMLResponse)
    async def agent_jobs():
        items = "".join(f"<li>{href('/agent/result/' + job.request_id, job.request_id)} — {escape(job.tool)} — {escape(job.status)}</li>" for job in list(store.jobs.values())[-30:][::-1]) or "<li>No jobs</li>"
        return agent_page("Agent Jobs", f"<h1>Agent Jobs</h1><ul>{items}</ul>{href('/agent', 'Agent Home')}")

    @app.get("/agent/latest", response_class=HTMLResponse)
    async def agent_latest():
        return RedirectResponse("/read/latest", status_code=307)

    @app.get("/agent/recipes", response_class=HTMLResponse)
    async def agent_recipes():
        return agent_page("Agent Recipes", f"<h1>Recipes</h1><p>Recipes use the generic draft flow. Discovery reads populate the Roblox Instance Picker.</p><ul><li>{href('/agent/tool/studio_list_sessions/start', 'Read Studio Sessions')}</li><li>{href('/agent/discover/tree', 'Inspect Studio Tree and discover refs')}</li><li>{href('/agent/discover/selection', 'Inspect Selection and discover refs')}</li><li>{href('/agent/tool/studio_get_place_info/start', 'Read Place Info')}</li><li>{href('/agent/tool/studio_get_output/start', 'Read Output')}</li></ul>{href('/agent', 'Agent Home')}")

    @app.get("/agent/discover/tree", response_class=HTMLResponse)
    async def discover_tree():
        result = await discover("studio_get_tree", {"root": "game", "depth": 2})
        return agent_page("Studio Tree Discovery", f"<h1>Studio Tree Discovery</h1><p>Recent refs: {len(store.recent_refs)}</p><pre>{escape(json.dumps(result, ensure_ascii=False, indent=2, default=str))}</pre>{href('/agent/recipes', 'Recipes')} {href('/agent', 'Agent Home')}")

    @app.get("/agent/discover/selection", response_class=HTMLResponse)
    async def discover_selection():
        result = await discover("studio_get_selection", {})
        return agent_page("Studio Selection Discovery", f"<h1>Studio Selection Discovery</h1><p>Recent refs: {len(store.recent_refs)}</p><pre>{escape(json.dumps(result, ensure_ascii=False, indent=2, default=str))}</pre>{href('/agent/recipes', 'Recipes')} {href('/agent', 'Agent Home')}")

    @app.get("/agent/help", response_class=HTMLResponse)
    async def agent_help():
        return agent_page("Agent Help", f"<h1>Agent Gateway Help</h1><p>Start at Agent Tools, open a real tool, create a draft, set arguments with links, prepare, then execute once.</p><p>Large free-text values are intentionally limited in link mode; use the existing raw API for large scripts.</p>{href('/agent', 'Agent Home')} {href('/read/health', 'Health')}")
