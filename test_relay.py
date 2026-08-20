import asyncio

from local.mcp_adapter import safe_close
from local.relay_client import catalog_refresh_needed


def test_safe_close_does_not_kill_reconnect_after_transient_cleanup_failure():
    class BrokenAdapter:
        async def close(self):
            raise ExceptionGroup("stream disconnected", [ConnectionError("peer closed")])

    asyncio.run(safe_close(BrokenAdapter()))


def test_catalog_refreshes_after_reconnect_or_studio_state_change():
    assert catalog_refresh_needed(False, None, True) is True
    assert catalog_refresh_needed(True, True, True) is False
    assert catalog_refresh_needed(True, True, False) is True
