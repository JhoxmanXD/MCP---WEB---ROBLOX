import asyncio

from local.mcp_adapter import safe_close


def test_safe_close_does_not_kill_reconnect_after_transient_cleanup_failure():
    class BrokenAdapter:
        async def close(self):
            raise ExceptionGroup("stream disconnected", [ConnectionError("peer closed")])

    asyncio.run(safe_close(BrokenAdapter()))
