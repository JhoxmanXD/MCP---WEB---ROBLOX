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
