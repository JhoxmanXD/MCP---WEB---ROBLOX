from __future__ import annotations

import asyncio
import json
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

try:
    from .models import Catalog, Heartbeat, Job
    from .protocol import parse_arguments
    from .store import MemoryStore
    from .agent import register_agent_routes
    from .build_info import AGENT_PROTOCOL_VERSION, DEPLOY_COMMIT, RENDER_INSTANCE_ID
except ImportError:  # Render runs `uvicorn app:app` from web/
    from models import Catalog, Heartbeat, Job
    from protocol import parse_arguments
    from store import MemoryStore
    from agent import register_agent_routes
    from build_info import AGENT_PROTOCOL_VERSION, DEPLOY_COMMIT, RENDER_INSTANCE_ID

app = FastAPI(title="MCP-WEB", version="0.1.0")
store = MemoryStore()
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")
READ_WAIT_SECONDS = 8.0
register_agent_routes(app, store, lambda: current_studio_connected())


def no_cache(response: JSONResponse) -> JSONResponse:
    response.headers.update({"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0", "CDN-Cache-Control": "no-store", "Surrogate-Control": "no-store"})
    return response


def payload(value: Any) -> JSONResponse:
    return no_cache(JSONResponse(value))


def html_page(title: str, body: str) -> HTMLResponse:
    response = HTMLResponse(f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title><style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;line-height:1.5;color:#172033}}a{{color:#0759b5}}pre{{white-space:pre-wrap;background:#f2f5f9;padding:16px;border-radius:8px}}li{{margin:4px 0}}</style></head><body>{body}</body></html>")
    response.headers.update({"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"})
    return response


def text_value(value: Any) -> str:
    return escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def current_studio_connected() -> bool:
    return store.online() and store.catalog.studio_connected


def catalog_data() -> dict[str, Any]:
    data = store.catalog.model_dump(mode="json")
    data["studio_connected"] = current_studio_connected()
    return data


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/read/health", response_class=HTMLResponse)
async def read_health() -> HTMLResponse:
    heartbeat = store.heartbeat
    body = f"<h1>MCP-WEB HEALTH</h1><p><strong>local_client_online:</strong> {str(store.online()).lower()}</p><p><strong>mcp_connected:</strong> {str(bool(heartbeat and heartbeat.mcp_connected and store.online())).lower()}</p><p><strong>studio_connected:</strong> {str(current_studio_connected()).lower()}</p><p><strong>DEPLOY_COMMIT:</strong> <code>{escape(DEPLOY_COMMIT)}</code></p><p><strong>RENDER_INSTANCE_ID:</strong> <code>{escape(RENDER_INSTANCE_ID)}</code></p><p><strong>AGENT_PROTOCOL_VERSION:</strong> <code>{escape(AGENT_PROTOCOL_VERSION)}</code></p><p><a href='/'>Home</a> · <a href='/read/catalog'>Live Catalog</a> · <a href='/read/sessions'>Read Studio Sessions</a></p>"
    return html_page("MCP-WEB Health", body)


@app.get("/read/catalog", response_class=HTMLResponse)
async def read_catalog() -> HTMLResponse:
    live_catalog = catalog_data()
    items = "".join(f"<li><code>{escape(str(tool.get('name', '')))}</code> — {escape(str(tool.get('description', '')))}</li>" for tool in live_catalog["tools"])
    body = f"<h1>MCP-WEB LIVE CATALOG</h1><p><strong>server_instance_id:</strong> <code>{escape(live_catalog['server_instance_id'])}</code></p><p><strong>catalog_generation:</strong> {live_catalog['catalog_generation']}</p><p><strong>updated_at:</strong> {escape(live_catalog['updated_at'])}</p><p><strong>studio_connected:</strong> {str(live_catalog['studio_connected']).lower()}</p><p><strong>tool_count:</strong> {live_catalog['tool_count']}</p><h2>Tools</h2><ol>{items}</ol><p><a href='/'>Home</a> · <a href='/read/health'>Live Health</a> · <a href='/read/sessions'>Read Studio Sessions</a></p>"
    return html_page("MCP-WEB Live Catalog", body)


@app.get("/read/sessions", response_class=HTMLResponse)
async def read_sessions() -> HTMLResponse:
    announced = {item.get("name") for item in store.catalog.tools if isinstance(item, dict)}
    if "studio_list_sessions" not in announced:
        return html_page("MCP-WEB Sessions", "<h1>MCP-WEB READ SESSIONS</h1><p>studio_list_sessions no está disponible en el catálogo actual.</p><p><a href='/read/catalog'>Live Catalog</a></p>")
    request_id = f"WEB_READ_{uuid4().hex[:12]}"
    store.create_job(Job(request_id=request_id, tool="studio_list_sessions", arguments={}))
    deadline = asyncio.get_running_loop().time() + READ_WAIT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        job = store.jobs[request_id]
        if job.status in {"completed", "error"}:
            return _read_result_page(job)
        await asyncio.sleep(0.25)
    job = store.jobs[request_id]
    return _read_result_page(job)


def _read_result_page(job: Job) -> HTMLResponse:
    refresh = f"<a href='/read/result/{escape(job.request_id)}'>Refresh result</a>"
    result = f"<pre>{text_value(job.result if job.status == 'completed' else job.error)}</pre>" if job.status in {"completed", "error"} else "<p>El relay aún no ha completado el job.</p>"
    body = f"<h1>MCP-WEB READ SESSIONS</h1><p><strong>request_id:</strong> <code>{escape(job.request_id)}</code></p><p><strong>status:</strong> {escape(job.status)}</p>{result}<p>{refresh} · <a href='/read/sessions'>New read</a> · <a href='/'>Home</a></p>"
    return html_page("MCP-WEB Sessions Result", body)


@app.get("/read/result/{request_id}", response_class=HTMLResponse)
async def read_result(request_id: str) -> HTMLResponse:
    job = store.jobs.get(request_id)
    if not job:
        return html_page("MCP-WEB Result", f"<h1>Result not found</h1><p>request_id: <code>{escape(request_id)}</code></p><p><a href='/read/sessions'>Read Studio Sessions</a></p>")
    return _read_result_page(job)


@app.get("/read/latest", response_class=HTMLResponse)
async def read_latest() -> HTMLResponse:
    if not store.latest:
        return html_page("MCP-WEB Latest", "<h1>MCP-WEB LATEST RESULT</h1><p>No completed job yet.</p><p><a href='/'>Home</a> · <a href='/read/sessions'>Read Studio Sessions</a></p>")
    latest = store.latest
    body = f"<h1>MCP-WEB LATEST RESULT</h1><p><strong>request_id:</strong> <code>{escape(str(latest.get('request_id')))}</code></p><p><strong>tool:</strong> {escape(str(latest.get('tool')))}</p><p><strong>status:</strong> {escape(str(latest.get('status')))}</p><p><strong>completed_at:</strong> {escape(str(latest.get('completed_at')))}</p><pre>{text_value(latest.get('result') if latest.get('status') == 'completed' else latest.get('error'))}</pre><p><a href='/'>Home</a> · <a href='/read/sessions'>Read Studio Sessions</a></p>"
    return html_page("MCP-WEB Latest Result", body)


@app.get("/api/v1/health")
@app.get("/api/v1/health.json")
async def health() -> JSONResponse:
    return payload({"ok": True, "service": "MCP-WEB", "version": "0.1.0", "local_client_online": store.online(), "deploy_commit": DEPLOY_COMMIT, "render_instance_id": RENDER_INSTANCE_ID, "agent_protocol_version": AGENT_PROTOCOL_VERSION})


@app.get("/api/v1/catalog.json")
async def catalog() -> JSONResponse:
    return payload(catalog_data())


@app.post("/api/v1/catalog")
async def upload_catalog(body: dict[str, Any]) -> JSONResponse:
    tools = body.get("tools", [])
    if not isinstance(tools, list):
        raise HTTPException(400, "tools must be a list")
    store.replace_catalog(tools, bool(body.get("studio_connected")), body.get("updated_at"))
    return payload(catalog_data())


@app.post("/api/v1/client/heartbeat")
async def heartbeat(body: Heartbeat) -> JSONResponse:
    store.heartbeat = body
    store.catalog.studio_connected = body.studio_connected
    return payload({
        "ok": True,
        "local_client_online": True,
        "catalog_present": bool(store.catalog.tools),
        "catalog_tool_count": store.catalog.tool_count,
    })


@app.get("/api/v1/call/{tool_name}")
async def call(tool_name: str, request: Request) -> JSONResponse:
    query = dict(request.query_params)
    rid = query.get("rid")
    if not rid:
        raise HTTPException(400, "rid is required for idempotent calls")
    announced = {item.get("name") for item in store.catalog.tools if isinstance(item, dict)}
    if tool_name not in announced:
        raise HTTPException(400, "tool is not announced by MCP tools/list")
    try:
        arguments = parse_arguments(query.pop("args", None), query, {"rid", "state", "t", "nonce", "args"})
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"invalid args JSON: {exc}") from exc
    job = store.create_job(Job(request_id=rid, tool=tool_name, arguments=arguments, state_key=query.get("state")))
    return payload(job.model_dump(mode="json"))


@app.get("/api/v1/jobs/next")
async def next_job() -> JSONResponse:
    job = store.next_job()
    return payload(job.model_dump(mode="json") if job else {"job": None})


@app.post("/api/v1/jobs/{request_id}/complete")
async def complete_job(request_id: str, body: dict[str, Any]) -> JSONResponse:
    if request_id not in store.jobs:
        raise HTTPException(404, "unknown request_id")
    success = bool(body.get("success"))
    job = store.complete(request_id, success, body.get("result"), body.get("error"))
    return payload(job.model_dump(mode="json"))


@app.get("/api/v1/result/{request_id}.json")
async def result(request_id: str) -> JSONResponse:
    job = store.jobs.get(request_id)
    if not job:
        raise HTTPException(404, "unknown request_id")
    return payload(job.model_dump(mode="json"))


@app.get("/api/v1/state/latest.json")
async def latest_state() -> JSONResponse:
    return payload(store.latest or {"status": "empty"})


@app.get("/api/v1/state/{state_key}.json")
async def state(state_key: str) -> JSONResponse:
    return payload(store.states.get(state_key, {"state_key": state_key, "status": "empty"}))


@app.get("/api/v1/dashboard.json")
async def dashboard_data() -> JSONResponse:
    return payload({"health": True, "local_client_online": store.online(), "studio_connected": current_studio_connected(), "tool_count": store.catalog.tool_count, "server_instance_id": store.catalog.server_instance_id, "catalog_generation": store.catalog.catalog_generation, "deploy_commit": DEPLOY_COMMIT, "render_instance_id": RENDER_INSTANCE_ID, "agent_protocol_version": AGENT_PROTOCOL_VERSION, "active_drafts": sum(not draft.get("executed") for draft in store.drafts.values()), "counts": store.counts(), "recent_jobs": store.recent(), "heartbeat": store.heartbeat.model_dump(mode="json") if store.heartbeat else None})
