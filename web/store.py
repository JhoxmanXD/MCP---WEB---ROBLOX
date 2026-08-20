from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

try:
    from .models import Catalog, Heartbeat, Job, now_iso
except ImportError:  # Render imports app.py as a top-level module.
    from models import Catalog, Heartbeat, Job, now_iso


class MemoryStore:
    def __init__(self) -> None:
        self.lock = RLock()
        self.jobs: dict[str, Job] = {}
        self.queue: deque[str] = deque()
        self.server_instance_id = str(uuid4())
        self.catalog_generation = 0
        self.catalog = Catalog(server_instance_id=self.server_instance_id)
        self.latest: dict[str, Any] | None = None
        self.states: dict[str, Any] = {}
        self.heartbeat: Heartbeat | None = None
        self.drafts: dict[str, dict[str, Any]] = {}
        self.recent_refs: list[dict[str, Any]] = []
        self.recent_string_values: list[str] = []
        self.views: dict[str, dict[str, Any]] = {}
        self.actions: dict[str, dict[str, Any]] = {}
        self.prepared: dict[str, dict[str, Any]] = {}
        self.result_views: dict[str, dict[str, Any]] = {}
        self.editors: dict[str, dict[str, Any]] = {}

    def export_agent_state(self) -> dict[str, Any]:
        """Return JSON-safe workflow state for a shared state backend.

        Agent navigation records and the minimum relay records that feed result
        views are shared together. This prevents a second worker from seeing a
        ``R_`` snapshot while missing its job or catalog.
        """
        with self.lock:
            jobs = {
                request_id: job.model_dump(mode="json")
                for request_id, job in list(self.jobs.items())[-1024:]
            }
            return deepcopy({
                "jobs": jobs,
                "queue": [request_id for request_id in self.queue if request_id in jobs],
                "latest": self.latest,
                "states": self.states,
                "catalog": self.catalog.model_dump(mode="json"),
                "heartbeat": self.heartbeat.model_dump(mode="json") if self.heartbeat else None,
                "drafts": self.drafts,
                "recent_refs": self.recent_refs,
                "recent_string_values": self.recent_string_values,
                "views": self.views,
                "actions": self.actions,
                "prepared": self.prepared,
                "result_views": self.result_views,
                "editors": self.editors,
            })

    def import_agent_state(self, state: dict[str, Any]) -> None:
        """Replace Agent Mode collections after validating JSON shape."""
        if not isinstance(state, dict):
            raise ValueError("agent state must be a JSON object")
        collections = (
            "jobs", "queue", "latest", "states", "catalog", "heartbeat",
            "drafts", "recent_refs", "recent_string_values", "views",
            "actions", "prepared", "result_views", "editors",
        )
        for name in collections:
            default = [] if name in {"queue", "recent_refs", "recent_string_values"} else None if name == "heartbeat" else {}
            value = state.get(name, default)
            if name in {"queue", "recent_refs", "recent_string_values"}:
                if not isinstance(value, list):
                    raise ValueError(f"agent state field {name} must be a list")
            elif name == "heartbeat":
                if value is not None and not isinstance(value, dict):
                    raise ValueError("agent state field heartbeat must be an object or null")
            elif name == "latest":
                if value is not None and not isinstance(value, dict):
                    raise ValueError("agent state field latest must be an object or null")
            elif not isinstance(value, dict):
                raise ValueError(f"agent state field {name} must be an object")
        with self.lock:
            self.jobs = {request_id: Job(**job) for request_id, job in state.get("jobs", {}).items()}
            self.queue = deque(request_id for request_id in state.get("queue", []) if request_id in self.jobs)
            self.latest = deepcopy(state.get("latest"))
            self.states = deepcopy(state.get("states", {}))
            self.catalog = Catalog(**state.get("catalog", {}))
            heartbeat = state.get("heartbeat")
            self.heartbeat = Heartbeat(**heartbeat) if heartbeat else None
            self.drafts = deepcopy(state.get("drafts", {}))
            self.recent_refs = deepcopy(state.get("recent_refs", []))
            self.recent_string_values = deepcopy(state.get("recent_string_values", []))
            self.views = deepcopy(state.get("views", {}))
            self.actions = deepcopy(state.get("actions", {}))
            self.prepared = deepcopy(state.get("prepared", {}))
            self.result_views = deepcopy(state.get("result_views", {}))
            self.editors = deepcopy(state.get("editors", {}))

    def create_job(self, job: Job) -> Job:
        with self.lock:
            existing = self.jobs.get(job.request_id)
            if existing:
                return existing
            self.jobs[job.request_id] = job
            self.queue.append(job.request_id)
            return job

    def next_job(self) -> Job | None:
        with self.lock:
            while self.queue:
                job = self.jobs[self.queue.popleft()]
                if job.status != "pending":
                    continue
                job.status = "running"
                job.started_at = now_iso()
                return job
        return None

    def complete(self, request_id: str, success: bool, result: Any = None, error: Any = None) -> Job:
        with self.lock:
            job = self.jobs[request_id]
            job.status = "completed" if success else "error"
            job.completed_at = now_iso()
            job.result = result if success else None
            job.error = None if success else error
            self.latest = job.model_dump(mode="json")
            if job.state_key:
                self.states[job.state_key] = result if success else {"error": error}
            return job

    def counts(self) -> dict[str, int]:
        with self.lock:
            return {
                "pending": sum(j.status == "pending" for j in self.jobs.values()),
                "running": sum(j.status == "running" for j in self.jobs.values()),
                "completed": sum(j.status == "completed" for j in self.jobs.values()),
                "error": sum(j.status == "error" for j in self.jobs.values()),
            }

    def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.lock:
            return [job.model_dump(mode="json") for job in list(self.jobs.values())[-limit:][::-1]]

    def online(self) -> bool:
        if not self.heartbeat:
            return False
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(self.heartbeat.timestamp)).total_seconds()
            return age < 15
        except ValueError:
            return False

    def replace_catalog(self, tools: list[dict[str, Any]], studio_connected: bool, updated_at: str | None = None) -> Catalog:
        with self.lock:
            self.catalog_generation += 1
            values: dict[str, Any] = {
                "server_instance_id": self.server_instance_id,
                "catalog_generation": self.catalog_generation,
                "studio_connected": studio_connected,
                "tool_count": len(tools),
                "tools": tools,
            }
            if updated_at:
                values["updated_at"] = updated_at
            self.catalog = Catalog(**values)
            return self.catalog
