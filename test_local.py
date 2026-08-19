import asyncio

from local.mcp_adapter import StreamableHTTPMCP


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
