"""Shared Agent Mode state with a memory development backend.

Agent links are opaque, mutable state. They must not depend on which web
worker receives the next request, so production can use a Redis-compatible
store with a namespace, JSON schema version, TTL, and a short distributed
lock. No pickle/eval or application objects cross the storage boundary.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from uuid import uuid4


AGENT_STATE_SCHEMA_VERSION = "agent-state-v1"
AGENT_STATE_DEFAULT_NAMESPACE = "mcp-web:agent:immutable-v1"


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
        }

    @asynccontextmanager
    async def request(self, store: Any) -> AsyncIterator[None]:
        yield


class InMemorySharedAgentStateStore(AgentStateStore):
    """Small shared backend used by tests to exercise worker boundaries."""

    mode = "memory-test-shared"
    shared = True

    def __init__(self, namespace: str = "test") -> None:
        self.namespace = namespace
        self._document: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "shared": True,
            "connected": True,
            "schema_version": AGENT_STATE_SCHEMA_VERSION,
            "namespace": self.namespace,
        }

    @asynccontextmanager
    async def request(self, store: Any) -> AsyncIterator[None]:
        async with self._lock:
            version = self._load_into(store)
            try:
                yield
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
        self.key = f"{namespace}:state"
        self.lock_key = f"{namespace}:lock"
        self._redis: Any = None
        self._last_error: str | None = None
        self._configured = bool(url)

    def status(self) -> dict[str, Any]:
        result = {
            "mode": self.mode,
            "shared": True,
            "connected": self._redis is not None and self._last_error is None,
            "configured": self._configured,
            "schema_version": AGENT_STATE_SCHEMA_VERSION,
            "namespace": self.namespace,
            "ttl_seconds": self.ttl_seconds,
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
            return 0
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
