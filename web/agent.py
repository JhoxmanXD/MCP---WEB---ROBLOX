from __future__ import annotations

import json
import asyncio
import copy
import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from html import escape
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

try:
    from .models import Job
    from .build_info import AGENT_PROTOCOL_VERSION, DEPLOY_COMMIT, RENDER_INSTANCE_ID
    from .agent_state import agent_state_external_io, refresh_agent_state_for_io
except ImportError:  # Render runs `uvicorn app:app` from web/
    from models import Job
    from build_info import AGENT_PROTOCOL_VERSION, DEPLOY_COMMIT, RENDER_INSTANCE_ID
    from agent_state import agent_state_external_io, refresh_agent_state_for_io


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


logger = logging.getLogger("mcp-web.agent")

# A Render Free instance can temporarily leave a discovery job queued while
# the relay reconnects.  Keep the bounded wait long enough to cover that
# recovery window; otherwise the editor freezes the incomplete fallback into
# an immutable snapshot before the runtime metadata arrives.
DISCOVERY_WAIT_SECONDS = 45.0


def schema_type(schema: dict[str, Any]) -> str:
    if isinstance(schema.get("type"), str):
        return schema["type"]
    for option in schema.get("anyOf", []) + schema.get("oneOf", []):
        if option.get("type") not in {"null", None}:
            return option.get("type", "value")
    return "value"


def roblox_type(schema: dict[str, Any]) -> str | None:
    value = schema.get("roblox_type") or schema.get("robloxType") or schema.get("x-roblox-type")
    return value if isinstance(value, str) else None


def typed_schema(value_type: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = metadata or {}
    schema: dict[str, Any] = {"type": "object", "roblox_type": value_type}
    if value_type == "Vector2":
        schema["properties"] = {"x": {"type": "number"}, "y": {"type": "number"}}
    elif value_type == "Vector3":
        schema["properties"] = {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}}
    elif value_type == "Color3":
        schema["properties"] = {"r": {"type": "number", "minimum": 0, "maximum": 1}, "g": {"type": "number", "minimum": 0, "maximum": 1}, "b": {"type": "number", "minimum": 0, "maximum": 1}}
    elif value_type == "CFrame":
        schema["properties"] = {str(index): {"type": "number"} for index in range(12)}
    elif value_type == "UDim":
        schema["properties"] = {"scale": {"type": "number"}, "offset": {"type": "integer"}}
    elif value_type == "UDim2":
        schema["properties"] = {"x": typed_schema("UDim"), "y": typed_schema("UDim")}
    elif value_type == "NumberRange":
        schema["properties"] = {"min": {"type": "number"}, "max": {"type": "number"}}
    elif value_type == "BrickColor":
        schema["properties"] = {"number": {"type": "integer"}, "name": {"type": "string"}}
    if metadata.get("enumValues") or metadata.get("enum_values"):
        schema["enum_values"] = copy.deepcopy(metadata.get("enumValues") or metadata.get("enum_values"))
    if metadata.get("enumType") or metadata.get("enum_type"):
        schema["enum_type"] = metadata.get("enumType") or metadata.get("enum_type")
    return schema


def merge_property_metadata(existing: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    """Merge runtime metadata without losing a previously complete enum list."""
    merged = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    if not isinstance(incoming, dict):
        return merged
    for key, value in incoming.items():
        if key in {"enumValues", "enum_values"}:
            if isinstance(value, list) and value:
                merged[key] = copy.deepcopy(value)
            elif key not in merged:
                merged[key] = copy.deepcopy(value)
            continue
        if value is not None:
            merged[key] = copy.deepcopy(value)
    return merged


def enum_metadata_incomplete(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    roblox_value_type = metadata.get("robloxType") or metadata.get("roblox_type")
    return roblox_value_type == "EnumItem" and not (
        isinstance(metadata.get("enumValues"), list) and metadata.get("enumValues")
    ) and not (
        isinstance(metadata.get("enum_values"), list) and metadata.get("enum_values")
    )


# These are the property contracts confirmed from the Roblox bridge.  The
# table is intentionally small: dynamic bridge metadata remains authoritative
# for everything not listed here, and unknown properties keep the generic
# fallback instead of being guessed.
ROBLOX_CLASS_ANCESTORS: dict[str, tuple[str, ...]] = {
    "Part": ("BasePart", "PVInstance", "Instance"),
    "SpawnLocation": ("Part", "BasePart", "PVInstance", "Instance"),
    "MeshPart": ("BasePart", "PVInstance", "Instance"),
    "WedgePart": ("Part", "BasePart", "PVInstance", "Instance"),
    "CornerWedgePart": ("Part", "BasePart", "PVInstance", "Instance"),
    "TrussPart": ("Part", "BasePart", "PVInstance", "Instance"),
    "Seat": ("Part", "BasePart", "PVInstance", "Instance"),
    "VehicleSeat": ("Seat", "Part", "BasePart", "PVInstance", "Instance"),
}

ROBLOX_KNOWN_PROPERTY_TYPES: dict[str, dict[str, str]] = {
    "BasePart": {
        "Size": "Vector3",
        "Position": "Vector3",
        "Color": "Color3",
        "Material": "EnumItem",
        "Anchored": "boolean",
        "CanCollide": "boolean",
    },
}


def known_property_schema(class_name: str | None, property_name: str) -> dict[str, Any] | None:
    """Resolve only audited class/property pairs, including inheritance."""
    if not isinstance(class_name, str) or not class_name:
        return None
    classes = (class_name,) + ROBLOX_CLASS_ANCESTORS.get(class_name, ())
    for class_key in classes:
        value_type = ROBLOX_KNOWN_PROPERTY_TYPES.get(class_key, {}).get(property_name)
        if value_type:
            metadata = {"robloxType": value_type}
            if value_type == "EnumItem" and property_name == "Material":
                metadata["enumType"] = "Material"
            return typed_schema(value_type, metadata)
    return None


def nullable(schema: dict[str, Any]) -> bool:
    return any(option.get("type") == "null" for option in schema.get("anyOf", []) + schema.get("oneOf", [])) or schema.get("type") == "null"


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
    footer = f"<footer><small>DEPLOY_COMMIT: <code>{escape(DEPLOY_COMMIT)}</code> · RENDER_INSTANCE_ID: <code>{escape(RENDER_INSTANCE_ID)}</code> · AGENT_PROTOCOL_VERSION: <code>{escape(AGENT_PROTOCOL_VERSION)}</code></small></footer>"
    response = HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title><style>body{{font-family:system-ui,sans-serif;max-width:1000px;margin:30px auto;padding:0 18px;color:#172033}}a{{color:#0759b5;margin-right:12px}}pre{{white-space:pre-wrap;background:#f2f5f9;padding:14px;border-radius:8px}}.card{{border:1px solid #d9e0ea;padding:14px;margin:10px 0;border-radius:8px}}.missing{{color:#9a2c00}}code{{background:#eef2f7;padding:2px 4px}}footer{{margin-top:28px;color:#667085}}</style></head><body>{body}{footer}</body></html>")
    response.headers.update({"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0", "CDN-Cache-Control": "no-store", "Surrogate-Control": "no-store"})
    return response


def agent_redirect(path: str, status_code: int = 303) -> RedirectResponse:
    response = RedirectResponse(path, status_code=status_code)
    response.headers.update({"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0", "CDN-Cache-Control": "no-store", "Surrogate-Control": "no-store"})
    return response


def register_agent_routes(app, store, current_studio_connected, agent_state_status=None):
    DRAFT_TTL_SECONDS = 3600

    def lifecycle_context() -> dict[str, str]:
        return {
            "store_id": str(getattr(store, "server_instance_id", "unknown")),
            "process_id": str(os.getpid()),
            "instance_id": str(RENDER_INSTANCE_ID),
        }

    def request_context(request: Request) -> dict[str, str]:
        def header(name: str) -> str:
            value = request.headers.get(name, "")
            return value[:160].replace("\r", " ").replace("\n", " ") or "none"
        return {
            "method": request.method,
            "path": request.url.path[:200],
            "request_id": header("x-request-id"),
            "user_agent": header("user-agent"),
            "referer": header("referer"),
        }

    def backend_context() -> dict[str, Any]:
        backend = agent_state_status() if agent_state_status else {}
        return {
            "backend_identity_hash": backend.get("backend_identity_hash", "unknown"),
            "redis_db": backend.get("redis_db", "unknown"),
            "namespace": backend.get("namespace", "unknown"),
            "state_key": backend.get("state_key", "unknown"),
        }

    def register_action(action: dict[str, Any]) -> None:
        draft = store.drafts.get(action.get("draft_id"))
        if draft and not action.get("expires_at"):
            action["expires_at"] = draft.get("expires_at")
        action.setdefault("state_schema_version", "agent-state-v1")
        store.pending_agent_action_ids.add(action["action_id"])
        lock = getattr(store, "lock", None)
        if lock is None:
            store.actions[action["action_id"]] = action
        else:
            with lock:
                store.actions[action["action_id"]] = action
        context = lifecycle_context()
        backend = backend_context()
        logger.warning(
            "ACTION_CREATED action_id=%s draft_id=%s view_id=%s editor_id=%s expected_revision=%s operation=%s created_at=%s request_id=%s store_id=%s process_id=%s instance_id=%s process=%s render_instance=%s backend_identity_hash=%s redis_db=%s namespace=%s state_key=%s persisted=pending state_backend=%s",
            action["action_id"], action.get("draft_id"), action.get("view_id", "none"), action.get("editor_id", "none"),
            action.get("expected_revision"), action.get("operation"), action.get("created_at"),
            action.get("request_id", "unknown"), context["store_id"], context["process_id"], context["instance_id"],
            context["process_id"], context["instance_id"], backend["backend_identity_hash"], backend["redis_db"],
            backend["namespace"], backend["state_key"],
            (agent_state_status() if agent_state_status else {}).get("mode", "memory"),
        )

    def expired_state(kind: str, state_id: str, detail: str = "") -> HTMLResponse:
        context = lifecycle_context()
        logger.warning(
            "AGENT_STATE_EXPIRED kind=%s state_id=%s reason=%s store_id=%s process_id=%s instance_id=%s action_count=%s drafts=%s views=%s detail=%s",
            kind, state_id, detail or "none", context["store_id"], context["process_id"], context["instance_id"],
            len(store.actions), len(store.drafts), len(store.views), detail or "none",
        )
        body = (
            "<h1>AGENT STATE EXPIRED</h1>"
            f"<p>{escape(kind.capitalize())} no longer available. Return to Tools and start a fresh invocation.</p>"
            f"<p>state_id: <code>{escape(state_id)}</code></p>"
            f"<p>store_id: <code>{escape(context['store_id'])}</code></p>"
            f"<p>process_id: <code>{escape(context['process_id'])}</code></p>"
            f"<p>{href('/agent/tools', 'Return to Tools')} {href('/agent', 'Agent Home')}</p>"
        )
        response = agent_page("Agent State Expired", body)
        response.status_code = 410
        return response

    def missing_action(action_id: str) -> HTMLResponse:
        context = lifecycle_context()
        backend = agent_state_status() if agent_state_status else {}
        diagnostic = backend_context()
        logger.warning(
            "ACTION_LOOKUP action_id=%s exists=false reason=ACTION_KEY_MISSING redis_key=%s state_key=%s redis_ttl=%s deserialize_status=not-found draft_exists=false view_exists=false owner_exists=false consumed=unknown resulting_url=none namespace=%s backend_identity_hash=%s redis_db=%s request_id=unknown store_id=%s process_id=%s instance_id=%s process=%s render_instance=%s action_count=%s drafts=%s views=%s",
            action_id, diagnostic["state_key"], diagnostic["state_key"], getattr(store, "agent_state_observed_ttl", "unknown"),
            diagnostic["namespace"], diagnostic["backend_identity_hash"], diagnostic["redis_db"],
            context["store_id"], context["process_id"], context["instance_id"], context["process_id"], context["instance_id"],
            len(store.actions), len(store.drafts), len(store.views),
        )
        return expired_state("action", action_id, "ACTION_KEY_MISSING")

    def purge_drafts() -> None:
        now_epoch = time.time()
        cutoff = now_epoch - DRAFT_TTL_SECONDS
        for draft_id, draft in list(store.drafts.items()):
            try:
                deadline = draft.get("expires_at")
                expires_epoch = datetime.fromisoformat(deadline).timestamp() if deadline else datetime.fromisoformat(draft.get("last_access", draft["created_at"])).timestamp() + DRAFT_TTL_SECONDS
                if expires_epoch < now_epoch or expires_epoch < cutoff:
                    del store.drafts[draft_id]
                    for collection in (store.views, store.actions, store.prepared, store.result_views, store.editors):
                        for item_id, item in list(collection.items()):
                            if item.get("draft_id") == draft_id:
                                del collection[item_id]
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
        refresh_deadline(draft)
        return draft

    def touch_view(view: dict[str, Any]) -> None:
        draft = store.drafts.get(view.get("draft_id"))
        if draft:
            refresh_deadline(draft)
            view["last_access"] = draft["last_access"]
            view["expires_at"] = draft["expires_at"]

    def refresh_deadline(draft: dict[str, Any]) -> None:
        touched = datetime.now(timezone.utc)
        draft["last_access"] = touched.isoformat()
        draft["expires_at"] = (touched.timestamp() + DRAFT_TTL_SECONDS)
        draft["expires_at"] = datetime.fromtimestamp(draft["expires_at"], timezone.utc).isoformat()
        for collection in (store.views, store.actions, store.prepared, store.result_views, store.editors):
            for item in collection.values():
                if item.get("draft_id") == draft.get("draft_id"):
                    item["expires_at"] = draft["expires_at"]

    def required(schema: dict[str, Any]) -> list[str]:
        return list(schema.get("required", []))

    def missing(draft: dict[str, Any]) -> list[str]:
        return [name for name in required(draft["schema"]) if name not in draft["arguments"]]

    def arguments_hash(draft: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"tool_name": draft["tool_name"], "arguments": draft.get("arguments", {})},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def advance_revision(
        draft: dict[str, Any],
        reason: str,
        action: dict[str, Any] | None = None,
        before_hash: str | None = None,
    ) -> bool:
        revision_before = int(draft.get("revision", 0))
        before_hash = before_hash or arguments_hash(draft)
        after_hash = arguments_hash(draft)
        action_id = action.get("action_id") if action else None
        operation = action.get("operation") if action else reason
        context = lifecycle_context()
        if before_hash == after_hash:
            refresh_deadline(draft)
            logger.warning(
                "DRAFT_REVISION_UNCHANGED draft_id=%s revision=%s reason=%s action_id=%s operation=%s arguments_hash=%s process=%s instance=%s",
                draft["draft_id"], revision_before, reason, action_id, operation,
                after_hash, context["process_id"], context["instance_id"],
            )
            return False
        draft["revision"] = revision_before + 1
        refresh_deadline(draft)
        logger.warning(
            "DRAFT_REVISION_ADVANCED draft_id=%s revision_before=%s revision_after=%s reason=%s action_id=%s operation=%s arguments_hash_before=%s arguments_hash_after=%s process=%s instance=%s",
            draft["draft_id"], revision_before, draft["revision"], reason, action_id, operation,
            before_hash, after_hash, context["process_id"], context["instance_id"],
        )
        return True

    def create_agent_view(draft: dict[str, Any]) -> str:
        view_id = "V_" + uuid4().hex[:18]
        arguments = copy.deepcopy(draft.get("arguments", {}))
        created_at = now()
        snapshot = {
            "view_id": view_id,
            "draft_id": draft["draft_id"],
            "revision": int(draft.get("revision", 0)),
            "created_at": created_at,
            "last_access": created_at,
            "expires_at": draft.get("expires_at"),
            "arguments_snapshot": arguments,
            "missing_arguments": [name for name in required(draft["schema"]) if name not in arguments],
            "ready": not any(name for name in required(draft["schema"]) if name not in arguments),
            "tool_name": draft["tool_name"],
            "schema": copy.deepcopy(draft["schema"]),
            "action_ids": {},
            "recent_string_values": copy.deepcopy(store.recent_string_values),
            "recent_refs": copy.deepcopy(store.recent_refs[-100:]),
        }
        store.views[view_id] = snapshot
        return view_id

    def make_action(draft_id: str, expected_revision: int, operation: str, payload: dict[str, Any]) -> str:
        action_id = "A_" + uuid4().hex[:18]
        register_action({
            "action_id": action_id,
            "draft_id": draft_id,
            "expected_revision": expected_revision,
            "operation": operation,
            "payload": copy.deepcopy(payload),
            "created_at": now(),
            "expires_at": store.drafts.get(draft_id, {}).get("expires_at"),
            "consumed": False,
            "resulting_url": None,
        })
        return f"/agent/action/{action_id}"

    def action_link(draft_id: str, revision: int, operation: str, payload: dict[str, Any], label: str) -> str:
        return href(make_action(draft_id, revision, operation, payload), label)

    def view_action_link(view: dict[str, Any], operation: str, payload: dict[str, Any], label: str) -> str:
        key = json.dumps([operation, payload], ensure_ascii=False, sort_keys=True, default=str)
        action_id = view.setdefault("action_ids", {}).get(key)
        if not action_id:
            action_id = "A_" + uuid4().hex[:18]
            register_action({"action_id": action_id, "draft_id": view["draft_id"], "view_id": view["view_id"], "expected_revision": view["revision"], "operation": operation, "payload": copy.deepcopy(payload), "created_at": now(), "consumed": False, "resulting_url": None})
            view["action_ids"][key] = action_id
        return href(f"/agent/action/{action_id}", label)

    def path_get(value: Any, path: list[Any]) -> Any:
        current = value
        for part in path:
            current = current[int(part)] if isinstance(current, list) else current[part]
        return current

    def path_set(value: Any, path: list[Any], replacement: Any) -> None:
        if not path:
            raise ValueError("empty path")
        current = value
        for part in path[:-1]:
            if isinstance(current, list):
                current = current[int(part)]
            else:
                if part not in current or not isinstance(current[part], (dict, list)):
                    current[part] = {}
                current = current[part]
        last = path[-1]
        if isinstance(current, list):
            current[int(last)] = replacement
        else:
            current[last] = replacement

    def path_delete(value: Any, path: list[Any]) -> None:
        if not path:
            return
        current = path_get(value, path[:-1]) if len(path) > 1 else value
        last = path[-1]
        if isinstance(current, list):
            current.pop(int(last))
        elif isinstance(current, dict):
            current.pop(last, None)

    def infer_schema(value: Any) -> dict[str, Any]:
        if isinstance(value, bool): return {"type": "boolean"}
        if isinstance(value, int) and not isinstance(value, bool): return {"type": "integer"}
        if isinstance(value, float): return {"type": "number"}
        if isinstance(value, str): return {"type": "string"}
        if isinstance(value, list): return {"type": "array", "items": infer_schema(value[0]) if value else {"type": "string"}}
        if isinstance(value, dict): return {"type": "object", "additionalProperties": True}
        if value is None: return {"type": ["null", "string"]}
        return {"type": "string"}

    def default_for_schema(schema: dict[str, Any]) -> Any:
        if "default" in schema: return copy.deepcopy(schema["default"])
        special = roblox_type(schema)
        if special == "Vector2": return {"$type": "Vector2", "x": 0, "y": 0}
        if special == "Vector3": return {"$type": "Vector3", "x": 0, "y": 0, "z": 0}
        if special == "Color3": return {"$type": "Color3", "r": 0, "g": 0, "b": 0}
        if special == "CFrame": return {"$type": "CFrame", "components": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]}
        if special == "UDim": return {"$type": "UDim", "scale": 0, "offset": 0}
        if special == "UDim2": return {"$type": "UDim2", "x": {"$type": "UDim", "scale": 0, "offset": 0}, "y": {"$type": "UDim", "scale": 0, "offset": 0}}
        if special == "NumberRange": return {"$type": "NumberRange", "min": 0, "max": 0}
        if special == "BrickColor": return {"$type": "BrickColor", "number": 1, "name": "White"}
        if special == "EnumItem":
            values = schema.get("enum_values", [])
            return copy.deepcopy(values[0]) if values else None
        typ = schema_type(schema)
        if typ == "object": return {}
        if typ == "array": return []
        if typ == "boolean": return False
        if typ in {"integer", "number"}: return 0
        if typ == "string": return ""
        return None

    def selector_schema(name: str, schema: dict[str, Any]) -> bool:
        return name.lower() in {"ref", "parent", "target", "instance", "selection", "script"}

    def create_editor(view: dict[str, Any], path: list[Any], kind: str = "value", schema: dict[str, Any] | None = None, property_schemas: dict[str, dict[str, Any]] | None = None, parent_schema: dict[str, Any] | None = None) -> str:
        editor_id = "E_" + uuid4().hex[:18]
        editor_schema = copy.deepcopy(schema or {})
        if path:
            try:
                value = copy.deepcopy(path_get(view["arguments_snapshot"], path))
            except (KeyError, IndexError, TypeError):
                value = default_for_schema(editor_schema)
        else:
            value = copy.deepcopy(view["arguments_snapshot"])
        store.editors[editor_id] = {"editor_id": editor_id, "view_id": view["view_id"], "draft_id": view["draft_id"], "revision": view["revision"], "path": copy.deepcopy(path), "kind": kind, "schema": editor_schema or infer_schema(value), "parent_schema": copy.deepcopy(parent_schema), "property_schemas": copy.deepcopy(property_schemas or {}), "value_snapshot": value, "action_ids": {}}
        return f"/agent/editor/{editor_id}"

    def create_key_editor(view: dict[str, Any], parent_path: list[Any], parent_schema: dict[str, Any] | None = None, property_schemas: dict[str, dict[str, Any]] | None = None) -> str:
        editor_id = "E_" + uuid4().hex[:18]
        store.editors[editor_id] = {"editor_id": editor_id, "view_id": view["view_id"], "draft_id": view["draft_id"], "revision": view["revision"], "path": copy.deepcopy(parent_path), "kind": "key", "schema": {"type": "string"}, "parent_schema": copy.deepcopy(parent_schema or {"type": "object", "additionalProperties": True}), "property_schemas": copy.deepcopy(property_schemas or {}), "value_snapshot": "", "action_ids": {}}
        return f"/agent/editor/{editor_id}"

    def editor_action_link(editor: dict[str, Any], operation: str, payload: dict[str, Any], label: str) -> str:
        key = json.dumps([operation, payload], ensure_ascii=False, sort_keys=True, default=str)
        action_id = editor["action_ids"].get(key)
        if not action_id:
            action_id = "A_" + uuid4().hex[:18]
            register_action({"action_id": action_id, "draft_id": editor["draft_id"], "view_id": editor.get("view_id"), "editor_id": editor["editor_id"], "expected_revision": editor["revision"], "operation": operation, "payload": {"editor_id": editor["editor_id"], **copy.deepcopy(payload)}, "created_at": now(), "consumed": False, "resulting_url": None})
            editor["action_ids"][key] = action_id
        return href(f"/agent/action/{action_id}", label)

    def editor_back(editor: dict[str, Any]) -> str:
        return href(f"/agent/view/{editor['view_id']}", "Back to Draft")

    def render_editor(editor: dict[str, Any]) -> HTMLResponse:
        value = editor["value_snapshot"]; schema = editor["schema"]; kind = editor["kind"]
        actions: list[str] = []
        if kind == "key":
            for token, char in STRING_CHARACTERS:
                actions.append(editor_action_link(editor, "editor_append_key", {"character": char}, f"Append {char if char != ' ' else 'space'}"))
            actions += [editor_action_link(editor, "editor_backspace_key", {}, "Backspace"), editor_action_link(editor, "editor_clear_key", {}, "Clear"), editor_action_link(editor, "editor_finish_key", {}, "Finish"), editor_back(editor)]
            body = f"<h1>Object Key Composer</h1><p>EDITOR_ID: <code>{escape(editor['editor_id'])}</code></p><p>DRAFT_REVISION: {editor['revision']}</p><p>CURRENT KEY: <code>{escape(str(value))}</code></p><p>{' '.join(actions)}</p>"
            return agent_page("Object Key Composer", body)
        special = roblox_type(schema)
        if special in {"Vector2", "Vector3", "Color3", "CFrame", "UDim", "UDim2", "NumberRange", "BrickColor"}:
            if special == "Vector2":
                components = [("X", ["x"]), ("Y", ["y"])]
            elif special == "Vector3":
                components = [("X", ["x"]), ("Y", ["y"]), ("Z", ["z"])]
            elif special == "Color3":
                components = [("R", ["r"]), ("G", ["g"]), ("B", ["b"])]
            elif special == "CFrame":
                components = [(label, ["components", str(index)]) for index, label in enumerate(("X", "Y", "Z", "R00", "R01", "R02", "R10", "R11", "R12", "R20", "R21", "R22"))]
            elif special == "UDim":
                components = [("Scale", ["scale"]), ("Offset", ["offset"])]
            elif special == "UDim2":
                components = [("X Scale", ["x", "scale"]), ("X Offset", ["x", "offset"]), ("Y Scale", ["y", "scale"]), ("Y Offset", ["y", "offset"])]
            elif special == "NumberRange":
                components = [("Min", ["min"]), ("Max", ["max"])]
            else:
                components = [("Number", ["number"]), ("Name", ["name"])]
            for label, suffix in components:
                component_schema = {"type": "number" if label != "Name" else "string"}
                if special == "Color3":
                    component_schema.update({"minimum": 0, "maximum": 1})
                actions.append(editor_action_link(editor, "open_editor", {"view_id": editor["view_id"], "path": editor["path"] + suffix, "kind": component_schema["type"], "schema": component_schema, "parent_schema": schema}, f"Edit {label}"))
            body = f"<h1>{escape(special)} Editor</h1><p>EDITOR_ID: <code>{escape(editor['editor_id'])}</code></p><p>DRAFT_REVISION: {editor['revision']}</p><p>ROBLOX_TYPE: <code>{escape(special)}</code></p><pre>{escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre><p>{' '.join(actions)} {editor_back(editor)}</p>"
            return agent_page(f"{special} Editor", body)
        if special == "EnumItem":
            values = schema.get("enum_values", [])
            actions = [editor_action_link(editor, "editor_set_value", {"value": item}, f"Set {item.get('name', 'enum value')}") for item in values if isinstance(item, dict)]
            body = f"<h1>Enum Editor</h1><p>EDITOR_ID: <code>{escape(editor['editor_id'])}</code></p><p>DRAFT_REVISION: {editor['revision']}</p><p>ROBLOX_TYPE: <code>EnumItem</code></p><p>ENUM_TYPE: <code>{escape(str(schema.get('enum_type', 'unknown')))}</code></p><pre>{escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre><p>{' '.join(actions) or 'No enum metadata available; typed picker unavailable.'} {editor_back(editor)}</p>"
            return agent_page("Enum Editor", body)
        if special in {"PhysicalProperties", "Font", "NumberSequence", "ColorSequence", "Rect", "Vector2"} and special not in {"Vector2"}:
            body = f"<h1>Unsupported Typed Editor</h1><p>EDITOR_ID: <code>{escape(editor['editor_id'])}</code></p><p>ROBLOX_TYPE: <code>{escape(special)}</code></p><p>This Roblox type is readable or metadata-visible but has no safe write editor in the current bridge contract.</p><p>{editor_back(editor)}</p>"
            return agent_page("Unsupported Typed Editor", body)
        typ = schema_type(schema)
        if typ == "object":
            items = []
            properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
            for key, item in value.items() if isinstance(value, dict) else []:
                item_schema = editor.get("property_schemas", {}).get(str(key)) or properties.get(key) or (schema.get("additionalProperties") if isinstance(schema.get("additionalProperties"), dict) else None)
                item_schema = item_schema or infer_schema(item)
                edit = editor_action_link(editor, "open_editor", {"view_id": editor["view_id"], "path": editor["path"] + [key], "kind": roblox_type(item_schema) or schema_type(item_schema), "schema": item_schema, "parent_schema": schema, "property_schemas": editor.get("property_schemas", {})}, f"Edit {key}")
                remove = editor_action_link(editor, "editor_remove_key", {"key": key}, f"Remove {key}")
                items.append(f"<li><strong>{escape(str(key))}</strong>: <code>{escape(str(item))}</code> {edit} {remove}</li>")
            add = editor_action_link(editor, "editor_open_key", {"parent_schema": schema}, "Add field")
            clear = editor_action_link(editor, "editor_clear_container", {}, "Clear object")
            body = f"<h1>Object Editor</h1><p>EDITOR_ID: <code>{escape(editor['editor_id'])}</code></p><p>DRAFT_REVISION: {editor['revision']}</p><pre>{escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre><ul>{''.join(items) or '<li>No fields</li>'}</ul><p>{add} {clear} {editor_back(editor)}</p>"
            return agent_page("Object Editor", body)
        if typ == "array":
            items = []
            item_schema = schema.get("items", {}) if isinstance(schema.get("items"), dict) else {}
            for index, item in enumerate(value if isinstance(value, list) else []):
                actual_schema = item_schema if item_schema and schema_type(item_schema) != "value" else infer_schema(item)
                edit = editor_action_link(editor, "open_editor", {"view_id": editor["view_id"], "path": editor["path"] + [index], "kind": roblox_type(actual_schema) or schema_type(actual_schema), "schema": actual_schema, "parent_schema": schema}, f"Edit item {index}")
                remove = editor_action_link(editor, "editor_remove_item", {"index": index}, f"Remove item {index}")
                items.append(f"<li><code>{escape(str(item))}</code> {edit} {remove}</li>")
            add = editor_action_link(editor, "editor_add_item", {"value": default_for_schema(item_schema)}, "Add item")
            links = [add, editor_action_link(editor, "editor_remove_last", {}, "Remove last"), editor_action_link(editor, "editor_clear_container", {}, "Clear"), editor_back(editor)]
            body = f"<h1>Array Editor</h1><p>EDITOR_ID: <code>{escape(editor['editor_id'])}</code></p><p>DRAFT_REVISION: {editor['revision']}</p><pre>{escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre><ol>{''.join(items) or '<li>Empty array</li>'}</ol><p>{' '.join(links)}</p>"
            return agent_page("Array Editor", body)
        if "enum" in schema:
            actions += [editor_action_link(editor, "editor_set_value", {"value": item}, f"Set {item}") for item in schema["enum"]]
        elif typ == "boolean":
            actions += [editor_action_link(editor, "editor_set_value", {"value": True}, "Set true"), editor_action_link(editor, "editor_set_value", {"value": False}, "Set false")]
        elif typ in {"integer", "number"}:
            for token in ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "-", "."]:
                actions.append(editor_action_link(editor, "editor_append_number", {"token": token}, token))
            actions += [editor_action_link(editor, "editor_backspace", {}, "Backspace"), editor_action_link(editor, "editor_clear_scalar", {}, "Clear"), editor_action_link(editor, "editor_finish_number", {}, "Finish")]
        elif typ == "string":
            for token, char in STRING_CHARACTERS:
                actions.append(editor_action_link(editor, "editor_append_string", {"character": char}, f"Append {char if char != ' ' else 'space'}"))
            actions += [editor_action_link(editor, "editor_backspace", {}, "Backspace"), editor_action_link(editor, "editor_clear_scalar", {}, "Clear"), editor_action_link(editor, "editor_finish_scalar", {}, "Finish")]
        elif typ == "value":
            actions += [editor_action_link(editor, "editor_initialize_value", {"value_type": "string"}, "Edit as string"), editor_action_link(editor, "editor_initialize_value", {"value_type": "number"}, "Edit as number"), editor_action_link(editor, "editor_initialize_value", {"value_type": "boolean"}, "Edit as boolean"), editor_action_link(editor, "editor_initialize_value", {"value_type": "object"}, "Edit as object"), editor_action_link(editor, "editor_initialize_value", {"value_type": "array"}, "Edit as array"), editor_action_link(editor, "editor_set_value", {"value": None}, "Set null")]
        displayed_type = "unknown (generic fallback)" if typ == "value" else typ
        body = f"<h1>Value Editor</h1><p>EDITOR_ID: <code>{escape(editor['editor_id'])}</code></p><p>DRAFT_REVISION: {editor['revision']}</p><p>TYPE: <code>{escape(displayed_type)}</code></p><p>CURRENT VALUE: <code>{escape(str(value))}</code></p><p>{' '.join(actions)} {editor_back(editor)}</p>"
        return agent_page("Value Editor", body)

    def stale_page(action: dict[str, Any], draft: dict[str, Any]) -> HTMLResponse:
        current = create_agent_view(draft)
        body = f"<h1>STALE DRAFT VIEW</h1><p>Expected revision: {action['expected_revision']}</p><p>Current revision: {draft.get('revision', 0)}</p><p>This action belongs to an older draft view and was NOT applied.</p><p>{href('/agent/view/' + current, 'Open Current Draft')}</p>"
        return agent_page("Stale Draft View", body)

    def snapshot_links(view: dict[str, Any], name: str, schema: dict[str, Any]) -> str:
        links: list[str] = []
        base = {"name": name}
        current = view.get("arguments_snapshot", {}).get(name, object())
        if "enum" in schema:
            links += [view_action_link(view, "set_arg", {**base, "value": value}, f"Set {value}") for value in schema["enum"] if value != current]
        typ = schema_type(schema)
        if typ == "string":
            values = [schema.get("default"), "Workspace", "Part", "Folder", "Model", "Script", "Name", "Parent"] + list(reversed(view.get("recent_string_values", [])))
            links += [view_action_link(view, "set_arg", {**base, "value": value}, f"Set {value}") for value in values if value is not None and value != current]
            links.append(view_action_link(view, "open_string", {"name": name, "view_id": view["view_id"]}, f"Open String Composer ({name})"))
        elif typ == "boolean":
            links += [view_action_link(view, "set_arg", {**base, "value": value}, f"Set {str(value).lower()}") for value in (True, False) if value != current]
        elif typ in {"integer", "number"}:
            values = [0, 1, -1, 10, 100, 0.5] if typ == "number" else [0, 1, -1, 10, 100]
            links += [view_action_link(view, "set_arg", {**base, "value": value}, str(value)) for value in values if value != current]
        elif typ == "object":
            links.append(view_action_link(view, "open_editor", {"view_id": view["view_id"], "path": [name], "kind": "object", "schema": schema}, "Edit object"))
            if name in {"values", "properties", "attributes"}:
                links.append(view_action_link(view, "set_nested", {"name": name, "path": ["Anchored"], "value": True}, "Set Anchored=true"))
        elif typ == "array" and name == "names":
            links.append(view_action_link(view, "set_arg", {"name": name, "value": ["Anchored"]}, "Read Anchored"))
        elif typ == "array":
            links.append(view_action_link(view, "open_editor", {"view_id": view["view_id"], "path": [name], "kind": "array", "schema": schema}, "Edit array"))
        if selector_schema(name, schema):
            links.append(view_action_link(view, "open_picker", {"name": name, "view_id": view["view_id"]}, "Choose Roblox Instance"))
        if nullable(schema):
            if current is not None:
                links.append(view_action_link(view, "set_arg", {**base, "value": None}, "Set null"))
        if name in view.get("arguments_snapshot", {}):
            links.append(view_action_link(view, "clear", base, "Clear"))
        return " ".join(links)

    def remember_string(value: str) -> None:
        if value and value in store.recent_string_values:
            store.recent_string_values.remove(value)
        if value:
            store.recent_string_values.append(value)
            del store.recent_string_values[:-12]

    def same_discovery_job(job: Job, tool_name: str, arguments: dict[str, Any]) -> bool:
        return job.tool == tool_name and isinstance(job.arguments, dict) and job.arguments == arguments

    async def discover(tool_name: str, arguments: dict[str, Any], action: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if tool_name not in catalog_tools():
            return None
        existing = next((job for job in reversed(list(store.jobs.values())) if same_discovery_job(job, tool_name, arguments) and (
            job.status == "completed" or (
                action is not None and action.get("discovery_request_id") == job.request_id and job.status in {"pending", "running"}
            )
        )), None)
        job = existing or store.create_job(Job(request_id="WEB_AGENT_DISCOVER_" + uuid4().hex[:12], tool=tool_name, arguments=copy.deepcopy(arguments)))
        request_id = job.request_id
        if action is not None:
            action.update({
                "pending_external_io": job.status not in {"completed", "error"},
                "pending_operation": "discover",
                "discovery_tool": tool_name,
                "discovery_request_id": request_id,
                "discovery_arguments": copy.deepcopy(arguments),
            })
        logger.warning(
            "AGENT_DISCOVER_START request_id=%s tool=%s action_id=%s status=%s",
            request_id, tool_name, (action or {}).get("action_id", "none"), job.status,
        )
        if job.status in {"completed", "error"}:
            if job.status == "completed":
                collect_refs(job.result)
            if action is not None:
                action["pending_external_io"] = False
            logger.warning(
                "AGENT_DISCOVER_RESULT request_id=%s tool=%s action_id=%s status=%s",
                request_id, tool_name, (action or {}).get("action_id", "none"), job.status,
            )
            return job.model_dump(mode="json")

        # Persist the pending job/action before releasing the Agent State lock.
        # Relay/Studio polling must never run inside the shared-state lease.
        result: dict[str, Any]
        async with agent_state_external_io(f"discover:{tool_name}"):
            deadline = asyncio.get_running_loop().time() + DISCOVERY_WAIT_SECONDS
            while asyncio.get_running_loop().time() < deadline:
                await refresh_agent_state_for_io(f"discover_poll:{tool_name}")
                job = store.jobs.get(request_id)
                if job is None:
                    result = {"request_id": request_id, "tool": tool_name, "status": "error", "error": {"message": "discovery job state was refreshed before completion"}}
                    break
                if job.status in {"completed", "error"}:
                    if job.status == "completed":
                        collect_refs(job.result)
                    result = job.model_dump(mode="json")
                    break
                await asyncio.sleep(0.25)
            else:
                await refresh_agent_state_for_io(f"discover_timeout:{tool_name}")
                job = store.jobs.get(request_id)
                result = job.model_dump(mode="json") if job is not None else {"request_id": request_id, "tool": tool_name, "status": "error", "error": {"message": "discovery job state was refreshed before timeout"}}
        if action is not None:
            action["pending_external_io"] = False
        logger.warning(
            "AGENT_DISCOVER_RESULT request_id=%s tool=%s action_id=%s status=%s",
            request_id, tool_name, (action or {}).get("action_id", "none"), result.get("status", "unknown"),
        )
        return result

    def result_data(result: Any) -> Any:
        if not isinstance(result, dict):
            return None
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured.get("data", structured)
        for item in result.get("content", []):
            text_value = item.get("text") if isinstance(item, dict) else None
            if isinstance(text_value, str):
                try:
                    decoded = json.loads(text_value)
                    if isinstance(decoded, dict):
                        return decoded.get("data", decoded)
                except (ValueError, TypeError):
                    pass
        return None

    def property_schema_from_metadata(metadata: dict[str, Any] | None, current: Any = None) -> dict[str, Any] | None:
        metadata = metadata or {}
        value = metadata.get("value", current)
        roblox_value_type = metadata.get("robloxType") or metadata.get("roblox_type")
        if not roblox_value_type and isinstance(value, dict):
            roblox_value_type = value.get("$type")
        if not isinstance(roblox_value_type, str) or roblox_value_type in {"nil", "Unreadable", "Unsupported", "Truncated"}:
            return None
        if roblox_value_type in {"boolean", "number", "string", "Instance"}:
            return {"type": {"boolean": "boolean", "number": "number", "string": "string"}.get(roblox_value_type, "object")}
        return typed_schema(roblox_value_type, metadata)

    def property_context(draft: dict[str, Any], object_path: list[Any]) -> dict[str, Any]:
        tool_name = draft.get("tool_name", "")
        arguments = draft.get("arguments", {})
        if tool_name in {"studio_set_properties", "set_properties", "properties"} and object_path and object_path[0] == "values":
            return {"ref": arguments.get("ref")}
        if tool_name in {"studio_create_instance", "create", "instance.create"} and object_path and object_path[0] == "properties":
            return {"class_name": arguments.get("class_name") or arguments.get("className")}
        if object_path and object_path[0] == "operations":
            try:
                index = int(object_path[1])
                operation = arguments.get("operations", [])[index]
                if isinstance(operation, dict):
                    operation_args = operation.get("args") if isinstance(operation.get("args"), dict) else operation
                    operation_name = operation.get("operation") or operation.get("kind") or operation.get("tool") or ""
                    if any(part in {"properties", "values"} for part in object_path[2:]):
                        if operation_name in {"create", "studio_create_instance", "instance.create"}:
                            return {"class_name": operation_args.get("class_name") or operation_args.get("className")}
                        if operation_name in {"set_properties", "studio_set_properties", "properties.set"}:
                            return {"ref": operation_args.get("ref") or operation_args.get("instance")}
            except (IndexError, TypeError, ValueError):
                pass
        return {}

    def schema_at_path(schema: dict[str, Any], path: list[Any]) -> dict[str, Any]:
        current = schema
        for part in path:
            properties = current.get("properties", {}) if isinstance(current, dict) else {}
            if isinstance(properties, dict) and str(part) in properties:
                current = properties[str(part)]
            elif isinstance(current, dict) and isinstance(current.get("additionalProperties"), dict):
                current = current["additionalProperties"]
            elif isinstance(current, dict) and isinstance(current.get("items"), dict):
                current = current["items"]
            else:
                break
        return current if isinstance(current, dict) else {}

    async def resolve_property_schemas(draft: dict[str, Any], object_path: list[Any], names: list[str], action: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
        if not names:
            return {}
        cache = draft.setdefault("property_metadata", {})
        missing_names = [name for name in names if name not in cache]
        hint_schema = schema_at_path(draft.get("schema", {}), object_path)
        hints = hint_schema.get("x-roblox-property-metadata") or hint_schema.get("x_roblox_property_metadata") or {}
        if isinstance(hints, dict):
            for name in missing_names:
                if isinstance(hints.get(name), dict):
                    cache[name] = copy.deepcopy(hints[name])
            missing_names = [name for name in missing_names if name not in cache]
        context = property_context(draft, object_path)
        live_discovery_attempted = False
        # Keep audited contracts aside until live metadata has had a chance to
        # enrich them (especially EnumItem values).
        known_fallbacks: dict[str, dict[str, Any]] = {}
        for name in list(missing_names):
            known = known_property_schema(context.get("class_name"), name)
            if known:
                known_fallbacks[name] = known

        # A relay job may complete after the request that created it has
        # returned.  Reuse the newest completed runtime result for the exact
        # class/property contract before issuing another delayed discovery.
        # This is a server-side result cache, not editor/session reuse, and it
        # preserves only complete EnumItem metadata.
        if missing_names and context.get("class_name"):
            matching_jobs = []
            requested_names = [str(name) for name in missing_names]
            for job in store.jobs.values():
                if job.status != "completed" or job.tool != "studio_get_properties":
                    continue
                job_arguments = job.arguments if isinstance(job.arguments, dict) else {}
                job_names = job_arguments.get("names")
                if job_arguments.get("class_name") != context["class_name"] or job_names != requested_names:
                    continue
                matching_jobs.append(job)
            for job in reversed(matching_jobs):
                data = result_data(job.result)
                metadata = data.get("propertyMetadata") or data.get("property_metadata") if isinstance(data, dict) else {}
                if not isinstance(metadata, dict):
                    continue
                for name in list(missing_names):
                    if name not in metadata:
                        continue
                    merged = merge_property_metadata(cache.get(name), metadata[name])
                    if enum_metadata_incomplete(merged):
                        continue
                    cache[name] = merged
                missing_names = [name for name in missing_names if name not in cache or enum_metadata_incomplete(cache.get(name))]
                if not missing_names:
                    break
        live_metadata_needed = any(
            name not in known_fallbacks or enum_metadata_incomplete(known_fallbacks.get(name))
            for name in missing_names
        )
        if missing_names and live_metadata_needed and (context.get("ref") is not None or context.get("class_name")):
            tool_name = "studio_get_properties" if "studio_get_properties" in catalog_tools() else "properties"
            request: dict[str, Any] = {"names": missing_names}
            if context.get("ref") is not None:
                request["ref"] = context["ref"]
            else:
                request["class_name"] = context["class_name"]
            live_discovery_attempted = tool_name in catalog_tools()
            job = await discover(tool_name, request, action) if live_discovery_attempted else None
            data = result_data(job.get("result")) if isinstance(job, dict) and job.get("status") == "completed" else None
            if isinstance(data, dict):
                metadata = data.get("propertyMetadata") or data.get("property_metadata") or {}
                values = data.get("properties") or {}
                for name in missing_names:
                    if name in metadata:
                        merged = merge_property_metadata(cache.get(name), metadata[name])
                        if enum_metadata_incomplete(merged):
                            cache.pop(name, None)
                        else:
                            cache[name] = merged
                    elif name in values:
                        cache[name] = merge_property_metadata(cache.get(name), {"value": values[name]})
            missing_names = [name for name in missing_names if name not in cache or enum_metadata_incomplete(cache.get(name))]
            if missing_names and context.get("class_name") and tool_name == "studio_get_properties" and "studio_find_instances" in catalog_tools():
                sample_requests = [
                    {"query": "", "class_name": context["class_name"], "limit": 1},
                    {"query": "", "limit": 1},
                ]
                for sample_request in sample_requests:
                    sample_job = await discover("studio_find_instances", sample_request, action)
                    samples = result_data(sample_job.get("result")) if isinstance(sample_job, dict) and sample_job.get("status") == "completed" else None
                    if not (isinstance(samples, list) and samples and isinstance(samples[0], dict)):
                        continue
                    sample = samples[0]
                    # The bridge distinguishes an issued id from a path.  Do
                    # not send the display ref as a bare string, which would
                    # be parsed as a path by the MCP server.
                    sample_ref = {"id": sample["ref"]} if isinstance(sample.get("ref"), str) else None
                    if sample_ref is None and isinstance(sample.get("id"), str):
                        sample_ref = {"id": sample["id"]}
                    if sample_ref is None and isinstance(sample.get("path"), (str, list)):
                        sample_ref = {"path": sample["path"]}
                    if sample_ref is None:
                        continue
                    sample_properties = await discover("studio_get_properties", {"ref": sample_ref, "names": missing_names}, action)
                    sample_data = result_data(sample_properties.get("result")) if isinstance(sample_properties, dict) and sample_properties.get("status") == "completed" else None
                    if isinstance(sample_data, dict):
                        metadata = sample_data.get("propertyMetadata") or sample_data.get("property_metadata") or {}
                        values = sample_data.get("properties") or {}
                        for name in missing_names:
                            if name in metadata:
                                merged = merge_property_metadata(cache.get(name), metadata[name])
                                if enum_metadata_incomplete(merged):
                                    cache.pop(name, None)
                                else:
                                    cache[name] = merged
                            elif name in values:
                                cache[name] = merge_property_metadata(cache.get(name), {"value": values[name]})
                    if any(name in cache for name in missing_names):
                        break
        # Use only audited contracts as a deterministic offline fallback.  A
        # live bridge response above always wins and supplies enum members.
        for name, known in known_fallbacks.items():
            if name not in cache and not (live_discovery_attempted and enum_metadata_incomplete(known)):
                cache[name] = copy.deepcopy(known)
        result: dict[str, dict[str, Any]] = {}
        for name in names:
            metadata = cache.get(name)
            if metadata is None and not live_discovery_attempted:
                metadata = known_fallbacks.get(name)
            typed = property_schema_from_metadata(metadata)
            if typed:
                result[name] = typed
        return result

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
        context = lifecycle_context()
        heartbeat = store.heartbeat.model_dump(mode="json") if store.heartbeat else {}
        backend = agent_state_status() if agent_state_status else {"mode": "memory", "shared": False, "connected": True}
        backend_text = escape(json.dumps(backend, ensure_ascii=False, sort_keys=True))
        body = f"<h1>Agent Status</h1><p>local_client_online: {str(store.online()).lower()}</p><p>mcp_connected: {str(bool(heartbeat.get('mcp_connected') and store.online())).lower()}</p><p>studio_connected: {str(current_studio_connected()).lower()}</p><p>tool_count: {store.catalog.tool_count}</p><p>AGENT_STATE_BACKEND: <code>{escape(str(backend.get('mode')))}</code></p><p>AGENT_STATE_SHARED: <code>{str(bool(backend.get('shared'))).lower()}</code></p><p>AGENT_STATE_CONNECTED: <code>{str(bool(backend.get('connected'))).lower()}</code></p><p>AGENT_STATE_ROUNDTRIP: <code>{str(backend.get('roundtrip')).lower()}</code></p><p>AGENT_STATE_STATUS: <code>{backend_text}</code></p><p>STORE_ID: <code>{escape(context['store_id'])}</code></p><p>PROCESS_ID: <code>{escape(context['process_id'])}</code></p><p>DEPLOY_COMMIT: <code>{escape(DEPLOY_COMMIT)}</code></p><p>RENDER_INSTANCE_ID: <code>{escape(RENDER_INSTANCE_ID)}</code></p><p>AGENT_PROTOCOL_VERSION: <code>{escape(AGENT_PROTOCOL_VERSION)}</code></p><p>{href('/agent', 'Agent Home')} {href('/read/health', 'Live Health')}</p>"
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
        purge_drafts()
        tool = tool_or_404(tool_name)
        draft_id = "d_" + uuid4().hex[:16]
        created_at = datetime.now(timezone.utc)
        expires_at = (created_at.timestamp() + DRAFT_TTL_SECONDS)
        store.drafts[draft_id] = {"draft_id": draft_id, "revision": 0, "tool_name": tool_name, "schema": tool.get("inputSchema", {}), "arguments": {}, "created_at": created_at.isoformat(), "last_access": created_at.isoformat(), "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(), "status": "draft", "executed": False, "request_id": None, "execution_token": None}
        view_id = create_agent_view(store.drafts[draft_id])
        return agent_redirect(f"/agent/view/{view_id}")

    @app.get("/agent/view/{view_id}", response_class=HTMLResponse)
    async def agent_view(view_id: str):
        purge_drafts()
        view = store.views.get(view_id)
        if not view:
            return expired_state("view", view_id, "missing")
        touch_view(view)
        draft = draft_or_404(view["draft_id"])
        fields = []
        for name, schema in view["schema"].get("properties", {}).items():
            value = view["arguments_snapshot"].get(name, "<missing>")
            fields.append(f"<div class='card'><strong>{escape(name)}</strong> ({escape(schema_type(schema))})<br>value: <code>{escape(str(value))}</code><br>{snapshot_links(view, name, schema)}</div>")
        prepare = view_action_link(view, "prepare", {}, "Prepare Execution") if view["ready"] else "Complete required arguments first"
        body = f"<h1>Invocation View</h1><p>VIEW_ID: <code>{escape(view_id)}</code></p><p>DRAFT_ID: <code>{escape(view['draft_id'])}</code></p><p>DRAFT_REVISION: {view['revision']}</p><p>STATE: {escape('ready' if view['ready'] else 'draft')}</p><p>TOOL: {escape(view['tool_name'])}</p><p class='missing'>Missing required: {escape(', '.join(view['missing_arguments']) or 'none')}</p>{''.join(fields)}<h2>Arguments snapshot</h2><pre>{escape(json.dumps(view['arguments_snapshot'], ensure_ascii=False, indent=2))}</pre><p>{prepare} {href('/agent/tools', 'Tools')} {href('/agent', 'Agent Home')}</p>"
        return agent_page("Invocation View", body)

    @app.get("/agent/action/{action_id}", response_class=HTMLResponse)
    async def agent_action(action_id: str, request: Request):
        purge_drafts()
        request_info = request_context(request)
        action = store.actions.get(action_id)
        if not action:
            return missing_action(action_id)
        context = lifecycle_context()
        diagnostic = backend_context()
        draft_exists = action.get("draft_id") in store.drafts
        view_exists = not action.get("view_id") or action.get("view_id") in store.views
        owner_exists = draft_exists and view_exists
        logger.warning(
            "ACTION_LOOKUP action_id=%s exists=true reason=FOUND redis_key=%s state_key=%s redis_ttl=%s deserialize_status=ok draft_exists=%s view_exists=%s owner_exists=%s consumed=%s resulting_url=%s namespace=%s backend_identity_hash=%s redis_db=%s request_id=%s store_id=%s process_id=%s instance_id=%s process=%s render_instance=%s action_count=%s draft_id=%s view_id=%s expected_revision=%s method=%s user_agent=%s referer=%s",
            action_id,
            diagnostic["state_key"], diagnostic["state_key"],
            getattr(store, "agent_state_observed_ttl", "unknown"),
            draft_exists, view_exists, owner_exists, action.get("consumed"), action.get("resulting_url") or "none",
            diagnostic["namespace"], diagnostic["backend_identity_hash"], diagnostic["redis_db"], request_info["request_id"],
            context["store_id"], context["process_id"], context["instance_id"], context["process_id"], context["instance_id"],
            len(store.actions), action.get("draft_id"), action.get("view_id", "none"), action.get("expected_revision"),
            request_info["method"], request_info["user_agent"], request_info["referer"],
        )
        if action.get("consumed"):
            target = action.get("resulting_url")
            logger.info("ACTION_REPLAY action_id=%s operation=%s resulting_url=%s method=%s request_id=%s user_agent=%s referer=%s", action_id, action.get("operation"), target or "none", request_info["method"], request_info["request_id"], request_info["user_agent"], request_info["referer"])
            return agent_redirect(target) if target else agent_page("Action complete", "<h1>Action already consumed</h1>")
        if action.get("pending_external_io"):
            pending_job = store.jobs.get(action.get("discovery_request_id"))
            if pending_job is not None and pending_job.status in {"pending", "running"}:
                return agent_page(
                    "Discovery pending",
                    f"<h1>Discovery pending</h1><p>request_id: <code>{escape(str(pending_job.request_id))}</code></p>"
                    f"<p>{href('/agent/action/' + quote(action_id, safe=''), 'Refresh')}</p>",
                )
            # A completed job is safe to replay through the normal resolver;
            # a missing job is recovered by creating one durable replacement.
            action["pending_external_io"] = False
        try:
            draft = draft_or_404(action["draft_id"])
        except HTTPException as exc:
            if exc.status_code == 404:
                return expired_state("draft", str(action.get("draft_id")), "action owner missing")
            raise
        if action["operation"] not in {"execute_prepared", "refresh_result"} and int(action["expected_revision"]) != int(draft.get("revision", 0)):
            return stale_page(action, draft)
        arguments_hash_before = arguments_hash(draft)
        operation = action["operation"]; payload = action["payload"]
        if operation == "open_string":
            view_id = payload.get("view_id") or create_agent_view(draft)
            target = f"/agent/string-view/{view_id}/{quote(payload['name'], safe='')}"
        elif operation == "open_picker":
            view_id = payload.get("view_id") or create_agent_view(draft)
            target = f"/agent/picker-view/{view_id}/{quote(payload['name'], safe='')}"
        elif operation == "open_editor":
            source_view = store.views.get(payload.get("view_id"))
            if not source_view:
                return expired_state("view", str(payload.get("view_id")), "action source missing")
            editor_path = list(payload["path"])
            editor_schema = payload.get("schema")
            property_schemas = {}
            try:
                source_value = path_get(source_view["arguments_snapshot"], editor_path) if editor_path else source_view["arguments_snapshot"]
            except (KeyError, IndexError, TypeError):
                source_value = default_for_schema(editor_schema or {})
            typed_container = isinstance(editor_schema, dict) and roblox_type(editor_schema) in {
                "Vector2", "Vector3", "Color3", "CFrame", "UDim", "UDim2", "NumberRange", "BrickColor",
            }
            if isinstance(source_value, dict) and isinstance(editor_schema, dict) and schema_type(editor_schema) == "object" and not typed_container:
                property_schemas = copy.deepcopy(payload.get("property_schemas") or {})
                unresolved_names = []
                for key in source_value:
                    property_schema = property_schemas.get(str(key))
                    if not isinstance(property_schema, dict):
                        unresolved_names.append(str(key))
                        continue
                    if roblox_type(property_schema) == "EnumItem" and not (property_schema.get("enum_values") or property_schema.get("enumValues")):
                        unresolved_names.append(str(key))
                if unresolved_names:
                    property_schemas.update(await resolve_property_schemas(draft, editor_path, unresolved_names, action))
            target = create_editor(source_view, editor_path, payload.get("kind", roblox_type(editor_schema or {}) or "value"), editor_schema, property_schemas, payload.get("parent_schema"))
        elif operation == "editor_open_key":
            editor = store.editors.get(payload.get("editor_id"))
            if not editor:
                return expired_state("editor", str(payload.get("editor_id")), "action editor missing")
            source_view = store.views.get(editor["view_id"])
            target = create_key_editor(source_view, editor["path"], payload.get("parent_schema") or editor.get("schema"), editor.get("property_schemas"))
        elif operation in {"editor_append_key", "editor_backspace_key", "editor_clear_key"}:
            editor = store.editors.get(payload.get("editor_id"))
            if not editor:
                return expired_state("editor", str(payload.get("editor_id")), "action editor missing")
            value = str(editor["value_snapshot"])
            if operation == "editor_append_key": value += payload["character"]
            elif operation == "editor_backspace_key": value = value[:-1]
            else: value = ""
            cloned_view = store.views.get(editor["view_id"])
            target = create_key_editor(cloned_view, editor["path"], editor.get("parent_schema"), editor.get("property_schemas"))
            new_editor_id = target.rsplit("/", 1)[-1]
            store.editors[new_editor_id]["value_snapshot"] = value
        elif operation == "editor_finish_key":
            editor = store.editors.get(payload.get("editor_id"))
            if not editor:
                return expired_state("editor", str(payload.get("editor_id")), "action editor missing")
            key = str(editor["value_snapshot"])
            if not key:
                target = create_editor(store.views[editor["view_id"]], editor["path"], "object", editor.get("parent_schema"), editor.get("property_schemas"))
            else:
                path_set(draft["arguments"], editor["path"] + [key], None)
                property_schemas = await resolve_property_schemas(draft, editor["path"], [key], action)
                if key in property_schemas:
                    path_set(draft["arguments"], editor["path"] + [key], default_for_schema(property_schemas[key]))
                advance_revision(draft, operation, action, arguments_hash_before)
                next_view = create_agent_view(draft)
                target = create_editor(store.views[next_view], editor["path"], "object", editor.get("parent_schema") or {"type": "object", "additionalProperties": True}, {**editor.get("property_schemas", {}), **property_schemas})
        elif operation == "editor_initialize_value":
            editor = store.editors.get(payload.get("editor_id"))
            if not editor:
                raise HTTPException(404, "Editor not found")
            value_type = payload.get("value_type")
            if value_type not in {"string", "number", "integer", "boolean", "object", "array"}:
                raise HTTPException(400, "Unsupported value type")
            defaults = {"string": "", "number": 0, "integer": 0, "boolean": False, "object": {}, "array": []}
            path_set(draft["arguments"], editor["path"], defaults[value_type])
            advance_revision(draft, operation, action, arguments_hash_before)
            next_view = create_agent_view(draft)
            target = create_editor(store.views[next_view], editor["path"], value_type, {"type": value_type, "items": {"type": "object"}} if value_type == "array" else {"type": value_type, "additionalProperties": True} if value_type == "object" else {"type": value_type})
        elif operation.startswith("editor_"):
            editor = store.editors.get(payload.get("editor_id"))
            if not editor:
                raise HTTPException(404, "Editor not found")
            path = list(editor["path"])
            if path:
                try:
                    value = path_get(draft["arguments"], path)
                except (KeyError, IndexError, TypeError):
                    value = default_for_schema(editor["schema"])
                    path_set(draft["arguments"], path, value)
            else:
                value = draft["arguments"]
            typ = schema_type(editor["schema"])
            mutated = True
            if operation == "editor_set_value":
                path_set(draft["arguments"], path, copy.deepcopy(payload.get("value")))
            elif operation == "editor_append_string":
                path_set(draft["arguments"], path, str(value) + payload["character"])
            elif operation == "editor_append_number":
                path_set(draft["arguments"], path, str(value) + payload["token"])
            elif operation == "editor_backspace":
                path_set(draft["arguments"], path, str(value)[:-1])
            elif operation == "editor_clear_scalar":
                path_set(draft["arguments"], path, "")
            elif operation == "editor_finish_scalar":
                path_set(draft["arguments"], path, str(value))
            elif operation == "editor_finish_number":
                raw = str(value)
                try:
                    parsed = float(raw) if "." in raw else int(raw)
                except ValueError as exc:
                    raise HTTPException(400, "Enter a valid number before finishing") from exc
                minimum = editor["schema"].get("minimum")
                maximum = editor["schema"].get("maximum")
                if minimum is not None and parsed < minimum:
                    raise HTTPException(400, f"Value must be at least {minimum}")
                if maximum is not None and parsed > maximum:
                    raise HTTPException(400, f"Value must be at most {maximum}")
                path_set(draft["arguments"], path, parsed)
            elif operation == "editor_add_item":
                if not isinstance(value, list): raise HTTPException(400, "Editor value is not an array")
                value.append(copy.deepcopy(payload.get("value")))
            elif operation == "editor_remove_item":
                path_delete(draft["arguments"], path + [int(payload["index"])])
            elif operation == "editor_remove_last":
                if isinstance(value, list) and value: value.pop()
            elif operation == "editor_remove_key":
                path_delete(draft["arguments"], path + [payload["key"]])
            elif operation == "editor_clear_container":
                path_set(draft["arguments"], path, [] if typ == "array" else {})
            else:
                mutated = False
            if not mutated:
                raise HTTPException(400, "Unknown editor action")
            advance_revision(draft, operation, action, arguments_hash_before)
            next_view = create_agent_view(draft)
            if operation in {"editor_append_string", "editor_append_number", "editor_backspace", "editor_clear_scalar"}:
                target = create_editor(store.views[next_view], path, editor["kind"], editor["schema"])
            elif operation in {"editor_add_item", "editor_remove_item", "editor_remove_last", "editor_remove_key", "editor_clear_container"}:
                target = create_editor(store.views[next_view], path, editor["kind"], editor["schema"])
            elif len(path) > 1:
                parent_path = path[:-1]
                parent_value = path_get(store.views[next_view]["arguments_snapshot"], parent_path)
                parent_schema = editor.get("parent_schema") or ("array" if isinstance(parent_value, list) else {"type": "object"})
                parent_kind = roblox_type(parent_schema) or ("array" if isinstance(parent_value, list) else "object") if isinstance(parent_schema, dict) else parent_schema
                target = create_editor(store.views[next_view], parent_path, parent_kind, parent_schema if isinstance(parent_schema, dict) else None, editor.get("property_schemas"))
            else:
                target = f"/agent/view/{next_view}"
        elif operation == "prepare":
            arguments = copy.deepcopy(draft["arguments"])
            canonical = json.dumps({"tool_name": draft["tool_name"], "arguments": arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            prepare_id = "P_" + uuid4().hex[:18]
            store.prepared[prepare_id] = {"prepare_id": prepare_id, "draft_id": draft["draft_id"], "draft_revision": draft["revision"], "tool_name": draft["tool_name"], "arguments_snapshot": arguments, "arguments_hash": hashlib.sha256(canonical.encode()).hexdigest(), "created_at": now(), "executed": False, "request_id": None, "execute_action_id": None}
            target = f"/agent/prepared/{prepare_id}"
        elif operation == "execute_prepared":
            prepared = store.prepared.get(payload["prepare_id"])
            if not prepared:
                return expired_state("prepared", str(payload["prepare_id"]), "action prepared missing")
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
                return expired_state("result view", str(payload["result_view_id"]), "action result missing")
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
            advance_revision(draft, operation, action, arguments_hash_before)
            next_view = create_agent_view(draft)
            if operation in {"append_string", "backspace", "clear"} and schema_type(draft["schema"].get("properties", {}).get(payload.get("name"), {})) == "string":
                target = f"/agent/string-view/{next_view}/{quote(payload['name'], safe='')}"
            else:
                target = f"/agent/view/{next_view}"
        revision_after = int(draft.get("revision", 0))
        action["consumed"] = True; action["resulting_url"] = target
        logger.info(
            "ACTION_CONSUMED action_id=%s operation=%s expected_revision=%s revision_before=%s revision_after=%s arguments_hash_before=%s arguments_hash_after=%s method=%s request_id=%s user_agent=%s referer=%s resulting_url=%s",
            action_id, operation, action.get("expected_revision"),
            int(action.get("expected_revision", revision_after)), revision_after,
            arguments_hash_before, arguments_hash(draft), request_info["method"],
            request_info["request_id"], request_info["user_agent"], request_info["referer"], target,
        )
        return agent_redirect(target)

    @app.get("/agent/string-view/{view_id}/{name}", response_class=HTMLResponse)
    async def string_view(view_id: str, name: str):
        purge_drafts()
        view = store.views.get(view_id)
        if not view or name not in view["schema"].get("properties", {}):
            return expired_state("string view", view_id, "missing")
        touch_view(view)
        current = str(view["arguments_snapshot"].get(name, "")); base = f"/agent/string-view/{view_id}/{quote(name, safe='')}"
        links = " ".join(view_action_link(view, "append_string", {"name": name, "character": char}, f"Append {char if char != ' ' else 'space'}") for token, char in STRING_CHARACTERS)
        actions = " ".join([view_action_link(view, "backspace", {"name": name}, "Backspace"), view_action_link(view, "clear", {"name": name}, "Clear"), view_action_link(view, "finish_string", {"name": name}, "Finish"), href(f"/agent/view/{view_id}", "Back to Draft")])
        body = f"<h1>String Composer</h1><p>VIEW_ID: <code>{escape(view_id)}</code></p><p>DRAFT_ID: <code>{escape(view['draft_id'])}</code></p><p>DRAFT_REVISION: {view['revision']}</p><p>ARGUMENT: <code>{escape(name)}</code></p><p>CURRENT VALUE: <code>{escape(current)}</code></p><p>{links}</p><p>{actions}</p>"
        return agent_page("String Composer", body)

    @app.get("/agent/picker-view/{view_id}/{name}", response_class=HTMLResponse)
    async def picker_view(view_id: str, name: str):
        purge_drafts()
        view = store.views.get(view_id)
        if not view:
            return expired_state("picker", view_id, "missing")
        touch_view(view)
        items = []
        for candidate in view.get("recent_refs", [])[-50:]:
            candidate_url = view_action_link(view, "select_instance", {"name": name, "candidate": candidate}, "__candidate__")
            label = str(candidate.get("name", candidate.get("ref", "candidate")))
            if candidate.get("className"): label += f" ({candidate['className']})"
            items.append(f"<li>{candidate_url.replace('>__candidate__</a>', '>' + escape(label) + '</a>')} — {escape(str(candidate.get('displayPath', candidate.get('ref', ''))))}</li>")
        body = f"<h1>Roblox Instance Picker</h1><p>VIEW_ID: <code>{escape(view_id)}</code></p><p>DRAFT_ID: <code>{escape(view['draft_id'])}</code></p><p>DRAFT_REVISION: {view['revision']}</p><ul>{''.join(items) or '<li>No recent references yet. Run a read recipe first.</li>'}</ul>{href('/agent/view/' + view_id, 'Back to Draft')}"
        return agent_page("Roblox Instance Picker", body)

    @app.get("/agent/editor/{editor_id}", response_class=HTMLResponse)
    async def editor_view(editor_id: str):
        purge_drafts()
        editor = store.editors.get(editor_id)
        if not editor:
            return expired_state("editor", editor_id, "missing")
        view = store.views.get(editor.get("view_id"))
        if view:
            touch_view(view)
        return render_editor(editor)

    @app.get("/agent/prepared/{prepare_id}", response_class=HTMLResponse)
    async def prepared_view(prepare_id: str):
        purge_drafts()
        prepared = store.prepared.get(prepare_id)
        if not prepared:
            return expired_state("prepared", prepare_id, "missing")
        draft = store.drafts.get(prepared.get("draft_id"))
        if draft:
            draft["last_access"] = now()
        if not prepared.get("execute_action_id"):
            action_id = "A_" + uuid4().hex[:18]
            register_action({"action_id": action_id, "draft_id": prepared["draft_id"], "prepared_id": prepare_id, "expected_revision": prepared["draft_revision"], "operation": "execute_prepared", "payload": {"prepare_id": prepare_id}, "created_at": now(), "consumed": False, "resulting_url": None})
            prepared["execute_action_id"] = action_id
        execute_href = href(f"/agent/action/{prepared['execute_action_id']}", "Execute now") if not prepared.get("executed") else href(f"/agent/result-view/{prepared['result_view_id']}", "Open Result")
        body = f"<h1>Prepared Invocation</h1><p>PREPARE_ID: <code>{escape(prepare_id)}</code></p><p>DRAFT_ID: <code>{escape(prepared['draft_id'])}</code></p><p>DRAFT_REVISION: {prepared['draft_revision']}</p><p>STATE: {escape('executed' if prepared.get('executed') else 'prepared')}</p><p>TOOL: {escape(prepared['tool_name'])}</p><p>ARGUMENTS_SHA256: <code>{prepared['arguments_hash']}</code></p><pre>{escape(json.dumps(prepared['arguments_snapshot'], ensure_ascii=False, indent=2))}</pre><p>{execute_href} {href('/agent', 'Agent Home')}</p>"
        return agent_page("Prepared Invocation", body)

    @app.get("/agent/result-view/{result_view_id}", response_class=HTMLResponse)
    async def result_view(result_view_id: str):
        purge_drafts()
        snapshot = store.result_views.get(result_view_id)
        if not snapshot:
            return expired_state("result view", result_view_id, "missing")
        draft = store.drafts.get(snapshot.get("draft_id"))
        if draft:
            draft["last_access"] = now()
        if snapshot["status"] in {"pending", "running"}:
            action_id = snapshot.get("refresh_action_id")
            if not action_id:
                action_id = "A_" + uuid4().hex[:18]
                register_action({"action_id": action_id, "draft_id": snapshot["draft_id"], "result_view_id": result_view_id, "expected_revision": snapshot["draft_revision"], "operation": "refresh_result", "payload": {"result_view_id": result_view_id}, "created_at": now(), "consumed": False, "resulting_url": None})
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
