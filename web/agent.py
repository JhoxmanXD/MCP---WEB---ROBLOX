from __future__ import annotations

import json
import asyncio
import copy
import hashlib
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


STRING_CHARACTERS = (
    [(f"upper_{char}", char) for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    + [(f"lower_{char}", char) for char in "abcdefghijklmnopqrstuvwxyz"]
    + [(f"digit_{char}", char) for char in "0123456789"]
    + [("underscore", "_"), ("hyphen", "-"), ("dot", "."), ("slash", "/"), ("space", " ")]
    + [(f"symbol_{index}", char) for index, char in enumerate(":()[]{},=+")]
)
STRING_CHARACTER_BY_TOKEN = dict(STRING_CHARACTERS)


def agent_page(title: str, body: str) -> HTMLResponse:
    response = HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title><style>body{{font-family:system-ui,sans-serif;max-width:1000px;margin:30px auto;padding:0 18px;color:#172033}}a{{color:#0759b5;margin-right:12px}}pre{{white-space:pre-wrap;background:#f2f5f9;padding:14px;border-radius:8px}}.card{{border:1px solid #d9e0ea;padding:14px;margin:10px 0;border-radius:8px}}.missing{{color:#9a2c00}}code{{background:#eef2f7;padding:2px 4px}}</style></head><body>{body}</body></html>")
    response.headers.update({"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0", "CDN-Cache-Control": "no-store", "Surrogate-Control": "no-store"})
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

    def bump(draft: dict[str, Any]) -> None:
        draft["revision"] = int(draft.get("revision", 0)) + 1
        draft["last_access"] = now()

    def make_view(draft: dict[str, Any]) -> str:
        view_id = "V_" + uuid4().hex[:18]
        arguments = copy.deepcopy(draft.get("arguments", {}))
        snapshot = {
            "view_id": view_id,
            "draft_id": draft["draft_id"],
            "revision": int(draft.get("revision", 0)),
            "created_at": now(),
            "arguments_snapshot": arguments,
            "missing_arguments": [name for name in required(draft["schema"]) if name not in arguments],
            "ready": not any(name for name in required(draft["schema"]) if name not in arguments),
            "tool_name": draft["tool_name"],
            "schema": copy.deepcopy(draft["schema"]),
            "action_ids": {},
        }
        store.views[view_id] = snapshot
        return view_id

    def make_action(draft_id: str, expected_revision: int, operation: str, payload: dict[str, Any]) -> str:
        action_id = "A_" + uuid4().hex[:18]
        store.actions[action_id] = {
            "action_id": action_id,
            "draft_id": draft_id,
            "expected_revision": expected_revision,
            "operation": operation,
            "payload": copy.deepcopy(payload),
            "created_at": now(),
            "consumed": False,
            "resulting_view_id": None,
        }
        return f"/agent/action/{action_id}"

    def action_link(draft_id: str, revision: int, operation: str, payload: dict[str, Any], label: str) -> str:
        return href(make_action(draft_id, revision, operation, payload), label)

    def view_action_link(view: dict[str, Any], operation: str, payload: dict[str, Any], label: str) -> str:
        key = json.dumps([operation, payload], ensure_ascii=False, sort_keys=True, default=str)
        action_id = view.setdefault("action_ids", {}).get(key)
        if not action_id:
            action_id = "A_" + uuid4().hex[:18]
            store.actions[action_id] = {"action_id": action_id, "draft_id": view["draft_id"], "expected_revision": view["revision"], "operation": operation, "payload": copy.deepcopy(payload), "created_at": now(), "consumed": False, "resulting_url": None}
            view["action_ids"][key] = action_id
        return href(f"/agent/action/{action_id}", label)

    def stale_page(action: dict[str, Any], draft: dict[str, Any]) -> HTMLResponse:
        current = make_view(draft)
        body = f"<h1>STALE DRAFT VIEW</h1><p>Expected revision: {action['expected_revision']}</p><p>Current revision: {draft.get('revision', 0)}</p><p>This action belongs to an older draft view and was NOT applied.</p><p>{href('/agent/view/' + current, 'Open Current Draft')}</p>"
        return agent_page("Stale Draft View", body)

    def snapshot_links(view: dict[str, Any], name: str, schema: dict[str, Any]) -> str:
        draft_id = view["draft_id"]; revision = view["revision"]; encoded = quote(name, safe="")
        links: list[str] = []
        base = {"name": name}
        if "enum" in schema:
            links += [view_action_link(view, "set_arg", {**base, "value": value}, f"Set {value}") for value in schema["enum"]]
        typ = schema_type(schema)
        if typ == "string":
            values = [schema.get("default"), "Workspace", "Part", "Folder", "Model", "Script", "Name", "Parent"] + list(reversed(store.recent_string_values))
            links += [view_action_link(view, "set_arg", {**base, "value": value}, f"Set {value}") for value in values if value is not None]
            links.append(view_action_link(view, "open_string", {"name": name, "view_id": view["view_id"]}, f"Open String Composer ({name})"))
        elif typ == "boolean":
            links += [view_action_link(view, "set_arg", {**base, "value": value}, f"Set {str(value).lower()}") for value in (True, False)]
        elif typ in {"integer", "number"}:
            values = [0, 1, -1, 10, 100, 0.5] if typ == "number" else [0, 1, -1, 10, 100]
            links += [view_action_link(view, "set_arg", {**base, "value": value}, str(value)) for value in values]
        elif typ == "object" and name in {"values", "properties", "attributes"}:
            links.append(view_action_link(view, "set_nested", {"name": name, "path": ["Anchored"], "value": True}, "Set Anchored=true"))
        elif typ == "array" and name == "names":
            links.append(view_action_link(view, "set_arg", {"name": name, "value": ["Anchored"]}, "Read Anchored"))
        if name.lower() in {"ref", "parent", "target", "instance", "selection", "script", "object"} or typ == "object":
            links.append(view_action_link(view, "open_picker", {"name": name, "view_id": view["view_id"]}, "Choose Roblox Instance"))
        if nullable(schema):
            links.append(view_action_link(view, "set_arg", {**base, "value": None}, "Set null"))
        links.append(view_action_link(view, "clear", base, "Clear"))
        return " ".join(links)

    def remember_string(value: str) -> None:
        if value and value in store.recent_string_values:
            store.recent_string_values.remove(value)
        if value:
            store.recent_string_values.append(value)
            del store.recent_string_values[:-12]

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

    def candidate_from(value: dict[str, Any]) -> dict[str, Any] | None:
        if not (value.get("ref") or value.get("path")):
            return None
        candidate = {key: value[key] for key in ("ref", "name", "className", "childCount", "session_id") if key in value}
        raw_path = value.get("path")
        if isinstance(raw_path, list) and all(isinstance(part, str) and part for part in raw_path):
            candidate["path"] = raw_path
            candidate["displayPath"] = ".".join(raw_path)
        elif isinstance(raw_path, str) and raw_path:
            candidate["displayPath"] = raw_path
            candidate["path"] = [part for part in raw_path.split(".") if part]
        return candidate or None

    def collect_refs(value: Any) -> None:
        if isinstance(value, dict):
            candidate = candidate_from(value)
            if candidate:
                if candidate and candidate not in store.recent_refs:
                    store.recent_refs.append(candidate)
                    del store.recent_refs[:-100]
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
            if name == "names":
                links.append(href(base + quote(json.dumps(["Anchored"]), safe=''), "Read Anchored"))
        elif typ == "boolean":
            links += [href(base + "true", "Set true"), href(base + "false", "Set false")]
        elif typ in {"integer", "number"}:
            values = [0, 1, -1, 10, 100, 0.5] if typ == "number" else [0, 1, -1, 10, 100]
            links += [href(base + quote(json.dumps(value), safe=''), str(value)) for value in values]
            links.append(href(f"/agent/draft/{draft['draft_id']}/arg/{quote(name, safe='')}/number", "Number composer"))
        elif typ == "string":
            values = [schema.get("default"), "Workspace", "Part", "Folder", "Model", "Script", "Name", "Parent"]
            values += list(reversed(store.recent_string_values))
            links += [href(base + quote(json.dumps(value), safe=''), f"Set {value}") for value in values if value is not None]
            links.append(href(f"/agent/draft/{draft['draft_id']}/string/{quote(name, safe='')}", f"Open String Composer ({name})"))
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
        store.drafts[draft_id] = {"draft_id": draft_id, "revision": 0, "tool_name": tool_name, "schema": tool.get("inputSchema", {}), "arguments": {}, "created_at": now(), "last_access": now(), "status": "draft", "executed": False, "request_id": None, "execution_token": None}
        view_id = make_view(store.drafts[draft_id])
        return RedirectResponse(f"/agent/view/{view_id}", status_code=303)

    @app.get("/agent/view/{view_id}", response_class=HTMLResponse)
    async def agent_view(view_id: str):
        view = store.views.get(view_id)
        if not view:
            raise HTTPException(404, "View not found or expired")
        draft = draft_or_404(view["draft_id"])
        fields = []
        for name, schema in view["schema"].get("properties", {}).items():
            value = view["arguments_snapshot"].get(name, "<missing>")
            fields.append(f"<div class='card'><strong>{escape(name)}</strong> ({escape(schema_type(schema))})<br>value: <code>{escape(str(value))}</code><br>{snapshot_links(view, name, schema)}</div>")
        prepare = action_link(view["draft_id"], view["revision"], "prepare", {}, "Prepare Execution") if view["ready"] else "Complete required arguments first"
        body = f"<h1>Invocation View</h1><p>VIEW_ID: <code>{escape(view_id)}</code></p><p>DRAFT_ID: <code>{escape(view['draft_id'])}</code></p><p>DRAFT_REVISION: {view['revision']}</p><p>STATE: {escape('ready' if view['ready'] else 'draft')}</p><p>TOOL: {escape(view['tool_name'])}</p><p class='missing'>Missing required: {escape(', '.join(view['missing_arguments']) or 'none')}</p>{''.join(fields)}<h2>Arguments snapshot</h2><pre>{escape(json.dumps(view['arguments_snapshot'], ensure_ascii=False, indent=2))}</pre><p>{prepare} {href('/agent/tools', 'Tools')} {href('/agent', 'Agent Home')}</p>"
        return agent_page("Invocation View", body)

    @app.get("/agent/action/{action_id}", response_class=HTMLResponse)
    async def agent_action(action_id: str):
        action = store.actions.get(action_id)
        if not action:
            raise HTTPException(404, "Action not found or expired")
        if action.get("consumed"):
            target = action.get("resulting_url")
            return RedirectResponse(target, status_code=303) if target else agent_page("Action complete", "<h1>Action already consumed</h1>")
        draft = draft_or_404(action["draft_id"])
        if action["operation"] not in {"execute_prepared", "refresh_result"} and int(action["expected_revision"]) != int(draft.get("revision", 0)):
            return stale_page(action, draft)
        operation = action["operation"]; payload = action["payload"]
        if operation == "open_string":
            view_id = payload.get("view_id") or make_view(draft)
            target = f"/agent/string-view/{view_id}/{quote(payload['name'], safe='')}"
        elif operation == "open_picker":
            view_id = payload.get("view_id") or make_view(draft)
            target = f"/agent/picker-view/{view_id}/{quote(payload['name'], safe='')}"
        elif operation == "prepare":
            arguments = copy.deepcopy(draft["arguments"])
            canonical = json.dumps({"tool_name": draft["tool_name"], "arguments": arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            prepare_id = "P_" + uuid4().hex[:18]
            store.prepared[prepare_id] = {"prepare_id": prepare_id, "draft_id": draft["draft_id"], "draft_revision": draft["revision"], "tool_name": draft["tool_name"], "arguments_snapshot": arguments, "arguments_hash": hashlib.sha256(canonical.encode()).hexdigest(), "created_at": now(), "executed": False, "request_id": None, "execute_action_id": None}
            target = f"/agent/prepared/{prepare_id}"
        elif operation == "execute_prepared":
            prepared = store.prepared.get(payload["prepare_id"])
            if not prepared:
                raise HTTPException(404, "Prepared invocation not found")
            if prepared.get("executed"):
                target = f"/agent/result-view/{prepared['result_view_id']}"
            else:
                request_id = "WEB_AGENT_" + uuid4().hex[:16]
                store.create_job(Job(request_id=request_id, tool=prepared["tool_name"], arguments=copy.deepcopy(prepared["arguments_snapshot"])))
                prepared.update({"executed": True, "request_id": request_id})
                result_view_id = "R_" + uuid4().hex[:18]
                prepared["result_view_id"] = result_view_id
                store.result_views[result_view_id] = {"result_view_id": result_view_id, "request_id": request_id, "draft_id": prepared["draft_id"], "draft_revision": prepared["draft_revision"], "status": "pending", "result": None, "error": None, "created_at": now()}
                target = f"/agent/result-view/{result_view_id}"
        elif operation == "refresh_result":
            old = store.result_views.get(payload["result_view_id"])
            if not old:
                raise HTTPException(404, "Result view not found")
            job = store.jobs.get(old["request_id"])
            status = job.status if job else old["status"]
            result_view_id = "R_" + uuid4().hex[:18]
            store.result_views[result_view_id] = {"result_view_id": result_view_id, "request_id": old["request_id"], "draft_id": old["draft_id"], "draft_revision": old["draft_revision"], "status": status, "result": copy.deepcopy(job.result) if job and status == "completed" else None, "error": copy.deepcopy(job.error) if job and status == "error" else None, "created_at": now()}
            target = f"/agent/result-view/{result_view_id}"
        else:
            if operation == "set_arg":
                draft["arguments"][payload["name"]] = copy.deepcopy(payload.get("value"))
            elif operation == "append_string":
                draft["arguments"][payload["name"]] = str(draft["arguments"].get(payload["name"], "")) + payload["character"]
            elif operation == "backspace":
                draft["arguments"][payload["name"]] = str(draft["arguments"].get(payload["name"], ""))[:-1]
            elif operation == "clear":
                draft["arguments"].pop(payload["name"], None)
            elif operation == "set_nested":
                draft["arguments"].setdefault(payload["name"], {})[payload["path"][-1]] = copy.deepcopy(payload["value"])
            elif operation == "select_instance":
                draft["arguments"][payload["name"]] = copy.deepcopy(payload["candidate"])
            elif operation == "finish_string":
                remember_string(str(draft["arguments"].get(payload["name"], "")))
            else:
                raise HTTPException(400, "Unknown action")
            bump(draft)
            next_view = make_view(draft)
            if operation in {"append_string", "backspace", "clear"} and schema_type(draft["schema"].get("properties", {}).get(payload.get("name"), {})) == "string":
                target = f"/agent/string-view/{next_view}/{quote(payload['name'], safe='')}"
            else:
                target = f"/agent/view/{next_view}"
        action["consumed"] = True; action["resulting_url"] = target
        return RedirectResponse(target, status_code=303)

    @app.get("/agent/string-view/{view_id}/{name}", response_class=HTMLResponse)
    async def string_view(view_id: str, name: str):
        view = store.views.get(view_id)
        if not view or name not in view["schema"].get("properties", {}):
            raise HTTPException(404, "String view not found")
        current = str(view["arguments_snapshot"].get(name, "")); base = f"/agent/string-view/{view_id}/{quote(name, safe='')}"
        links = " ".join(view_action_link(view, "append_string", {"name": name, "character": char}, f"Append {char if char != ' ' else 'space'}") for token, char in STRING_CHARACTERS)
        actions = " ".join([view_action_link(view, "backspace", {"name": name}, "Backspace"), view_action_link(view, "clear", {"name": name}, "Clear"), view_action_link(view, "finish_string", {"name": name}, "Finish"), href(f"/agent/view/{view_id}", "Back to Draft")])
        body = f"<h1>String Composer</h1><p>VIEW_ID: <code>{escape(view_id)}</code></p><p>DRAFT_ID: <code>{escape(view['draft_id'])}</code></p><p>DRAFT_REVISION: {view['revision']}</p><p>ARGUMENT: <code>{escape(name)}</code></p><p>CURRENT VALUE: <code>{escape(current)}</code></p><p>{links}</p><p>{actions}</p>"
        return agent_page("String Composer", body)

    @app.get("/agent/picker-view/{view_id}/{name}", response_class=HTMLResponse)
    async def picker_view(view_id: str, name: str):
        view = store.views.get(view_id)
        if not view:
            raise HTTPException(404, "Picker view not found")
        items = []
        for candidate in store.recent_refs[-50:]:
            candidate_url = view_action_link(view, "select_instance", {"name": name, "candidate": candidate}, "__candidate__")
            label = str(candidate.get("name", candidate.get("ref", "candidate")))
            if candidate.get("className"): label += f" ({candidate['className']})"
            items.append(f"<li>{candidate_url.replace('>__candidate__</a>', '>' + escape(label) + '</a>')} — {escape(str(candidate.get('displayPath', candidate.get('ref', ''))))}</li>")
        body = f"<h1>Roblox Instance Picker</h1><p>VIEW_ID: <code>{escape(view_id)}</code></p><p>DRAFT_ID: <code>{escape(view['draft_id'])}</code></p><p>DRAFT_REVISION: {view['revision']}</p><ul>{''.join(items) or '<li>No recent references yet. Run a read recipe first.</li>'}</ul>{href('/agent/view/' + view_id, 'Back to Draft')}"
        return agent_page("Roblox Instance Picker", body)

    @app.get("/agent/prepared/{prepare_id}", response_class=HTMLResponse)
    async def prepared_view(prepare_id: str):
        prepared = store.prepared.get(prepare_id)
        if not prepared:
            raise HTTPException(404, "Prepared invocation not found or expired")
        if not prepared.get("execute_action_id"):
            action_id = "A_" + uuid4().hex[:18]
            store.actions[action_id] = {"action_id": action_id, "draft_id": prepared["draft_id"], "expected_revision": prepared["draft_revision"], "operation": "execute_prepared", "payload": {"prepare_id": prepare_id}, "created_at": now(), "consumed": False, "resulting_url": None}
            prepared["execute_action_id"] = action_id
        execute_href = href(f"/agent/action/{prepared['execute_action_id']}", "Execute now") if not prepared.get("executed") else href(f"/agent/result-view/{prepared['result_view_id']}", "Open Result")
        body = f"<h1>Prepared Invocation</h1><p>PREPARE_ID: <code>{escape(prepare_id)}</code></p><p>DRAFT_ID: <code>{escape(prepared['draft_id'])}</code></p><p>DRAFT_REVISION: {prepared['draft_revision']}</p><p>STATE: {escape('executed' if prepared.get('executed') else 'prepared')}</p><p>TOOL: {escape(prepared['tool_name'])}</p><p>ARGUMENTS_SHA256: <code>{prepared['arguments_hash']}</code></p><pre>{escape(json.dumps(prepared['arguments_snapshot'], ensure_ascii=False, indent=2))}</pre><p>{execute_href} {href('/agent', 'Agent Home')}</p>"
        return agent_page("Prepared Invocation", body)

    @app.get("/agent/result-view/{result_view_id}", response_class=HTMLResponse)
    async def result_view(result_view_id: str):
        snapshot = store.result_views.get(result_view_id)
        if not snapshot:
            raise HTTPException(404, "Result view not found or expired")
        if snapshot["status"] in {"pending", "running"}:
            action_id = snapshot.get("refresh_action_id")
            if not action_id:
                action_id = "A_" + uuid4().hex[:18]
                store.actions[action_id] = {"action_id": action_id, "draft_id": snapshot["draft_id"], "expected_revision": snapshot["draft_revision"], "operation": "refresh_result", "payload": {"result_view_id": result_view_id}, "created_at": now(), "consumed": False, "resulting_url": None}
                snapshot["refresh_action_id"] = action_id
            refresh = href(f"/agent/action/{action_id}", "Refresh Result")
        else:
            refresh = ""
            collect_refs(snapshot.get("result"))
        body = f"<h1>Agent Result View</h1><p>RESULT_VIEW_ID: <code>{escape(result_view_id)}</code></p><p>REQUEST_ID: <code>{escape(snapshot['request_id'])}</code></p><p>STATUS: {escape(snapshot['status'])}</p><p>VIEW_ID: <code>{escape(result_view_id)}</code></p><p>DRAFT_ID: <code>{escape(snapshot['draft_id'])}</code></p><p>DRAFT_REVISION: {snapshot['draft_revision']}</p><pre>{escape(json.dumps(snapshot.get('result') if snapshot['status'] == 'completed' else snapshot.get('error'), ensure_ascii=False, indent=2, default=str))}</pre><p>{refresh} {href('/agent', 'Agent Home')}</p>"
        return agent_page("Agent Result View", body)

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

    @app.get("/agent/draft/{draft_id}/string/{name}", response_class=HTMLResponse)
    async def string_composer(draft_id: str, name: str):
        draft = draft_or_404(draft_id)
        schema = draft["schema"].get("properties", {}).get(name, {})
        if schema_type(schema) != "string":
            raise HTTPException(400, "Argument is not a string")
        current = str(draft["arguments"].get(name, ""))
        encoded_name = quote(name, safe="")
        base = f"/agent/draft/{draft_id}/string/{encoded_name}"
        character_links = " ".join(
            href(f"{base}/append/{token}", f"Append {escape(char) if char != ' ' else 'space'}")
            for token, char in STRING_CHARACTERS
        )
        actions = " ".join([
            href(f"{base}/backspace", "Backspace"),
            href(f"{base}/clear", "Clear"),
            href(f"{base}/finish", "Finish"),
            href(f"/agent/draft/{draft_id}", "Back to Draft"),
        ])
        body = f"<h1>String Composer</h1><p>DRAFT_ID: <code>{escape(draft_id)}</code></p><p>ARGUMENT: <code>{escape(name)}</code></p><p>CURRENT VALUE: <code>{escape(current)}</code></p><h2>Characters</h2><p>{character_links}</p><h2>Actions</h2><p>{actions}</p>"
        return agent_page("String Composer", body)

    @app.get("/agent/draft/{draft_id}/string/{name}/append/{token}")
    async def string_append(draft_id: str, name: str, token: str):
        draft = draft_or_404(draft_id)
        character = STRING_CHARACTER_BY_TOKEN.get(token)
        if character is None:
            raise HTTPException(404, "Unknown string composer character")
        draft["arguments"][name] = str(draft["arguments"].get(name, "")) + character
        draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}/string/{quote(name, safe='')}", status_code=303)

    @app.get("/agent/draft/{draft_id}/string/{name}/backspace")
    async def string_backspace(draft_id: str, name: str):
        draft = draft_or_404(draft_id)
        draft["arguments"][name] = str(draft["arguments"].get(name, ""))[:-1]
        draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}/string/{quote(name, safe='')}", status_code=303)

    @app.get("/agent/draft/{draft_id}/string/{name}/clear")
    async def string_clear(draft_id: str, name: str):
        draft = draft_or_404(draft_id)
        draft["arguments"].pop(name, None)
        draft["last_access"] = now()
        return RedirectResponse(f"/agent/draft/{draft_id}/string/{quote(name, safe='')}", status_code=303)

    @app.get("/agent/draft/{draft_id}/string/{name}/finish")
    async def string_finish(draft_id: str, name: str):
        draft = draft_or_404(draft_id)
        value = str(draft["arguments"].get(name, ""))
        draft["arguments"][name] = value
        remember_string(value)
        draft["last_access"] = now()
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
            if candidate.get("className"):
                label = f"{label} ({candidate['className']})"
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
        if job.status == "completed":
            collect_refs(job.result)
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
        result = await discover("studio_get_tree", {"root": "p", "depth": 2})
        return agent_page("Studio Tree Discovery", f"<h1>Studio Tree Discovery</h1><p>Recent refs: {len(store.recent_refs)}</p><pre>{escape(json.dumps(result, ensure_ascii=False, indent=2, default=str))}</pre>{href('/agent/recipes', 'Recipes')} {href('/agent', 'Agent Home')}")

    @app.get("/agent/discover/selection", response_class=HTMLResponse)
    async def discover_selection():
        result = await discover("studio_get_selection", {})
        return agent_page("Studio Selection Discovery", f"<h1>Studio Selection Discovery</h1><p>Recent refs: {len(store.recent_refs)}</p><pre>{escape(json.dumps(result, ensure_ascii=False, indent=2, default=str))}</pre>{href('/agent/recipes', 'Recipes')} {href('/agent', 'Agent Home')}")

    @app.get("/agent/help", response_class=HTMLResponse)
    async def agent_help():
        return agent_page("Agent Help", f"<h1>Agent Gateway Help</h1><p>Start at Agent Tools, open a real tool, create a draft, set arguments with links, prepare, then execute once.</p><p>Short free-text is supported through String Composer. Large free-text values and long Luau remain conditional; link navigation is not intended for hundreds of lines.</p>{href('/agent', 'Agent Home')} {href('/read/health', 'Health')}")
