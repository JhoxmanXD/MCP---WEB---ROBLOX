import asyncio

from local.mcp_adapter import StreamableHTTPMCP
from local.relay_client import catalog_needs_repair, restore_catalog_if_needed


class FakeSession:
    async def list_tools(self):
        class Result:
            tools = [{"name": "read_tool"}]
        return Result()

    async def call_tool(self, name, arguments):
        return {"name": name, "arguments": arguments}


def test_local_adapter_serializes_tools_and_results():
    adapter = StreamableHTTPMCP("http://example.invalid/mcp")
    adapter.session = FakeSession()
    async def check():
        assert await adapter.list_tools() == [{"name": "read_tool"}]
        assert await adapter.call_tool("read_tool", {"limit": 2}) == {"name": "read_tool", "arguments": {"limit": 2}}
    asyncio.run(check())


def test_catalog_repair_decision():
    assert catalog_needs_repair({"catalog_present": True, "catalog_tool_count": 71}, 71) is False
    assert catalog_needs_repair({"catalog_present": False, "catalog_tool_count": 0}, 71) is True
    assert catalog_needs_repair({"catalog_present": True, "catalog_tool_count": 70}, 71) is True


def test_catalog_restore_skips_existing_and_restores_missing():
    class FakeRelay:
        def __init__(self, fails=False):
            self.calls = 0
            self.fails = fails

        def catalog(self, tools, studio_connected):
            self.calls += 1
            if self.fails:
                raise RuntimeError("temporary outage")

    tools = [{"name": "a"}] * 71
    existing = FakeRelay()
    assert restore_catalog_if_needed(existing, tools, {"catalog_present": True, "catalog_tool_count": 71}) is False
    assert existing.calls == 0
    missing = FakeRelay()
    assert restore_catalog_if_needed(missing, tools, {"catalog_present": False, "catalog_tool_count": 0}) is True
    assert missing.calls == 1
    failing = FakeRelay(fails=True)
    assert restore_catalog_if_needed(failing, tools, {"catalog_present": False, "catalog_tool_count": 0}) is False
    assert failing.calls == 1


def test_mcp_adapter_reports_studio_session_state():
    adapter = StreamableHTTPMCP("http://example.invalid/mcp")

    class Session:
        async def call_tool(self, name, arguments):
            return {"isError": False, "structuredContent": {"data": {"sessions": [{"session_id": "studio-1"}]}}}

    adapter.session = Session()
    assert asyncio.run(adapter.studio_connected()) is True
