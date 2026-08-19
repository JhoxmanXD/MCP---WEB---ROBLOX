from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

JobStatus = Literal["pending", "running", "completed", "error"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Job(BaseModel):
    request_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    state_key: str | None = None
    status: JobStatus = "pending"
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    result: Any = None
    error: Any = None


class Catalog(BaseModel):
    updated_at: str = Field(default_factory=now_iso)
    studio_connected: bool = False
    tool_count: int = 0
    tools: list[dict[str, Any]] = Field(default_factory=list)


class Heartbeat(BaseModel):
    client: str
    mcp_connected: bool = False
    studio_connected: bool = False
    tool_count: int = 0
    timestamp: str = Field(default_factory=now_iso)
