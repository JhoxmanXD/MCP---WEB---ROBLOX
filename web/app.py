from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

try:
    from .models import Catalog, Heartbeat, Job
    from .protocol import parse_arguments
    from .store import MemoryStore
except ImportError:  # Render runs `uvicorn app:app` from web/
    from models import Catalog, Heartbeat, Job
    from protocol import parse_arguments
    from store import MemoryStore

app = FastAPI(title="MCP-WEB", version="0.1.0")
store = MemoryStore()
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def no_cache(response: JSONResponse) -> JSONResponse:
    response.headers.update({"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"})
    return response


def payload(value: Any) -> JSONResponse:
    return no_cache(JSONResponse(value))


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/v1/health")
@app.get("/api/v1/health.json")
async def health() -> JSONResponse:
    return payload({"ok": True, "service": "MCP-WEB", "version": "0.1.0", "local_client_online": store.online()})


@app.get("/api/v1/catalog.json")
async def catalog() -> JSONResponse:
    return payload(store.catalog.model_dump(mode="json"))


@app.post("/api/v1/catalog")
async def upload_catalog(body: dict[str, Any]) -> JSONResponse:
    tools = body.get("tools", [])
    if not isinstance(tools, list):
        raise HTTPException(400, "tools must be a list")
    values = {"studio_connected": bool(body.get("studio_connected")), "tool_count": len(tools), "tools": tools}
    if body.get("updated_at"):
        values["updated_at"] = body["updated_at"]
    store.catalog = Catalog(**values)
    return payload(store.catalog.model_dump(mode="json"))


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
    return payload({"health": True, "local_client_online": store.online(), "studio_connected": store.catalog.studio_connected, "tool_count": store.catalog.tool_count, "counts": store.counts(), "recent_jobs": store.recent(), "heartbeat": store.heartbeat.model_dump(mode="json") if store.heartbeat else None})
