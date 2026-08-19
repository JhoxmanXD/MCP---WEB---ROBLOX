import re

from fastapi.testclient import TestClient

from web.app import app, store


client = TestClient(app)


def setup_function():
    store.jobs.clear(); store.queue.clear(); store.states.clear(); store.latest = None
    store.recent_refs.clear(); store.recent_string_values.clear(); store.drafts.clear(); store.views.clear(); store.actions.clear(); store.prepared.clear(); store.result_views.clear()
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
    health = client.get("/api/v1/health.json").json()
    assert health["agent_protocol_version"] == "immutable-v1"


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
