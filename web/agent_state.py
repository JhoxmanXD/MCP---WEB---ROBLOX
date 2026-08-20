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
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


AGENT_STATE_SCHEMA_VERSION = "agent-state-v1"
AGENT_STATE_DEFAULT_NAMESPACE = "mcp-web:agent:immutable-v1"
logger = logging.getLogger("mcp-web.agent_state")


_active_request: ContextVar[Any | None] = ContextVar("agent_state_request", default=None)


def _lock_context(store: Any, request_context: dict[str, Any] | None) -> dict[str, str]:
    request_context = request_context or {}
    action_id = str(request_context.get("action_id") or "none")
    action = getattr(store, "actions", {}).get(action_id) if action_id != "none" else None
    return {
        "request_id": str(request_context.get("request_id") or "unknown"),
        "action_id": action_id,
        "operation": str((action or {}).get("operation") or request_context.get("operation") or "unknown"),
        "process": str(os.getpid()),
        "instance": str(os.getenv("RENDER_INSTANCE_ID", "unknown")),
    }


def _lock_log(level: int, event: str, context: dict[str, str], wait_ms: int = 0, held_ms: int = 0, reason: str = "request") -> None:
    logger.log(
        level,
        "%s request_id=%s action_id=%s operation=%s process=%s instance=%s wait_ms=%s held_ms=%s reason=%s",
        event, context["request_id"], context["action_id"], context["operation"],
        context["process"], context["instance"], wait_ms, held_ms, reason,
    )


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


class _AgentStateRequest:
    """A request lease that can be released while waiting on external I/O."""

    def __init__(self, backend: "AgentStateStore", store: Any, request_context: dict[str, Any] | None) -> None:
        self.backend = backend
        self.store = store
        self.context = _lock_context(store, request_context)
        self.suspended = False
        self.lock_started: float | None = None

    async def suspend(self, reason: str) -> None:
        if self.suspended:
            return
        await self.backend._suspend(self, reason)
        self.suspended = True

    async def resume(self, reason: str) -> None:
        if not self.suspended:
            return
        await self.backend._resume(self, reason)
        self.suspended = False

    async def refresh(self, reason: str) -> None:
        await self.backend._refresh(self, reason)


@asynccontextmanager
async def agent_state_external_io(reason: str = "external_io") -> AsyncIterator[None]:
    """Run an external wait without holding the shared Agent State lock."""
    request = _active_request.get()
    if request is None:
        yield
        return
    await request.suspend(reason)
    try:
        yield
    finally:
        # If the caller was cancelled while waiting, the durable pending state
        # is already safe and the lock is already released.  A normal return
        # reacquires the lease so the route can finish its mutation atomically.
        if request.suspended:
            try:
                await request.resume(reason)
            except asyncio.CancelledError:
                # Do not turn a client disconnect into a leaked Redis lease.
                request.suspended = True


async def refresh_agent_state_for_io(reason: str = "external_io_poll") -> None:
    request = _active_request.get()
    if request is not None and request.suspended:
        await request.refresh(reason)


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
    async def request(self, store: Any, request_context: dict[str, Any] | None = None) -> AsyncIterator[None]:
        raise NotImplementedError

    async def _suspend(self, request: _AgentStateRequest, reason: str) -> None:
        return None

    async def _resume(self, request: _AgentStateRequest, reason: str) -> None:
        return None

    async def _refresh(self, request: _AgentStateRequest, reason: str) -> None:
        return None


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
    async def request(self, store: Any, request_context: dict[str, Any] | None = None) -> AsyncIterator[None]:
        _clear_publication_audit(store)
        request = _AgentStateRequest(self, store, request_context)
        token = _active_request.set(request)
        try:
            yield
        finally:
            _active_request.reset(token)


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
    async def request(self, store: Any, request_context: dict[str, Any] | None = None) -> AsyncIterator[None]:
        request = _AgentStateRequest(self, store, request_context)
        await self._lock.acquire()
        entered = False
        token = None
        try:
            _clear_publication_audit(store)
            request.version = self._load_into(store)
            request.lock_started = time.monotonic()
            _lock_log(logging.WARNING, "AGENT_LOCK_ACQUIRED", request.context, reason="request")
            token = _active_request.set(request)
            entered = True
            yield
            if entered and not request.suspended:
                state = bounded_agent_state(store.export_agent_state())
                _verify_published_actions(state, set(getattr(store, "pending_agent_action_ids", set())))
                self._save_from(store, request.version)
        finally:
            if not request.suspended:
                if request.lock_started is not None:
                    held_ms = int((time.monotonic() - request.lock_started) * 1000)
                    _lock_log(logging.WARNING, "AGENT_LOCK_RELEASED", request.context, held_ms=held_ms, reason="request")
                self._lock.release()
            if token is not None:
                _active_request.reset(token)

    async def _suspend(self, request: _AgentStateRequest, reason: str) -> None:
        state = bounded_agent_state(request.store.export_agent_state())
        _verify_published_actions(state, set(getattr(request.store, "pending_agent_action_ids", set())))
        self._save_from(request.store, request.version)
        held_ms = int((time.monotonic() - request.lock_started) * 1000)
        _lock_log(logging.WARNING, "AGENT_LOCK_RELEASED", request.context, held_ms=held_ms, reason=reason)
        request.lock_started = None
        self._lock.release()

    async def _resume(self, request: _AgentStateRequest, reason: str) -> None:
        started = time.monotonic()
        _lock_log(logging.WARNING, "AGENT_LOCK_WAIT", request.context, reason=reason)
        await self._lock.acquire()
        try:
            request.version = self._load_into(request.store)
            request.lock_started = time.monotonic()
            _lock_log(logging.WARNING, "AGENT_LOCK_ACQUIRED", request.context, wait_ms=int((time.monotonic() - started) * 1000), reason=reason)
        except BaseException:
            self._lock.release()
            raise

    async def _refresh(self, request: _AgentStateRequest, reason: str) -> None:
        async with self._lock:
            self._load_into(request.store)

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
        # Redis provides cross-process exclusion.  This local FIFO gate keeps
        # requests from the same Render worker from repeatedly overtaking a
        # waiter while Redis round-trips are slow.
        self._process_lock = asyncio.Lock()
        self._process_owner = "none"

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

    async def _acquire(self, redis: Any, context: dict[str, str], reason: str = "request") -> tuple[str, int]:
        token = uuid4().hex
        started = time.monotonic()
        _lock_log(logging.WARNING, "AGENT_LOCK_WAIT", context, reason=reason)
        process_gate_acquired = False
        lease_acquired = False
        try:
            await self._process_lock.acquire()
            process_gate_acquired = True
            deadline = time.monotonic() + float(os.getenv("AGENT_STATE_LOCK_WAIT_SECONDS", "5"))
            while time.monotonic() < deadline:
                if await redis.set(self.lock_key, token, nx=True, ex=self.lock_seconds):
                    wait_ms = int((time.monotonic() - started) * 1000)
                    self._process_owner = f"{context['action_id']}/{context['operation']}/process-{context['process']}"
                    lease_acquired = True
                    _lock_log(logging.WARNING, "AGENT_LOCK_ACQUIRED", context, wait_ms=wait_ms, reason=reason)
                    return token, wait_ms
                await asyncio.sleep(0.1)
            lock_ttl = "unknown"
            try:
                lock_ttl = str(await redis.ttl(self.lock_key))
            except Exception:
                pass
            wait_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "AGENT_LOCK_TIMEOUT request_id=%s action_id=%s operation=%s process=%s instance=%s wait_ms=%s held_ms=0 reason=%s local_owner=%s redis_lock_ttl=%s",
                context["request_id"], context["action_id"], context["operation"], context["process"], context["instance"],
                wait_ms, reason, self._process_owner, lock_ttl,
            )
            raise AgentStateBackendUnavailable("timed out acquiring shared Agent state lock")
        finally:
            if process_gate_acquired and not lease_acquired:
                self._process_lock.release()

    async def _release(self, redis: Any, token: str) -> None:
        script = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
        try:
            await redis.eval(script, 1, self.lock_key, token)
        except Exception:
            # The lease expires automatically; never mask the route result.
            pass
        finally:
            if self._process_lock.locked():
                self._process_owner = "none"
                self._process_lock.release()

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
                logger.warning(
                    "ACTION_PERSISTED action_id=%s persisted=true state_key_exists=true redis_ttl=%s drafts_count=%s views_count=%s actions_count=%s redis_key=%s state_key=%s record_path=%s/%s namespace=%s backend_identity_hash=%s redis_db=%s process=%s render_instance=%s process_id=%s instance_id=%s store_id=%s",
                    action_id, redis_ttl, len(persisted["state"].get("drafts", {})), len(persisted["state"].get("views", {})), len(persisted["state"].get("actions", {})),
                    key_for_action(self.namespace, action_id).redis_key,
                    key_for_action(self.namespace, action_id).redis_key,
                    *key_for_action(self.namespace, action_id).record_path, self.namespace,
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
    async def request(self, store: Any, request_context: dict[str, Any] | None = None) -> AsyncIterator[None]:
        redis = await self._client()
        request = _AgentStateRequest(self, store, request_context)
        token, _wait_ms = await self._acquire(redis, request.context)
        request.redis = redis
        request.token = token
        request.version = None
        request.lock_started = time.monotonic()
        entered = False
        context_token = None
        try:
            _clear_publication_audit(store)
            request.version = await self._load(redis, store)
            context_token = _active_request.set(request)
            entered = True
            yield
        finally:
            try:
                if entered and not request.suspended:
                    await self._save(redis, store, request.version)
            finally:
                if not request.suspended:
                    if request.lock_started is not None:
                        held_ms = int((time.monotonic() - request.lock_started) * 1000)
                        _lock_log(logging.WARNING, "AGENT_LOCK_RELEASED", request.context, held_ms=held_ms, reason="request")
                    await self._release(redis, request.token)
                if context_token is not None:
                    _active_request.reset(context_token)

    async def _suspend(self, request: _AgentStateRequest, reason: str) -> None:
        await self._save(request.redis, request.store, request.version)
        held_ms = int((time.monotonic() - request.lock_started) * 1000)
        _lock_log(logging.WARNING, "AGENT_LOCK_RELEASED", request.context, held_ms=held_ms, reason=reason)
        await self._release(request.redis, request.token)
        request.lock_started = None

    async def _resume(self, request: _AgentStateRequest, reason: str) -> None:
        token = None
        try:
            token, wait_ms = await self._acquire(request.redis, request.context, reason=reason)
            request.token = token
            request.version = await self._load(request.redis, request.store)
            request.lock_started = time.monotonic()
            _lock_log(logging.WARNING, "AGENT_LOCK_ACQUIRED", request.context, wait_ms=wait_ms, reason=reason)
        except BaseException:
            if token is not None:
                await self._release(request.redis, token)
            raise

    async def _refresh(self, request: _AgentStateRequest, reason: str) -> None:
        token, wait_ms = await self._acquire(request.redis, request.context, reason=reason)
        started = time.monotonic()
        try:
            await self._load(request.redis, request.store)
        finally:
            held_ms = int((time.monotonic() - started) * 1000)
            _lock_log(logging.WARNING, "AGENT_LOCK_RELEASED", request.context, held_ms=held_ms, reason=reason)
            await self._release(request.redis, token)


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
