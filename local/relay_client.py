from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from .config import load_config
from .mcp_adapter import connect_with_backoff
from .web_client import RelayWebClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("mcp-web")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run() -> None:
    config = load_config()
    relay = RelayWebClient(config["relay_url"])
    interval = float(config.get("poll_interval_seconds", 1.0))
    client_name = config.get("client_name", "local-mcp-client")
    mcp_url = config.get("mcp_url", "http://127.0.0.1:8787/mcp")
    log.info("[MCP-WEB] Starting...")
    adapter = None
    try:
        while True:
            try:
                if adapter is None:
                    log.info("[MCP] Connecting to %s", mcp_url)
                    adapter = await connect_with_backoff(mcp_url)
                    tools = await adapter.list_tools()
                    log.info("[MCP] Connected — %d tools discovered", len(tools))
                    relay.catalog(tools, True)
                    log.info("[WEB] Catalog uploaded")
                relay.heartbeat({"client": client_name, "mcp_connected": True, "studio_connected": True, "tool_count": len(tools), "timestamp": iso_now()})
                job = await asyncio.to_thread(relay.next_job)
                if job:
                    started = time.monotonic()
                    log.info("[JOB %s] Tool: %s Arguments: %s", job["request_id"], job["tool"], job["arguments"])
                    try:
                        result = await adapter.call_tool(job["tool"], job.get("arguments", {}))
                        await asyncio.to_thread(relay.complete, job["request_id"], {"success": True, "result": result})
                        log.info("[JOB %s] Completed in %.2fs — result uploaded", job["request_id"], time.monotonic() - started)
                    except Exception as exc:
                        await asyncio.to_thread(relay.complete, job["request_id"], {"success": False, "error": str(exc), "error_type": type(exc).__name__, "tool": job["tool"]})
                        log.exception("[JOB %s] MCP error; continuing", job["request_id"])
                else:
                    log.info("[POLL] Waiting for jobs...")
                await asyncio.sleep(interval)
            except Exception as exc:
                log.warning("[RECONNECT] %s", exc)
                if adapter is not None:
                    await adapter.close()
                adapter = None
                await asyncio.sleep(3)
    finally:
        if adapter is not None:
            await adapter.close()
        relay.close()


if __name__ == "__main__":
    asyncio.run(run())
