import asyncio
import hashlib
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from web.app import app, store
from web.agent_state import AgentStateBackendUnavailable, InMemorySharedAgentStateBackend, RedisAgentStateBackend, key_for_action, key_for_draft, key_for_editor, key_for_prepared, key_for_result, key_for_view
from web.store import MemoryStore


client = TestClient(app)


class _ForensicRedis:
    """Small async Redis double for production backend lifecycle tests."""

    def __init__(self):
        self.values = {}
        self.expiries = {}
        self.operations = []

    def _expired(self, key):
        expiry = self.expiries.get(key)
        if expiry is not None and expiry <= time.monotonic():
            self.values.pop(key, None)
            self.expiries.pop(key, None)
            return True
        return False

    async def ping(self):
        return True

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values and not self._expired(key):
            return False
        self.values[key] = value
        if ex is not None:
            self.expiries[key] = time.monotonic() + ex
        else:
            self.expiries.pop(key, None)
        self.operations.append(("set", key))
        return True

    async def get(self, key):
        if self._expired(key):
            return None
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)
        self.expiries.pop(key, None)
        self.operations.append(("delete", key))
        return 1

    async def ttl(self, key):
        if self._expired(key):
            return -2
        expiry = self.expiries.get(key)
        return max(1, int(expiry - time.monotonic())) if expiry is not None else -1

    async def eval(self, _script, _key_count, key, token):
        if await self.get(key) == token:
            await self.delete(key)
        return 1

    def pipeline(self, transaction=True):
        assert transaction is True
        return _ForensicPipeline(self)


class _ForensicPipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def watch(self, _key):
        return None

    async def get(self, key):
        return await self.redis.get(key)

    def multi(self):
        return None

    def set(self, key, value, ex=None):
        self.commands.append((key, value, ex))

    async def execute(self):
        for key, value, ex in self.commands:
            await self.redis.set(key, value, ex=ex)
        self.commands.clear()
        return True


def setup_function():
    store.jobs.clear(); store.queue.clear(); store.states.clear(); store.latest = None
    store.recent_refs.clear(); store.recent_string_values.clear(); store.drafts.clear(); store.views.clear(); store.actions.clear(); store.prepared.clear(); store.result_views.clear(); store.editors.clear()
    store.pending_agent_action_ids.clear()
    store.catalog.tools = [{"name": "read_tool", "description": "read", "inputSchema": {}}]
    store.catalog.tool_count = 1


def test_health_and_no_cache():
    response = client.get("/api/v1/health.json")
    assert response.status_code == 200
    assert response.json()["service"] == "MCP-WEB"
    assert response.json()["agent_state_backend"] == "memory"
    assert response.json()["agent_state_backend_connected"] is True
    assert response.json()["agent_state_roundtrip"] is True
    assert response.headers["cache-control"].startswith("no-store")


def test_shared_agent_state_survives_restart_and_alternating_clients():
    backend = InMemorySharedAgentStateBackend("test:alternating")
    first_worker = MemoryStore()
    restarted_worker = MemoryStore()

    async def scenario():
        async with backend.request(first_worker):
            first_worker.drafts["d_shared"] = {"draft_id": "d_shared", "revision": 0, "created_at": "now"}
            first_worker.views["V_shared"] = {"view_id": "V_shared", "draft_id": "d_shared", "revision": 0}
            first_worker.actions["A_shared"] = {"action_id": "A_shared", "draft_id": "d_shared", "view_id": "V_shared", "consumed": False}
            first_worker.editors["E_shared"] = {"editor_id": "E_shared", "draft_id": "d_shared", "view_id": "V_shared", "value_snapshot": ""}
            first_worker.prepared["P_shared"] = {"prepare_id": "P_shared", "draft_id": "d_shared", "draft_revision": 0, "executed": False}
            first_worker.result_views["R_shared"] = {"result_view_id": "R_shared", "draft_id": "d_shared", "status": "pending"}
            first_worker.recent_refs.append({"ref": "instance://shared"})

        async with backend.request(restarted_worker):
            assert "A_shared" in restarted_worker.actions
            assert "E_shared" in restarted_worker.editors
            assert "P_shared" in restarted_worker.prepared
            assert "R_shared" in restarted_worker.result_views
            assert restarted_worker.recent_refs[-1]["ref"] == "instance://shared"
            restarted_worker.actions["A_shared"]["consumed"] = True

        async with backend.request(first_worker):
            assert first_worker.actions["A_shared"]["consumed"] is True
            for index in range(82):
                first_worker.actions[f"A_{index}"] = {"action_id": f"A_{index}", "draft_id": "d_shared", "view_id": "V_shared", "created_at": str(index)}

        async with backend.request(restarted_worker):
            assert len(restarted_worker.actions) == 83
            assert restarted_worker.actions["A_81"]["action_id"] == "A_81"

    asyncio.run(scenario())


def test_redis_startup_roundtrip_isolated_from_production_state_key():
    namespace = "forensic:startup"
    backend = RedisAgentStateBackend("redis://forensic", namespace, 3600, 60)
    redis = _ForensicRedis()
    backend._redis = redis
    production_key = f"{namespace}:state"
    redis.values[production_key] = '{"sentinel":"must-survive"}'
    redis.expiries[production_key] = time.monotonic() + 3600
    before = redis.values[production_key]

    async def actual():
        assert await backend.roundtrip() is True

    asyncio.run(actual())

    assert redis.values[production_key] == before
    assert all(key != production_key for _operation, key in redis.operations)
    diagnostic_keys = {key for _operation, key in redis.operations}
    assert len(diagnostic_keys) == 1
    assert next(iter(diagnostic_keys)).startswith(f"{namespace}:diagnostic:")
    assert next(iter(diagnostic_keys)) not in redis.values


def test_redis_store_constructor_is_read_only():
    namespace = "forensic:constructor"
    redis = _ForensicRedis()
    redis.values[f"{namespace}:state"] = "preexisting"
    backend = RedisAgentStateBackend("redis://forensic", namespace, 3600, 60)

    assert backend._redis is None
    assert redis.operations == []
    assert redis.values[f"{namespace}:state"] == "preexisting"


def test_backend_identity_hash_is_stable_without_exposing_redis_secret():
    first = RedisAgentStateBackend("redis://:super-secret@redis.example:6379/4", "forensic:identity", 3600, 60)
    second = RedisAgentStateBackend("redis://:super-secret@redis.example:6379/4", "forensic:identity", 3600, 60)

    assert first._backend_identity() == second._backend_identity()
    identity_hash, database = first._backend_identity()
    assert len(identity_hash) == 16
    assert database == "4"
    assert "super-secret" not in identity_hash
    status = first.status()
    assert status["backend_identity_hash"] == identity_hash
    assert status["redis_db"] == "4"
    assert status["state_key"] == "forensic:identity:state"


def test_redis_restart_survival_runs_startup_hook_then_follows_action(caplog):
    caplog.set_level("WARNING", logger="mcp-web.agent_state")
    namespace = "forensic:restart"
    redis = _ForensicRedis()
    backend_a = RedisAgentStateBackend("redis://forensic", namespace, 3600, 60)
    backend_b = RedisAgentStateBackend("redis://forensic", namespace, 3600, 60)
    backend_a._redis = redis
    backend_b._redis = redis
    worker_a = MemoryStore()

    async def scenario():
        async with backend_a.request(worker_a):
            worker_a.drafts["d_restart"] = {"draft_id": "d_restart", "revision": 0}
            worker_a.views["V_restart"] = {"view_id": "V_restart", "draft_id": "d_restart", "revision": 0}
            worker_a.actions["A_restart"] = {
                "action_id": "A_restart", "draft_id": "d_restart", "view_id": "V_restart",
                "expected_revision": 0, "operation": "set_arg", "payload": {},
                "consumed": False, "created_at": "now", "expires_at": "2099-01-01T00:00:00+00:00",
                "state_schema_version": "agent-state-v1",
            }
            worker_a.pending_agent_action_ids.add("A_restart")

        state_key = backend_a.key
        state_before = redis.values[state_key]
        hash_before = hashlib.sha256(state_before.encode()).hexdigest()
        ttl_before = await redis.ttl(state_key)

        # This is the production lifespan hook. It must not touch :state.
        startup_before = await backend_b.startup_diagnostics("before")
        assert await backend_b.roundtrip() is True
        startup_after = await backend_b.startup_diagnostics("after")
        assert startup_before["state_key_exists"] is True
        assert startup_before["drafts"] == startup_after["drafts"] == 1
        assert startup_before["views"] == startup_after["views"] == 1
        assert startup_before["actions"] == startup_after["actions"] == 1
        assert startup_before["ttl"] > 0 and startup_after["ttl"] > 0
        worker_b = MemoryStore()
        revision_before_mutation = await backend_b._load(redis, worker_b)
        assert revision_before_mutation == 1
        assert state_key in redis.values
        assert await redis.ttl(state_key) > 0
        assert "d_restart" in worker_b.drafts
        assert "V_restart" in worker_b.views
        assert "A_restart" in worker_b.actions
        assert hashlib.sha256(redis.values[state_key].encode()).hexdigest() == hash_before

        async with backend_b.request(worker_b):
            worker_b.actions["A_restart"]["consumed"] = True

        worker_c = MemoryStore()
        await backend_b._load(redis, worker_c)
        assert worker_c.actions["A_restart"]["consumed"] is True
        assert ttl_before > 0
        assert "ACTION_PERSISTED action_id=A_restart persisted=true" in caplog.text
        assert "redis://" not in caplog.text

    asyncio.run(scenario())


def test_agent_lifecycle_events_are_warning_visible_without_secrets(caplog):
    caplog.set_level("WARNING", logger="mcp-web.agent")
    draft_page = _start_string_draft()
    action_url = _link(draft_page.text, "Set Part")
    action_id = action_url.rsplit("/", 1)[-1]

    response = client.get(action_url, follow_redirects=False)

    assert response.status_code == 303
    assert f"ACTION_CREATED action_id={action_id}" in caplog.text
    assert f"ACTION_LOOKUP action_id={action_id} exists=true" in caplog.text
    assert "backend_identity_hash=" in caplog.text
    assert "redis_db=" in caplog.text
    assert "namespace=" in caplog.text
    assert "state_key=" in caplog.text
    assert "redis://" not in caplog.text
    assert "super-secret" not in caplog.text


def test_redis_lock_and_cas_preserve_updates_from_two_clients():
    namespace = "forensic:cas"
    redis = _ForensicRedis()
    seed_backend = RedisAgentStateBackend("redis://forensic", namespace, 3600, 60)
    backend_a = RedisAgentStateBackend("redis://forensic", namespace, 3600, 60)
    backend_b = RedisAgentStateBackend("redis://forensic", namespace, 3600, 60)
    for backend in (seed_backend, backend_a, backend_b):
        backend._redis = redis
    seed = MemoryStore()

    async def scenario():
        async with seed_backend.request(seed):
            seed.drafts["d_cas"] = {"draft_id": "d_cas", "revision": 0}
            seed.actions["A_cas"] = {
                "action_id": "A_cas", "draft_id": "d_cas", "consumed": False,
                "created_at": "now", "expires_at": "2099-01-01T00:00:00+00:00",
                "state_schema_version": "agent-state-v1",
            }

        async def update(backend, marker):
            worker = MemoryStore()
            async with backend.request(worker):
                worker.actions["A_cas"].setdefault("markers", {})[marker] = True

        await asyncio.gather(update(backend_a, "client_a"), update(backend_b, "client_b"))
        final = MemoryStore()
        await seed_backend._load(redis, final)
        assert final.actions["A_cas"]["markers"] == {"client_a": True, "client_b": True}

    asyncio.run(scenario())


def test_all_agent_entity_locators_use_same_physical_namespace_key():
    namespace = "mcp-web:agent:immutable-v1"
    locators = [
        key_for_draft(namespace, "d_1"), key_for_view(namespace, "V_1"),
        key_for_action(namespace, "A_1"), key_for_editor(namespace, "E_1"),
        key_for_prepared(namespace, "P_1"), key_for_result(namespace, "R_1"),
    ]
    assert {locator.redis_key for locator in locators} == {f"{namespace}:state"}
    assert [locator.record_path for locator in locators] == [
        ("drafts", "d_1"), ("views", "V_1"), ("actions", "A_1"),
        ("editors", "E_1"), ("prepared", "P_1"), ("result_views", "R_1"),
    ]


def test_shared_publication_rejects_action_not_durable():
    backend = InMemorySharedAgentStateBackend("test:publication")
    worker = MemoryStore()

    async def actual():
        async with backend.request(worker):
            worker.drafts["d_publish"] = {"draft_id": "d_publish"}
            worker.views["V_publish"] = {"view_id": "V_publish", "draft_id": "d_publish"}
            worker.actions["A_publish"] = {"action_id": "A_publish", "draft_id": "d_publish", "view_id": "V_publish", "expected_revision": 0, "operation": "prepare", "payload": {}, "consumed": False, "created_at": "now", "expires_at": "2099-01-01T00:00:00+00:00", "state_schema_version": "agent-state-v1"}
            worker.pending_agent_action_ids.add("A_publish")
        with pytest.raises(AgentStateBackendUnavailable, match="A_missing"):
            async with backend.request(worker):
                worker.pending_agent_action_ids.add("A_missing")

    asyncio.run(actual())


def test_shared_http_view_publishes_resolvable_action_before_response(monkeypatch):
    import web.app as app_module

    backend = InMemorySharedAgentStateBackend("test:http-publication")
    monkeypatch.setattr(app_module, "agent_state_backend", backend)
    store.catalog.tools = [{"name": "read_tool", "description": "read", "inputSchema": {}}]
    store.catalog.tool_count = 1
    start = client.get("/agent/tool/read_tool/start", follow_redirects=False)
    assert start.status_code == 303
    view = client.get(start.headers["location"])
    action_id = re.search(r"/agent/action/(A_[a-f0-9]+)", view.text).group(1)
    action = client.get(f"/agent/action/{action_id}", follow_redirects=False)
    assert action.status_code == 303
    assert action.headers["location"].startswith("/agent/prepared/P_")


def test_shared_agent_state_serializes_one_shot_action_consumption():
    backend = InMemorySharedAgentStateBackend("test:one-shot")
    seed = MemoryStore()
    seed.actions["A_once"] = {"action_id": "A_once", "consumed": False}

    async def scenario():
        async with backend.request(seed):
            pass

        async def consume(worker):
            async with backend.request(worker):
                action = worker.actions["A_once"]
                if action["consumed"]:
                    return False
                action["consumed"] = True
                return True

        results = await asyncio.gather(consume(MemoryStore()), consume(MemoryStore()))
        assert sorted(results) == [False, True]

    asyncio.run(scenario())


def test_shared_agent_state_serializes_prepared_execution_once():
    backend = InMemorySharedAgentStateBackend("test:prepared-once")
    seed = MemoryStore()
    seed.prepared["P_once"] = {"prepare_id": "P_once", "executed": False, "request_id": None}

    async def scenario():
        async with backend.request(seed):
            pass

        async def execute(worker):
            async with backend.request(worker):
                prepared = worker.prepared["P_once"]
                if prepared["executed"]:
                    return prepared["request_id"]
                prepared["executed"] = True
                prepared["request_id"] = "WEB_AGENT_SHARED_ONCE"
                return prepared["request_id"]

        results = await asyncio.gather(execute(MemoryStore()), execute(MemoryStore()))
        assert results == ["WEB_AGENT_SHARED_ONCE", "WEB_AGENT_SHARED_ONCE"]

    asyncio.run(scenario())


def test_configured_shared_backend_fails_closed_without_url(monkeypatch):
    import web.app as app_module

    monkeypatch.setattr(app_module, "agent_state_backend", RedisAgentStateBackend(None, "test:missing", 7200, 60))
    response = client.get("/agent/status")
    assert response.status_code == 503
    assert "AGENT STATE BACKEND UNAVAILABLE" in response.text


def test_idempotent_call_poll_complete_result_and_state():
    first = client.get("/api/v1/call/read_tool?rid=A1&state=workspace&limit=3")
    second = client.get("/api/v1/call/read_tool?rid=A1&state=workspace&limit=3&nonce=x")
    assert first.json()["request_id"] == second.json()["request_id"] == "A1"
    assert len(store.jobs) == 1
    job = client.get("/api/v1/jobs/next").json()
    assert job["status"] == "running"
    client.post("/api/v1/jobs/A1/complete", json={"success": True, "result": {"ok": True}})
    assert client.get("/api/v1/result/A1.json").json()["status"] == "completed"
    assert client.get("/api/v1/state/workspace.json").json() == {"ok": True}


def test_unannounced_tool_rejected():
    assert client.get("/api/v1/call/unknown?rid=X").status_code == 400


def test_catalog_and_empty_poll():
    assert client.get("/api/v1/catalog.json").json()["tool_count"] == 1
    assert client.get("/api/v1/jobs/next").json() == {"job": None}


def test_catalog_generation_and_timestamp_change_on_republish():
    first = client.post("/api/v1/catalog", json={"tools": [{"name": "read_tool"}], "studio_connected": True}).json()
    second = client.post("/api/v1/catalog", json={"tools": [{"name": "read_tool"}], "studio_connected": True}).json()
    assert first["server_instance_id"] == second["server_instance_id"]
    assert second["catalog_generation"] == first["catalog_generation"] + 1
    assert second["updated_at"] != first["updated_at"]


def test_heartbeat_reports_catalog_presence():
    response = client.post("/api/v1/client/heartbeat", json={"client": "test", "tool_count": 1, "mcp_connected": True, "studio_connected": True})
    assert response.json()["catalog_present"] is True
    assert response.json()["catalog_tool_count"] == 1
    assert client.get("/api/v1/dashboard.json").json()["local_client_online"] is True


def test_heartbeat_reports_missing_catalog():
    store.catalog.tools = []
    store.catalog.tool_count = 0
    response = client.post("/api/v1/client/heartbeat", json={"client": "test", "tool_count": 1, "mcp_connected": True, "studio_connected": True})
    assert response.json()["catalog_present"] is False
    assert response.json()["catalog_tool_count"] == 0


def test_dashboard_marks_studio_offline_when_heartbeat_says_offline():
    client.post("/api/v1/client/heartbeat", json={"client": "test", "tool_count": 1, "mcp_connected": False, "studio_connected": False})
    dashboard = client.get("/api/v1/dashboard.json").json()
    assert dashboard["local_client_online"] is True
    assert dashboard["studio_connected"] is False


def test_navigable_read_routes_and_no_cache(monkeypatch):
    from web import app as app_module
    monkeypatch.setattr(app_module, "READ_WAIT_SECONDS", 0.01)
    store.catalog.tools.append({"name": "studio_list_sessions", "description": "list sessions"})
    store.catalog.tool_count = len(store.catalog.tools)
    home = client.get("/")
    assert "/read/health" in home.text
    assert "/read/catalog" in home.text
    assert "/read/sessions" in home.text
    assert "/read/latest" in home.text
    for path in ("/read/health", "/read/catalog", "/read/sessions", "/read/latest"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"].startswith("no-store")
    sessions = client.get("/read/sessions")
    assert "request_id:" in sessions.text
    assert "Refresh result" in sessions.text


def test_read_result_does_not_create_job():
    from web import app as app_module
    before = len(store.jobs)
    response = client.get("/read/result/UNKNOWN_READ_ID")
    assert response.status_code == 200
    assert len(store.jobs) == before


def test_agent_gateway_draft_prepare_and_one_shot_execute():
    store.catalog.tools = [{"name": "test_tool", "description": "test", "inputSchema": {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]}}]
    store.catalog.tool_count = 1
    home = client.get("/agent")
    assert home.status_code == 200 and "/agent/tools" in home.text
    assert client.get("/agent/tools").status_code == 200
    assert client.get("/agent/tool/test_tool").status_code == 200
    started = client.get("/agent/tool/test_tool/start", follow_redirects=False)
    assert started.status_code == 303
    draft_url = started.headers["location"]
    draft = client.get(draft_url)
    assert "Missing required: enabled" in draft.text
    set_value = client.get(_link(draft.text, "Set true"), follow_redirects=True)
    assert set_value.status_code == 200
    prepared = client.get(_link(set_value.text, "Prepare Execution"), follow_redirects=True)
    assert prepared.status_code == 200
    execute_link = _link(prepared.text, "Execute now")
    executed = client.get(execute_link, follow_redirects=False)
    assert executed.status_code == 303
    result_page = client.get(executed.headers["location"])
    request_id = re.search(r"REQUEST_ID: <code>([^<]+)", result_page.text).group(1)
    repeat = client.get(execute_link, follow_redirects=False)
    assert repeat.headers["location"] == executed.headers["location"]
    assert len([job for job in store.jobs.values() if job.request_id == request_id]) == 1


def test_agent_candidate_contract_keeps_structured_path_and_display_path():
    store.catalog.tools = [{"name": "studio_find_instances", "inputSchema": {"type": "object"}}]
    store.catalog.tool_count = 1
    result = {"ok": True, "data": [{"ref": "rbx:test:i_1", "path": "p.Foo.Bar", "name": "Bar", "className": "Part"}]}
    # The public helper is exercised through a completed discovery job.
    store.create_job(__import__("web.models", fromlist=["Job"]).Job(request_id="DISCOVERY", tool="studio_find_instances", arguments={}))
    store.complete("DISCOVERY", True, result)
    for value in result["data"]:
        candidate = {"ref": value["ref"], "path": ["p", "Foo", "Bar"], "displayPath": value["path"], "name": value["name"], "className": value["className"]}
        store.recent_refs.append(candidate)
    picker = client.get("/agent/tool/studio_find_instances")
    assert picker.status_code == 200
    assert store.recent_refs[-1]["path"] == ["p", "Foo", "Bar"]


def _link(html: str, label: str) -> str:
    match = re.search(r"<a href='([^']+)'>(?:" + re.escape(label) + r")</a>", html)
    assert match, f"missing link: {label}"
    return match.group(1)


def _links(html: str, label: str) -> list[str]:
    return [match.group(1) for match in re.finditer(r"<a href='([^']+)'>(?:" + re.escape(label) + r")</a>", html)]


def _start_string_draft():
    store.catalog.tools = [{
        "name": "studio_create_instance",
        "description": "create",
        "inputSchema": {
            "type": "object",
            "properties": {"class_name": {"type": "string"}, "name": {"type": "string"}},
            "required": ["class_name", "name"],
        },
    }]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/studio_create_instance")
    start = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    return client.get(start.headers["location"])


def _compose_via_visible_links(draft_page, value: str, argument: str = "name"):
    composer = client.get(_link(draft_page.text, f"Open String Composer ({argument})"))
    assert "String Composer" in composer.text
    for char in value:
        label = f"Append {char}" if char != " " else "Append space"
        composer = client.get(_link(composer.text, label), follow_redirects=True)
    finished = client.get(_link(composer.text, "Finish"), follow_redirects=True)
    assert f"<code>{value}</code>" in finished.text
    return finished


def test_exact_public_revision_trace_open_composer_is_not_a_mutation(caplog):
    caplog.set_level("INFO", logger="mcp-web.agent")
    store.catalog.tools = [{
        "name": "find",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query", "limit"],
        },
    }]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/find")
    start = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    view = client.get(start.headers["location"])
    view = client.get(_link(view.text, "Set Workspace"), follow_redirects=True)
    draft_id = re.search(r"DRAFT_ID: <code>(d_[^<]+)", view.text).group(1)
    assert store.drafts[draft_id]["revision"] == 1
    view = client.get(_link(view.text, "10"), follow_redirects=True)
    assert store.drafts[draft_id]["revision"] == 2
    assert "Set Workspace" not in view.text
    view_id = re.search(r"VIEW_ID: <code>(V_[^<]+)", view.text).group(1)
    store.actions["A_noop"] = {
        "action_id": "A_noop", "draft_id": draft_id, "view_id": view_id,
        "expected_revision": 2, "operation": "set_arg",
        "payload": {"name": "query", "value": "Workspace"}, "consumed": False,
        "resulting_url": None, "created_at": "now", "expires_at": store.drafts[draft_id]["expires_at"],
        "state_schema_version": "agent-state-v1",
    }
    client.get("/agent/action/A_noop", follow_redirects=False)
    assert store.drafts[draft_id]["revision"] == 2
    assert "DRAFT_REVISION_UNCHANGED" in caplog.text
    composer = client.get(_link(view.text, "Open String Composer (query)"), follow_redirects=True)
    assert store.drafts[draft_id]["revision"] == 2
    composer = client.get(_link(composer.text, "Append A"), follow_redirects=True)
    assert store.drafts[draft_id]["revision"] == 3
    assert store.drafts[draft_id]["arguments"] == {"query": "WorkspaceA", "limit": 10}


def test_string_composer_builds_case_sensitive_values_using_only_generated_links():
    first = _compose_via_visible_links(_start_string_draft(), "SOL_MCP_FINAL_TEST")
    assert "Open String Composer" in first.text
    second = _compose_via_visible_links(_start_string_draft(), "WebControlledPart")
    assert "WebControlledPart" in second.text


def test_string_composer_charset_and_edit_actions_are_navigable():
    draft = _start_string_draft()
    composer = client.get(_link(draft.text, "Open String Composer (name)"))
    for label in ["Append A", "Append B", "Append C", "Append 1", "Append _", "Append -", "Append 2", "Append .", "Append space"]:
        assert label in composer.text
    for label in ["Append A", "Append B", "Append C"]:
        composer = client.get(_link(composer.text, label), follow_redirects=True)
    composer = client.get(_link(composer.text, "Backspace"), follow_redirects=True)
    assert "CURRENT VALUE: <code>AB</code>" in composer.text
    composer = client.get(_link(composer.text, "Clear"), follow_redirects=True)
    assert "CURRENT VALUE: <code></code>" in composer.text
    finished = client.get(_link(composer.text, "Finish"), follow_redirects=True)
    assert "Complete required arguments first" in finished.text
    assert "Workspace" in finished.text and "Recent" not in finished.text


def test_live_view_actions_are_registered_owned_and_replayable(caplog):
    caplog.set_level("INFO", logger="mcp-web.agent")
    draft = _start_string_draft()
    action_url = _link(draft.text, "Open String Composer (name)")
    action_id = re.search(r"/agent/action/(A_[^']+)", action_url).group(1)
    assert action_id in store.actions
    action = store.actions[action_id]
    assert action["draft_id"] in store.drafts
    assert action["view_id"] in store.views
    assert action["expected_revision"] == store.views[action["view_id"]]["revision"]
    assert action["expires_at"] == store.views[action["view_id"]]["expires_at"]

    first = client.get(action_url, follow_redirects=False)
    assert first.status_code == 303 and "/agent/string-view/V_" in first.headers["location"]
    replay = client.get(action_url, follow_redirects=False)
    assert replay.status_code == 303
    assert replay.headers["location"] == first.headers["location"]
    assert "ACTION_CREATED" in caplog.text and "ACTION_LOOKUP" in caplog.text


def test_missing_agent_action_returns_expired_state_instead_of_plain_404():
    draft = _start_string_draft()
    action_url = _link(draft.text, "Open String Composer (name)")
    store.actions.clear()
    expired = client.get(action_url)
    assert expired.status_code == 410
    assert "AGENT STATE EXPIRED" in expired.text
    assert "Return to Tools" in expired.text


def test_view_and_actions_share_rolling_ttl_and_cleanup_together():
    draft = _start_string_draft()
    view_id = re.search(r"/agent/view/(V_[^']+)", str(draft.request.url)).group(1)
    view = store.views[view_id]
    action_url = _link(draft.text, "Open String Composer (name)")
    action_id = re.search(r"/agent/action/(A_[^']+)", action_url).group(1)
    assert store.actions[action_id]["expires_at"] == view["expires_at"]
    store.drafts[view["draft_id"]]["expires_at"] = "2000-01-01T00:00:00+00:00"
    assert client.get(f"/agent/view/{view_id}").status_code == 410
    assert view_id not in store.views and action_id not in store.actions


def test_long_link_only_string_workflow_has_no_action_404s():
    composer = client.get(_link(_start_string_draft().text, "Open String Composer (name)"))
    statuses = []
    for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRST":
        response = client.get(_link(composer.text, f"Append {char}"), follow_redirects=True)
        statuses.append(response.status_code)
        composer = response
    assert len(statuses) >= 80
    assert all(status == 200 for status in statuses)
    assert all("AGENT STATE EXPIRED" not in response.text for response in [composer])


def test_stale_prepare_action_is_rejected_without_mutating_or_preparing():
    store.catalog.tools = [{"name": "cache_tool", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "class_name": {"type": "string"}}, "required": ["query", "class_name"]}}]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/cache_tool")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    view = client.get(started.headers["location"])
    view = client.get(_links(view.text, "Set Workspace")[0], follow_redirects=True)
    view = client.get(_links(view.text, "Set Folder")[1], follow_redirects=True)
    old_prepare = _link(view.text, "Prepare Execution")
    current = client.get(_links(view.text, "Set Workspace")[0], follow_redirects=True)
    stale = client.get(old_prepare)
    assert "STALE DRAFT VIEW" in stale.text
    assert "Expected revision: 2" in stale.text and "Current revision: 3" in stale.text
    assert not store.prepared
    assert "Workspace" in current.text


def test_public_agent_case_preserves_query_and_prepared_snapshot_uses_only_opaque_links():
    store.catalog.tools = [{"name": "studio_find_instances", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "class_name": {"type": "string"}}, "required": ["query", "class_name"]}}]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/studio_find_instances")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    view = client.get(started.headers["location"])
    assert re.search(r"/agent/view/V_[^']+", started.headers["location"])
    assert "/agent/draft/" not in view.text and "/agent/action/A_" in view.text
    composer = client.get(_link(view.text, "Open String Composer (query)"))
    for char in "SOL_MCP":
        composer = client.get(_link(composer.text, f"Append {char}"), follow_redirects=True)
    view = client.get(_link(composer.text, "Finish"), follow_redirects=True)
    assert 'value: <code>SOL_MCP</code>' in view.text
    assert 'value: <code>&lt;missing&gt;</code>' in view.text
    prepared = client.get(_links(view.text, "Set Folder")[1], follow_redirects=True)
    prepared = client.get(_link(prepared.text, "Prepare Execution"), follow_redirects=True)
    assert '"query": "SOL_MCP"' in prepared.text or '&quot;query&quot;: &quot;SOL_MCP&quot;' in prepared.text
    assert '"class_name": "Folder"' in prepared.text or '&quot;class_name&quot;: &quot;Folder&quot;' in prepared.text
    assert "/agent/prepared/P_" in str(prepared.request.url)


def test_build_markers_are_visible_on_agent_and_health():
    agent_status = client.get("/agent/status")
    assert "DEPLOY_COMMIT:" in agent_status.text
    assert "RENDER_INSTANCE_ID:" in agent_status.text
    assert "AGENT_PROTOCOL_VERSION:" in agent_status.text
    assert "STORE_ID:" in agent_status.text and "PROCESS_ID:" in agent_status.text
    health = client.get("/api/v1/health.json").json()
    assert health["agent_protocol_version"] == "immutable-v1"


def test_immutable_redirects_have_strong_no_cache_headers():
    store.catalog.tools = [{"name": "redirect_tool", "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}}}]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/redirect_tool")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    assert started.status_code == 303 and "/agent/view/V_" in started.headers["location"]
    for header, expected in [("cache-control", "no-store"), ("pragma", "no-cache"), ("expires", "0"), ("cdn-cache-control", "no-store"), ("surrogate-control", "no-store")]:
        assert expected in started.headers[header].lower()
    view = client.get(started.headers["location"])
    action = client.get(_link(view.text, "Open String Composer (value)"), follow_redirects=False)
    assert action.status_code == 303 and "/agent/string-view/V_" in action.headers["location"]
    assert "no-store" in action.headers["cache-control"]


def test_view_freezes_prepare_and_recent_string_action_links():
    store.catalog.tools = [{"name": "stable_tool", "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}}]
    store.catalog.tool_count = 1
    store.recent_string_values[:] = ["OldRecent"]
    page = _start_string_draft()
    page = client.get(_link(page.text, "Open String Composer (name)"))
    first = page.text
    store.recent_string_values[:] = ["NewRecent"]
    assert client.get(page.request.url).text == first


def test_picker_view_freezes_candidates():
    store.catalog.tools = [{"name": "picker_tool", "inputSchema": {"type": "object", "properties": {"parent": {"type": "object", "additionalProperties": True}}}}]
    store.catalog.tool_count = 1
    store.recent_refs[:] = [{"ref": "rbx:one", "name": "One", "className": "Folder", "path": ["p", "One"]}]
    tool_page = client.get("/agent/tool/picker_tool")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    page = client.get(started.headers["location"])
    picker = client.get(_link(page.text, "Choose Roblox Instance"))
    first = picker.text
    store.recent_refs[:] = [{"ref": "rbx:two", "name": "Two", "className": "Part", "path": ["p", "Two"]}]
    assert client.get(picker.request.url).text == first


def test_recursive_object_editor_supports_open_properties():
    store.catalog.tools = [{"name": "object_tool", "inputSchema": {"type": "object", "properties": {"properties": {"type": "object", "additionalProperties": True}}, "required": ["properties"]}}]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/object_tool")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    view = client.get(started.headers["location"])
    editor = client.get(_link(view.text, "Edit object"))
    key = client.get(_link(editor.text, "Add field"))
    for char in "Size": key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    editor = client.get(_link(key.text, "Finish"), follow_redirects=True)
    value = client.get(_link(editor.text, "Edit Size"))
    value = client.get(_link(value.text, "Edit as number"), follow_redirects=True)
    value = client.get(_link(value.text, "Clear"), follow_redirects=True)
    for char in "0.25": value = client.get(_link(value.text, char), follow_redirects=True)
    editor = client.get(_link(value.text, "Finish"), follow_redirects=True)
    final_view = client.get(_link(editor.text, "Back to Draft"))
    assert '&quot;Size&quot;: 0.25' in final_view.text
    assert "/agent/draft/" not in editor.text


def test_recursive_array_object_editor_supports_two_items():
    store.catalog.tools = [{"name": "studio_batch", "inputSchema": {"type": "object", "properties": {"operations": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}, "required": ["operations"]}}]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/studio_batch")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    view = client.get(started.headers["location"])
    array = client.get(_link(view.text, "Edit array"))
    object_editor = client.get(_link(array.text, "Add item"), follow_redirects=True)
    object_editor = client.get(_link(object_editor.text, "Edit item 0"))
    key = client.get(_link(object_editor.text, "Add field"))
    for char in "tool": key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    object_editor = client.get(_link(key.text, "Finish"), follow_redirects=True)
    value = client.get(_link(object_editor.text, "Edit tool"))
    value = client.get(_link(value.text, "Edit as string"), follow_redirects=True)
    value = client.get(_link(value.text, "Clear"), follow_redirects=True)
    for char in "studio_find_instances": value = client.get(_link(value.text, f"Append {char}"), follow_redirects=True)
    object_editor = client.get(_link(value.text, "Finish"), follow_redirects=True)
    view = client.get(_link(object_editor.text, "Back to Draft"))
    array = client.get(_link(view.text, "Edit array"))
    array = client.get(_link(array.text, "Add item"), follow_redirects=True)
    final_view = client.get(_link(array.text, "Back to Draft"))
    assert '&quot;operations&quot;: [' in final_view.text and '&quot;tool&quot;: &quot;studio_find_instances&quot;' in final_view.text
    assert final_view.text.count("{}") >= 1


def test_real_blocking_tool_schemas_expose_generic_immutable_editors():
    store.catalog.tools = [
        {"name": "studio_create_instance", "inputSchema": {"type": "object", "properties": {"properties": {"additionalProperties": True, "type": "object"}}}},
        {"name": "studio_set_properties", "inputSchema": {"type": "object", "properties": {"values": {"additionalProperties": True, "type": "object"}}}},
        {"name": "studio_batch", "inputSchema": {"type": "object", "properties": {"operations": {"items": {"additionalProperties": True, "type": "object"}, "type": "array"}}}},
    ]
    store.catalog.tool_count = 3
    for name, label in [("studio_create_instance", "Edit object"), ("studio_set_properties", "Edit object"), ("studio_batch", "Edit array")]:
        tool_page = client.get(f"/agent/tool/{name}")
        started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
        view = client.get(started.headers["location"])
        assert label in view.text and "/agent/draft/" not in view.text


def test_typed_property_dispatch_exposes_vector3_color3_and_enum_editors():
    metadata = {
        "Size": {"robloxType": "Vector3"},
        "Position": {"robloxType": "Vector3"},
        "Color": {"robloxType": "Color3"},
        "Material": {"robloxType": "EnumItem", "enumType": "Material", "enumValues": [
            {"$type": "EnumItem", "enumType": "Material", "name": "Grass", "value": 128},
            {"$type": "EnumItem", "enumType": "Material", "name": "Rock", "value": 512},
        ]},
    }
    store.catalog.tools = [{"name": "studio_create_instance", "inputSchema": {"type": "object", "properties": {
        "class_name": {"type": "string"},
        "properties": {"type": "object", "additionalProperties": True, "x-roblox-property-metadata": metadata},
    }}}]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/studio_create_instance")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    view = client.get(started.headers["location"])
    editor = client.get(_link(view.text, "Edit object"))
    key = client.get(_link(editor.text, "Add field"))
    for char in "Size": key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    editor = client.get(_link(key.text, "Finish"), follow_redirects=True)
    typed = client.get(_link(editor.text, "Edit Size"))
    assert "Vector3 Editor" in typed.text
    assert "Edit X" in typed.text and "Edit Y" in typed.text and "Edit Z" in typed.text
    assert "Edit as string" not in typed.text and "Edit as number" not in typed.text

    key = client.get(_link(editor.text, "Add field"))
    for char in "Color": key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    editor = client.get(_link(key.text, "Finish"), follow_redirects=True)
    color = client.get(_link(editor.text, "Edit Color"))
    assert "Color3 Editor" in color.text and "Edit R" in color.text

    key = client.get(_link(editor.text, "Add field"))
    for char in "Material": key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    editor = client.get(_link(key.text, "Finish"), follow_redirects=True)
    enum_page = client.get(_link(editor.text, "Edit Material"))
    assert "Enum Editor" in enum_page.text and "Set Grass" in enum_page.text and "Set Rock" in enum_page.text


def test_runtime_enum_metadata_waits_for_delayed_job_and_reaches_editor(monkeypatch):
    from web import agent as agent_module

    runtime_metadata = {
        "Material": {
            "readable": True,
            "propertyName": "Material",
            "robloxType": "EnumItem",
            "enumType": "Material",
            "value": {"value": 256, "enumType": "Material", "name": "Plastic", "$type": "EnumItem"},
            "enumValues": [
                {"value": 256, "enumType": "Material", "name": "Plastic", "$type": "EnumItem"},
                {"value": 1280, "enumType": "Material", "name": "Grass", "$type": "EnumItem"},
                {"value": 1360, "enumType": "Material", "name": "Ground", "$type": "EnumItem"},
                {"value": 896, "enumType": "Material", "name": "Rock", "$type": "EnumItem"},
            ],
        }
    }
    runtime_result = {"structuredContent": {"ok": True, "data": {"className": "Part", "propertyMetadata": runtime_metadata}}}
    original_create_job = store.create_job

    def delayed_create_job(job):
        created = original_create_job(job)
        if job.tool == "studio_get_properties":
            loop = asyncio.get_running_loop()
            loop.call_later(0.05, lambda: store.complete(job.request_id, True, runtime_result))
        return created

    monkeypatch.setattr(agent_module, "DISCOVERY_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(store, "create_job", delayed_create_job)
    store.catalog.tools = [
        {"name": "studio_create_instance", "inputSchema": {"type": "object", "properties": {
            "class_name": {"type": "string"}, "properties": {"type": "object", "additionalProperties": True},
        }}},
        {"name": "studio_get_properties", "inputSchema": {"type": "object", "properties": {"class_name": {"type": "string"}}}},
    ]
    store.catalog.tool_count = 2

    tool_page = client.get("/agent/tool/studio_create_instance")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    view = client.get(started.headers["location"])
    view = client.get(_links(view.text, "Set Part")[0], follow_redirects=True)
    editor = client.get(_links(view.text, "Edit object")[0], follow_redirects=True)
    key = client.get(_link(editor.text, "Add field"))
    for char in "Material":
        key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    editor = client.get(_link(key.text, "Finish"), follow_redirects=True)
    enum_page = client.get(_link(editor.text, "Edit Material"))

    assert "ROBLOX_TYPE:" in enum_page.text and "EnumItem" in enum_page.text
    assert "ENUM_TYPE:" in enum_page.text and "Material" in enum_page.text
    assert "Set Plastic" in enum_page.text
    assert "Set Grass" in enum_page.text
    assert "Set Ground" in enum_page.text
    assert "Set Rock" in enum_page.text
    draft_view = client.get(_link(editor.text, "Back to Draft"))
    draft_id = re.search(r"DRAFT_ID: <code>(d_[^<]+)", draft_view.text).group(1)
    assert store.drafts[draft_id]["property_metadata"]["Material"]["enumValues"] == runtime_metadata["Material"]["enumValues"]


def test_incomplete_enum_metadata_does_not_overwrite_complete_runtime_values():
    from web.agent import merge_property_metadata

    complete = {
        "robloxType": "EnumItem",
        "enumType": "Material",
        "enumValues": [{"name": "Grass", "$type": "EnumItem"}],
    }
    incomplete = {"robloxType": "EnumItem", "enumType": "Material"}
    merged = merge_property_metadata(complete, incomplete)
    assert merged["enumValues"] == complete["enumValues"]


def test_enum_metadata_recovers_after_first_discovery_timeout(monkeypatch):
    from web import agent as agent_module

    runtime_result = {"structuredContent": {"ok": True, "data": {"propertyMetadata": {
        "Material": {
            "robloxType": "EnumItem",
            "enumType": "Material",
            "enumValues": [
                {"value": 1280, "enumType": "Material", "name": "Grass", "$type": "EnumItem"},
                {"value": 1360, "enumType": "Material", "name": "Ground", "$type": "EnumItem"},
            ],
        }
    }}}}
    original_create_job = store.create_job
    discovery_calls = 0

    def recover_on_second_discovery(job):
        nonlocal discovery_calls
        created = original_create_job(job)
        if job.tool == "studio_get_properties":
            discovery_calls += 1
            if discovery_calls == 2:
                asyncio.get_running_loop().call_later(0.05, lambda: store.complete(job.request_id, True, runtime_result))
        return created

    monkeypatch.setattr(agent_module, "DISCOVERY_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(store, "create_job", recover_on_second_discovery)
    store.catalog.tools = [
        {"name": "studio_create_instance", "inputSchema": {"type": "object", "properties": {
            "class_name": {"type": "string"}, "properties": {"type": "object", "additionalProperties": True},
        }}},
        {"name": "studio_get_properties", "inputSchema": {"type": "object", "properties": {"class_name": {"type": "string"}}}},
    ]
    store.catalog.tool_count = 2

    tool_page = client.get("/agent/tool/studio_create_instance")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    view = client.get(started.headers["location"])
    view = client.get(_links(view.text, "Set Part")[0], follow_redirects=True)
    editor = client.get(_links(view.text, "Edit object")[0], follow_redirects=True)
    key = client.get(_link(editor.text, "Add field"))
    for char in "Material":
        key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    editor = client.get(_link(key.text, "Finish"), follow_redirects=True)
    draft_view = client.get(_link(editor.text, "Back to Draft"))
    draft_id = re.search(r"DRAFT_ID: <code>(d_[^<]+)", draft_view.text).group(1)
    assert "Material" not in store.drafts[draft_id].get("property_metadata", {})

    reopened = client.get(_links(draft_view.text, "Edit object")[0], follow_redirects=True)
    enum_page = client.get(_link(reopened.text, "Edit Material"))
    assert "Set Grass" in enum_page.text and "Set Ground" in enum_page.text


def test_audited_basepart_types_and_spawnlocation_inheritance_work_without_live_metadata():
    store.catalog.tools = [{"name": "studio_create_instance", "inputSchema": {"type": "object", "properties": {
        "class_name": {"type": "string"},
        "properties": {"type": "object", "additionalProperties": True},
    }}}]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/studio_create_instance")
    view = client.get(client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False).headers["location"])
    class_name = client.get(_link(view.text, "Open String Composer (class_name)"))
    for char in "SpawnLocation":
        class_name = client.get(_link(class_name.text, f"Append {char}"), follow_redirects=True)
    view = client.get(_link(class_name.text, "Finish"), follow_redirects=True)
    editor = client.get(_link(view.text, "Edit object"))
    key = client.get(_link(editor.text, "Add field"))
    for char in "Position":
        key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    editor = client.get(_link(key.text, "Finish"), follow_redirects=True)
    typed = client.get(_link(editor.text, "Edit Position"))
    assert "Vector3 Editor" in typed.text
    assert "Edit X" in typed.text and "Edit Y" in typed.text and "Edit Z" in typed.text

    key = client.get(_link(editor.text, "Add field"))
    for char in "Anchored":
        key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    editor = client.get(_link(key.text, "Finish"), follow_redirects=True)
    boolean = client.get(_link(editor.text, "Edit Anchored"))
    assert "Set true" in boolean.text and "Set false" in boolean.text


def test_typed_vector3_value_uses_canonical_snapshot_and_stale_action_is_rejected():
    metadata = {"Size": {"robloxType": "Vector3"}}
    store.catalog.tools = [{"name": "studio_set_properties", "inputSchema": {"type": "object", "properties": {
        "values": {"type": "object", "additionalProperties": True, "x-roblox-property-metadata": metadata},
    }}}]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/studio_set_properties")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    view = client.get(started.headers["location"])
    editor = client.get(_link(view.text, "Edit object"))
    key = client.get(_link(editor.text, "Add field"))
    for char in "Size": key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    editor = client.get(_link(key.text, "Finish"), follow_redirects=True)
    typed = client.get(_link(editor.text, "Edit Size"))
    stale_link = _link(typed.text, "Edit Y")
    x = client.get(_link(typed.text, "Edit X"))
    x = client.get(_link(x.text, "Clear"), follow_redirects=True)
    for char in "80": x = client.get(_link(x.text, char), follow_redirects=True)
    typed = client.get(_link(x.text, "Finish"), follow_redirects=True)
    final_view = client.get(_link(typed.text, "Back to Draft"))
    assert '&quot;$type&quot;: &quot;Vector3&quot;' in final_view.text
    assert '&quot;x&quot;: 80' in final_view.text
    prepared = client.get(_link(final_view.text, "Prepare Execution"), follow_redirects=True)
    assert '&quot;$type&quot;: &quot;Vector3&quot;' in prepared.text and '&quot;x&quot;: 80' in prepared.text
    stale = client.get(stale_link, follow_redirects=False)
    assert stale.status_code == 200
    assert "STALE DRAFT VIEW" in stale.text


def test_batch_nested_property_uses_same_typed_dispatch():
    item_schema = {"type": "object", "additionalProperties": True, "x-roblox-property-metadata": {"Size": {"robloxType": "Vector3"}}}
    store.catalog.tools = [{"name": "studio_batch", "inputSchema": {"type": "object", "properties": {
        "operations": {"type": "array", "items": item_schema},
    }}}]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/studio_batch")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    view = client.get(started.headers["location"])
    array = client.get(_link(view.text, "Edit array"))
    operation = client.get(_link(array.text, "Add item"), follow_redirects=True)
    operation = client.get(_link(operation.text, "Edit item 0"), follow_redirects=True)
    key = client.get(_link(operation.text, "Add field"), follow_redirects=True)
    for char in "Size": key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    operation = client.get(_link(key.text, "Finish"), follow_redirects=True)
    typed = client.get(_link(operation.text, "Edit Size"))
    assert "Vector3 Editor" in typed.text and "Edit X" in typed.text


def test_color3_component_range_rejects_invalid_typed_value():
    metadata = {"Color": {"robloxType": "Color3"}}
    store.catalog.tools = [{"name": "studio_create_instance", "inputSchema": {"type": "object", "properties": {
        "properties": {"type": "object", "additionalProperties": True, "x-roblox-property-metadata": metadata},
    }}}]
    store.catalog.tool_count = 1
    tool_page = client.get("/agent/tool/studio_create_instance")
    started = client.get(_link(tool_page.text, "Start invocation"), follow_redirects=False)
    view = client.get(started.headers["location"])
    editor = client.get(_link(view.text, "Edit object"))
    key = client.get(_link(editor.text, "Add field"))
    for char in "Color": key = client.get(_link(key.text, f"Append {char}"), follow_redirects=True)
    editor = client.get(_link(key.text, "Finish"), follow_redirects=True)
    color = client.get(_link(editor.text, "Edit Color"))
    red = client.get(_link(color.text, "Edit R"))
    red = client.get(_link(red.text, "Clear"), follow_redirects=True)
    red = client.get(_link(red.text, "2"), follow_redirects=True)
    rejected = client.get(_link(red.text, "Finish"), follow_redirects=False)
    assert rejected.status_code == 400
    assert "at most 1" in rejected.text


def test_prepared_invocation_keeps_snapshot_after_draft_changes_and_executes_once():
    view = client.get(_start_string_draft().request.url)
    view = client.get(_link(view.text, "Set Part"), follow_redirects=True)
    view = _compose_via_visible_links(view, "WebControlledPart")
    prepared = client.get(_link(view.text, "Prepare Execution"), follow_redirects=True)
    prepare_id = re.search(r"PREPARE_ID: <code>([^<]+)", prepared.text).group(1)
    assert "WebControlledPart" in prepared.text
    changed = client.get(_link(view.text, "Open String Composer (name)"), follow_redirects=True)
    changed = client.get(_link(changed.text, "Clear"), follow_redirects=True)
    for char in "SomethingElse":
        changed = client.get(_link(changed.text, f"Append {char}"), follow_redirects=True)
    changed = client.get(_link(changed.text, "Finish"), follow_redirects=True)
    assert "SomethingElse" in changed.text
    prepared_again = client.get(f"/agent/prepared/{prepare_id}")
    assert "WebControlledPart" in prepared_again.text and "SomethingElse" not in prepared_again.text
    execute = _link(prepared_again.text, "Execute now")
    first = client.get(execute, follow_redirects=False)
    second = client.get(execute, follow_redirects=False)
    assert first.headers["location"] == second.headers["location"]
    request_id = re.search(r"REQUEST_ID: <code>([^<]+)", client.get(first.headers["location"]).text).group(1)
    assert store.jobs[request_id].arguments["name"] == "WebControlledPart"


def test_result_views_are_immutable_and_refresh_creates_new_snapshot():
    view = client.get(_start_string_draft().request.url)
    view = client.get(_link(view.text, "Set Part"), follow_redirects=True)
    view = _compose_via_visible_links(view, "ResultSnapshot")
    prepared = client.get(_link(view.text, "Prepare Execution"), follow_redirects=True)
    result_one = client.get(_link(prepared.text, "Execute now"), follow_redirects=True)
    result_one_id = re.search(r"RESULT_VIEW_ID: <code>([^<]+)", result_one.text).group(1)
    request_id = re.search(r"REQUEST_ID: <code>([^<]+)", result_one.text).group(1)
    assert "STATUS: pending" in result_one.text
    store.complete(request_id, True, {"ok": True, "name": "ResultSnapshot"})
    result_two = client.get(_link(result_one.text, "Refresh Result"), follow_redirects=True)
    assert "STATUS: completed" in result_two.text
    assert "STATUS: pending" in client.get(f"/agent/result-view/{result_one_id}").text
