from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from .config import load_config
from .mcp_adapter import connect_with_backoff, safe_close
from .web_client import RelayWebClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("mcp-web")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def catalog_needs_repair(heartbeat: dict, tool_count: int) -> bool:
    return heartbeat.get("catalog_present") is not True or heartbeat.get("catalog_tool_count") != tool_count


def restore_catalog_if_needed(relay: RelayWebClient, tools: list[dict], heartbeat: dict) -> bool:
    if not catalog_needs_repair(heartbeat, len(tools)):
        return False
    try:
        relay.catalog(tools, bool(heartbeat.get("studio_connected")))
        return True
    except Exception as exc:
        log.warning("[WEB] Catalog upload failed; retrying: %s", exc)
        return False


def catalog_refresh_needed(published: bool, previous_studio_connected: bool | None, studio_connected: bool) -> bool:
    return not published or previous_studio_connected != studio_connected


async def run() -> None:
    config = load_config()
    relay = RelayWebClient(config["relay_url"])
    interval = float(config.get("poll_interval_seconds", 1.0))
    studio_check_interval = float(config.get("studio_check_interval_seconds", 5.0))
    client_name = config.get("client_name", "local-mcp-client")
    mcp_url = config.get("mcp_url", "http://127.0.0.1:8787/mcp")
    log.info("[MCP-WEB] Starting...")
    adapter = None
    tools: list[dict] = []
    studio_connected = False
    catalog_published = False
    catalog_studio_connected: bool | None = None
    last_studio_check = 0.0
    try:
        while True:
            try:
                if adapter is None:
                    log.info("[MCP] Connecting to %s", mcp_url)
                    adapter = await connect_with_backoff(mcp_url)
                    tools = await adapter.list_tools()
                    log.info("[MCP] Connected — %d tools discovered", len(tools))
                    catalog_published = False
                    catalog_studio_connected = None
                    last_studio_check = 0.0
                now = time.monotonic()
                if now - last_studio_check >= studio_check_interval:
                    studio_connected = "studio_list_sessions" in {tool.get("name") for tool in tools}
                    if studio_connected:
                        try:
                            studio_connected = await adapter.studio_connected()
                        except Exception as exc:
                            studio_connected = False
                            log.warning("[STUDIO] Connection check failed: %s", exc)
                    log.info("[STUDIO] %s", "connected" if studio_connected else "disconnected")
                    last_studio_check = now
                heartbeat = relay.heartbeat({"client": client_name, "mcp_connected": True, "studio_connected": studio_connected, "tool_count": len(tools), "timestamp": iso_now()})
                refreshed = False
                if catalog_refresh_needed(catalog_published, catalog_studio_connected, studio_connected):
                    try:
                        relay.catalog(tools, studio_connected)
                        catalog_published = True
                        catalog_studio_connected = studio_connected
                        refreshed = True
                    except Exception as exc:
                        log.warning("[WEB] Catalog upload failed; retrying: %s", exc)
                elif await asyncio.to_thread(restore_catalog_if_needed, relay, tools, heartbeat):
                    catalog_published = True
                    catalog_studio_connected = studio_connected
                    refreshed = True
                if refreshed:
                    log.info("[WEB] Catalog uploaded/restored (%d tools)", len(tools))
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
                    try:
                        await asyncio.to_thread(relay.heartbeat, {"client": client_name, "mcp_connected": False, "studio_connected": False, "tool_count": len(tools), "timestamp": iso_now()})
                    except Exception:
                        pass
                    await safe_close(adapter)
                adapter = None
                studio_connected = False
                await asyncio.sleep(3)
    finally:
        if adapter is not None:
            await safe_close(adapter)
        relay.close()


if __name__ == "__main__":
    asyncio.run(run())
