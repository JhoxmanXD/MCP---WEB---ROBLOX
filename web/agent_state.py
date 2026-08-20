"""Shared Agent Mode state with a memory development backend.

Agent links are opaque, mutable state. They must not depend on which web
worker receives the next request, so production can use a Redis-compatible
store with a namespace, JSON schema version, TTL, and a short distributed
lock. No pickle/eval or application objects cross the storage boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


AGENT_STATE_SCHEMA_VERSION = "agent-state-v1"
AGENT_STATE_DEFAULT_NAMESPACE = "mcp-web:agent:immutable-v1"
logger = logging.getLogger("mcp-web.agent_state")


@dataclass(frozen=True)
class StateLocator:
    """The physical Redis key plus the JSON record path inside it."""

    redis_key: str
    record_path: tuple[str, str]


def state_key_for(namespace: str) -> str:
    return f"{namespace}:state"


def _locator(namespace: str, collection: str, record_id: str) -> StateLocator:
    return StateLocator(state_key_for(namespace), (collection, str(record_id)))


def key_for_draft(namespace: str, draft_id: str) -> StateLocator:
    return _locator(namespace, "drafts", draft_id)


def key_for_view(namespace: str, view_id: str) -> StateLocator:
    return _locator(namespace, "views", view_id)


def key_for_action(namespace: str, action_id: str) -> StateLocator:
    return _locator(namespace, "actions", action_id)


def key_for_editor(namespace: str, editor_id: str) -> StateLocator:
    return _locator(namespace, "editors", editor_id)


def key_for_prepared(namespace: str, prepare_id: str) -> StateLocator:
    return _locator(namespace, "prepared", prepare_id)


def key_for_result(namespace: str, result_view_id: str) -> StateLocator:
    return _locator(namespace, "result_views", result_view_id)


class AgentStateBackendError(RuntimeError):
    """Base error for fail-closed Agent state handling."""


class AgentStateBackendUnavailable(AgentStateBackendError):
    pass


class AgentStateConflict(AgentStateBackendError):
    pass


class AgentStateIncompatible(AgentStateBackendError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_records(value: dict[str, Any], limit: int) -> dict[str, Any]:
    if len(value) <= limit:
        return value
    ordered = sorted(
        value.items(),
        key=lambda item: str(item[1].get("last_access", item[1].get("created_at", "")))
        if isinstance(item[1], dict) else "",
    )
    return dict(ordered[-limit:])


def bounded_agent_state(state: dict[str, Any]) -> dict[str, Any]:
    """Keep one namespace bounded without dropping recent active records."""
    limits = {
        "drafts": int(os.getenv("AGENT_STATE_MAX_DRAFTS", "256")),
        "views": int(os.getenv("AGENT_STATE_MAX_VIEWS", "1024")),
        "actions": int(os.getenv("AGENT_STATE_MAX_ACTIONS", "8192")),
        "prepared": int(os.getenv("AGENT_STATE_MAX_PREPARED", "256")),
        "result_views": int(os.getenv("AGENT_STATE_MAX_RESULT_VIEWS", "256")),
        "editors": int(os.getenv("AGENT_STATE_MAX_EDITORS", "1024")),
    }
    result = dict(state)
    drafts = result.get("drafts", {})
    if isinstance(drafts, dict) and len(drafts) > limits["drafts"]:
        drafts = _bounded_records(drafts, max(1, limits["drafts"]))
        result["drafts"] = drafts
    retained_drafts = set(drafts) if isinstance(drafts, dict) else set()
    for name, limit in limits.items():
        value = result.get(name, {})
        if isinstance(value, dict):
            if name != "drafts" and retained_drafts:
                value = {
                    key: item for key, item in value.items()
                    if not isinstance(item, dict) or not item.get("draft_id") or item.get("draft_id") in retained_drafts
                }
            result[name] = _bounded_records(value, max(1, limit))
    result["recent_refs"] = list(result.get("recent_refs", []))[-100:]
    result["recent_string_values"] = list(result.get("recent_string_values", []))[-32:]
    return result


def _verify_published_actions(state: dict[str, Any], action_ids: set[str]) -> None:
    """Fail before response publication if rendered links are not durable."""
    actions = state.get("actions", {})
    drafts = state.get("drafts", {})
    views = state.get("views", {})
    editors = state.get("editors", {})
    prepared = state.get("prepared", {})
    result_views = state.get("result_views", {})
    for action_id in action_ids:
        action = actions.get(action_id)
        if not isinstance(action, dict):
            raise AgentStateBackendUnavailable(f"action {action_id} was not durably persisted")
        required = ("action_id", "draft_id", "expected_revision", "operation", "payload", "consumed", "created_at", "expires_at", "state_schema_version")
        if any(field not in action for field in required):
            raise AgentStateIncompatible(f"action {action_id} record is incomplete")
        if action["state_schema_version"] != AGENT_STATE_SCHEMA_VERSION:
            raise AgentStateIncompatible(f"action {action_id} schema version is incompatible")
        draft_id = action.get("draft_id")
        if draft_id not in drafts:
            raise AgentStateBackendUnavailable(f"action {action_id} owner draft is not durable")
        owner_view = action.get("view_id")
        if owner_view and owner_view not in views:
            raise AgentStateBackendUnavailable(f"action {action_id} owner view is not durable")
        owner_editor = action.get("editor_id")
        if owner_editor and owner_editor not in editors:
            raise AgentStateBackendUnavailable(f"action {action_id} owner editor is not durable")
        owner_prepared = action.get("prepared_id") or action.get("payload", {}).get("prepare_id")
        if owner_prepared and owner_prepared not in prepared:
            raise AgentStateBackendUnavailable(f"action {action_id} owner prepared state is not durable")
        owner_result = action.get("result_view_id") or action.get("payload", {}).get("result_view_id")
        if owner_result and owner_result not in result_views:
            raise AgentStateBackendUnavailable(f"action {action_id} owner result view is not durable")


def _clear_publication_audit(store: Any) -> None:
    pending = getattr(store, "pending_agent_action_ids", None)
    if pending is not None:
        pending.clear()


class AgentStateStore:
    mode = "unknown"
    shared = False

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "shared": self.shared,
            "connected": False,
            "schema_version": AGENT_STATE_SCHEMA_VERSION,
        }

    async def roundtrip(self) -> bool:
        return bool(self.status().get("connected"))

    async def startup_diagnostics(self, phase: str) -> dict[str, Any]:
        return {"phase": phase, "state_key_exists": None, "ttl": None, "drafts": None, "views": None, "actions": None}

    @asynccontextmanager
    async def request(self, store: Any) -> AsyncIterator[None]:
        raise NotImplementedError


class InMemoryAgentStateStore(AgentStateStore):
    mode = "memory"
    shared = False

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "shared": self.shared,
            "connected": True,
            "schema_version": AGENT_STATE_SCHEMA_VERSION,
            "namespace": "process-local",
            "state_key": "process-local",
            "roundtrip": True,
        }

    @asynccontextmanager
    async def request(self, store: Any) -> AsyncIterator[None]:
        _clear_publication_audit(store)
        yield


class InMemorySharedAgentStateStore(AgentStateStore):
    """Small shared backend used by tests to exercise worker boundaries."""

    mode = "memory-test-shared"
    shared = True

    def __init__(self, namespace: str = "test") -> None:
        self.namespace = namespace
        self._document: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._roundtrip = True

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "shared": True,
            "connected": True,
            "schema_version": AGENT_STATE_SCHEMA_VERSION,
            "namespace": self.namespace,
            "state_key": f"{self.namespace}:state",
            "roundtrip": self._roundtrip,
        }

    @asynccontextmanager
    async def request(self, store: Any) -> AsyncIterator[None]:
        async with self._lock:
            _clear_publication_audit(store)
            version = self._load_into(store)
            try:
                yield
                state = bounded_agent_state(store.export_agent_state())
                _verify_published_actions(state, set(getattr(store, "pending_agent_action_ids", set())))
                self._save_from(store, version)
            finally:
                pass

    def _load_into(self, store: Any) -> int:
        if self._document is None:
            return 0
        _validate_document(self._document)
        _import_into_store(store, self._document["state"])
        return int(self._document["revision"])

    def _save_from(self, store: Any, version: int) -> None:
        self._document = _new_document(bounded_agent_state(store.export_agent_state()), version + 1, self.namespace)


class SharedAgentStateStore(AgentStateStore):
    mode = "redis"
    shared = True

    def __init__(self, url: str | None, namespace: str, ttl_seconds: int, lock_seconds: int) -> None:
        self.url = url
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds
        self.lock_seconds = lock_seconds
        self.key = state_key_for(namespace)
        self.lock_key = f"{namespace}:lock"
        self._redis: Any = None
        self._last_error: str | None = None
        self._configured = bool(url)
        self._roundtrip: bool | None = None

    def status(self) -> dict[str, Any]:
        identity_hash, database = self._backend_identity()
        result = {
            "mode": self.mode,
            "shared": True,
            "connected": self._redis is not None and self._last_error is None,
            "configured": self._configured,
            "schema_version": AGENT_STATE_SCHEMA_VERSION,
            "namespace": self.namespace,
            "state_key": self.key,
            "backend_identity_hash": identity_hash,
            "redis_db": database,
            "ttl_seconds": self.ttl_seconds,
            "roundtrip": self._roundtrip,
        }
        if self._last_error:
            result["error"] = self._last_error
        return result

    async def _client(self) -> Any:
        if not self.url:
            raise AgentStateBackendUnavailable("AGENT_STATE_URL is not configured")
        if self._redis is None:
            try:
                from redis.asyncio import Redis
            except ImportError as exc:
                raise AgentStateBackendUnavailable("redis package is not installed") from exc
            self._redis = Redis.from_url(self.url, decode_responses=True)
        try:
            await self._redis.ping()
        except Exception as exc:  # Redis client errors vary by compatible provider.
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise AgentStateBackendUnavailable("shared Agent state backend is unreachable") from exc
        self._last_error = None
        return self._redis

    async def roundtrip(self) -> bool:
        try:
            redis = await self._client()
            key = f"{self.namespace}:diagnostic:{uuid4().hex}"
            token = uuid4().hex
            await redis.set(key, token, ex=10)
            result = await redis.get(key)
            await redis.delete(key)
            self._roundtrip = result == token
        except AgentStateBackendError:
            self._roundtrip = False
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._roundtrip = False
        return bool(self._roundtrip)

    def _backend_identity(self) -> tuple[str, str]:
        if not self.url:
            return "unknown", "unknown"
        parsed = urlsplit(self.url)
        safe_identity = f"{parsed.scheme}://{parsed.hostname or 'unknown'}:{parsed.port or 'default'}{parsed.path or ''}"
        identity_hash = hashlib.sha256(safe_identity.encode()).hexdigest()[:16]
        database = "unknown"
        path_part = (parsed.path or "").strip("/")
        if path_part.isdigit():
            database = path_part
        else:
            query_db = parse_qs(parsed.query).get("db", [])
            if query_db and query_db[0].isdigit():
                database = query_db[0]
        return identity_hash, database

    async def startup_diagnostics(self, phase: str) -> dict[str, Any]:
        identity_hash, database = self._backend_identity()
        values: dict[str, Any] = {
            "phase": phase,
            "backend_identity_hash": identity_hash,
            "database": database,
            "namespace": self.namespace,
            "state_key": self.key,
            "state_key_exists": False,
            "ttl": -2,
            "drafts": 0,
            "views": 0,
            "actions": 0,
        }
        try:
            redis = await self._client()
            raw = await redis.get(self.key)
            values["state_key_exists"] = bool(raw)
            values["ttl"] = await redis.ttl(self.key)
            if raw:
                document = json.loads(raw)
                _validate_document(document)
                state = document["state"]
                values["drafts"] = len(state.get("drafts", {}))
                values["views"] = len(state.get("views", {}))
                values["actions"] = len(state.get("actions", {}))
        except Exception as exc:
            values["error"] = type(exc).__name__
        logger.warning(
            "AGENT_STATE_STARTUP phase=%s namespace=%s backend_identity_hash=%s database=%s state_key=%s state_key_exists_%s=%s ttl_%s=%s drafts_%s=%s views_%s=%s actions_%s=%s error=%s process_id=%s instance_id=%s",
            values["phase"], values["namespace"], values["backend_identity_hash"], values["database"],
            values["state_key"], values["phase"], values["state_key_exists"], values["phase"], values["ttl"],
            values["phase"], values["drafts"], values["phase"], values["views"], values["phase"], values["actions"],
            values.get("error", "none"), os.getpid(),
            os.getenv("RENDER_INSTANCE_ID", "unknown"),
        )
        return values

    async def _acquire(self, redis: Any) -> str:
        token = uuid4().hex
        deadline = time.monotonic() + float(os.getenv("AGENT_STATE_LOCK_WAIT_SECONDS", "5"))
        while time.monotonic() < deadline:
            if await redis.set(self.lock_key, token, nx=True, ex=self.lock_seconds):
                return token
            await asyncio.sleep(0.1)
        raise AgentStateBackendUnavailable("timed out acquiring shared Agent state lock")

    async def _release(self, redis: Any, token: str) -> None:
        script = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
        try:
            await redis.eval(script, 1, self.lock_key, token)
        except Exception:
            # The lease expires automatically; never mask the route result.
            pass

    async def _load(self, redis: Any, store: Any) -> int:
        raw = await redis.get(self.key)
        if not raw:
            store.agent_state_observed_ttl = -2
            return 0
        redis_ttl = await redis.ttl(self.key)
        store.agent_state_observed_ttl = redis_ttl
        if redis_ttl <= 0:
            raise AgentStateBackendUnavailable(f"shared state key has invalid TTL {redis_ttl}")
        try:
            document = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AgentStateIncompatible("shared Agent state is not valid JSON") from exc
        _validate_document(document)
        _import_into_store(store, document["state"])
        return int(document["revision"])

    async def _save(self, redis: Any, store: Any, expected_revision: int) -> None:
        # The distributed lease serializes requests; WATCH is the second line
        # of defence if a provider or operator changes the namespace directly.
        try:
            async with redis.pipeline(transaction=True) as pipe:
                await pipe.watch(self.key)
                raw = await pipe.get(self.key)
                current_revision = 0
                if raw:
                    current = json.loads(raw)
                    _validate_document(current)
                    current_revision = int(current["revision"])
                if current_revision != expected_revision:
                    raise AgentStateConflict("shared Agent state changed during request")
                document = _new_document(bounded_agent_state(store.export_agent_state()), expected_revision + 1, self.namespace)
                pipe.multi()
                pipe.set(self.key, json.dumps(document, ensure_ascii=False, separators=(",", ":")), ex=self.ttl_seconds)
                await pipe.execute()
            persisted_raw = await redis.get(self.key)
            persisted = json.loads(persisted_raw) if persisted_raw else None
            _validate_document(persisted)
            _verify_published_actions(persisted["state"], set(getattr(store, "pending_agent_action_ids", set())))
            redis_ttl = await redis.ttl(self.key)
            if redis_ttl <= 0:
                raise AgentStateBackendUnavailable(f"shared state key has invalid TTL {redis_ttl}")
            for action_id in getattr(store, "pending_agent_action_ids", set()):
                logger.info(
                    "ACTION_PERSISTED action_id=%s redis_key=%s state_key=%s record_path=%s/%s redis_ttl=%s persisted=true namespace=%s backend_identity_hash=%s redis_db=%s process=%s render_instance=%s process_id=%s instance_id=%s store_id=%s",
                    action_id, key_for_action(self.namespace, action_id).redis_key,
                    key_for_action(self.namespace, action_id).redis_key,
                    *key_for_action(self.namespace, action_id).record_path, redis_ttl, self.namespace,
                    self._backend_identity()[0], self._backend_identity()[1], os.getpid(), os.getenv("RENDER_INSTANCE_ID", "unknown"),
                    os.getpid(), os.getenv("RENDER_INSTANCE_ID", "unknown"),
                    getattr(store, "server_instance_id", "unknown"),
                )
        except AgentStateBackendError:
            raise
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise AgentStateBackendUnavailable("shared Agent state write failed") from exc

    @asynccontextmanager
    async def request(self, store: Any) -> AsyncIterator[None]:
        redis = await self._client()
        token = await self._acquire(redis)
        try:
            _clear_publication_audit(store)
            revision = await self._load(redis, store)
            yield
            await self._save(redis, store, revision)
        finally:
            await self._release(redis, token)


def _new_document(state: dict[str, Any], revision: int, namespace: str) -> dict[str, Any]:
    return {
        "schema_version": AGENT_STATE_SCHEMA_VERSION,
        "namespace": namespace,
        "revision": revision,
        "updated_at": _utc_now(),
        "state": state,
    }


def _validate_document(document: Any) -> None:
    if not isinstance(document, dict) or document.get("schema_version") != AGENT_STATE_SCHEMA_VERSION:
        raise AgentStateIncompatible("shared Agent state schema version is incompatible")
    if not isinstance(document.get("revision"), int) or not isinstance(document.get("state"), dict):
        raise AgentStateIncompatible("shared Agent state document is malformed")


def _import_into_store(store: Any, state: dict[str, Any]) -> None:
    try:
        store.import_agent_state(state)
    except (TypeError, ValueError) as exc:
        raise AgentStateIncompatible("shared Agent state collections are malformed") from exc


def build_agent_state_backend() -> AgentStateStore:
    mode = os.getenv("AGENT_STATE_BACKEND", "memory").strip().lower()
    if mode in {"memory", "local", "dev"}:
        return InMemoryAgentStateStore()
    if mode in {"redis", "redis-compatible", "shared"}:
        return SharedAgentStateStore(
            os.getenv("AGENT_STATE_URL"),
            os.getenv("AGENT_STATE_NAMESPACE", AGENT_STATE_DEFAULT_NAMESPACE),
            max(300, int(os.getenv("AGENT_STATE_TTL_SECONDS", "3600"))),
            max(30, int(os.getenv("AGENT_STATE_LOCK_SECONDS", "60"))),
        )
    return SharedAgentStateStore(None, os.getenv("AGENT_STATE_NAMESPACE", AGENT_STATE_DEFAULT_NAMESPACE), 3600, 60)


# Compatibility aliases keep the implementation readable at call sites while
# exposing the contract names used by the deployment documentation.
AgentStateBackend = AgentStateStore
MemoryAgentStateBackend = InMemoryAgentStateStore
InMemorySharedAgentStateBackend = InMemorySharedAgentStateStore
RedisAgentStateBackend = SharedAgentStateStore
