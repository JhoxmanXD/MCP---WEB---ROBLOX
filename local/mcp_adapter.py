from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from typing import Any

from .protocol import jsonable


class StreamableHTTPMCP:
    def __init__(self, url: str) -> None:
        self.url = url
        self.stack = AsyncExitStack()
        self.session = None

    async def connect(self) -> None:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        read_stream, write_stream, _ = await self.stack.enter_async_context(streamable_http_client(self.url))
        self.session = await self.stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self.session.initialize()

    async def close(self) -> None:
        await self.stack.aclose()

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self.session.list_tools()
        return [jsonable(tool) for tool in result.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = await self.session.call_tool(name, arguments)
        return jsonable(result)

    async def studio_connected(self) -> bool:
        result = await self.call_tool("studio_list_sessions", {})
        if result.get("isError"):
            return False
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            data = structured.get("data")
            if isinstance(data, dict) and isinstance(data.get("sessions"), list):
                return bool(data["sessions"])
        for item in result.get("content", []):
            text = item.get("text") if isinstance(item, dict) else None
            if isinstance(text, str):
                try:
                    data = json.loads(text).get("data", {})
                    return bool(data.get("sessions", []))
                except (ValueError, TypeError):
                    continue
        return False


async def connect_with_backoff(url: str, attempts: int = 3) -> StreamableHTTPMCP:
    last: Exception | None = None
    for attempt in range(attempts):
        adapter = StreamableHTTPMCP(url)
        try:
            await adapter.connect()
            return adapter
        except Exception as exc:
            last = exc
            await adapter.close()
            await asyncio.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"MCP connection failed: {last}")
