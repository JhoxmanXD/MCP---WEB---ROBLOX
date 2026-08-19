import re

from fastapi.testclient import TestClient

from web.app import app, store


client = TestClient(app)


def setup_function():
    store.jobs.clear(); store.queue.clear(); store.states.clear(); store.latest = None
    store.catalog.tools = [{"name": "read_tool", "description": "read", "inputSchema": {}}]
    store.catalog.tool_count = 1


def test_health_and_no_cache():
    response = client.get("/api/v1/health.json")
    assert response.status_code == 200
    assert response.json()["service"] == "MCP-WEB"
    assert response.headers["cache-control"].startswith("no-store")


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
    draft_id = draft_url.rsplit("/", 1)[-1]
    set_value = client.get(f"/agent/draft/{draft_id}/arg/enabled/set/true", follow_redirects=False)
    assert set_value.status_code == 303
    prepared = client.get(f"/agent/draft/{draft_id}/prepare", follow_redirects=False)
    assert prepared.status_code == 200
    execute_link = re.search(r"/agent/execute/[^']+", prepared.text).group(0)
    executed = client.get(execute_link, follow_redirects=False)
    assert executed.status_code == 303
    request_id = executed.headers["location"].rsplit("/", 1)[-1]
    repeat = client.get(execute_link, follow_redirects=False)
    assert repeat.headers["location"].endswith(request_id)
    assert len([job for job in store.jobs.values() if job.request_id == request_id]) == 1
