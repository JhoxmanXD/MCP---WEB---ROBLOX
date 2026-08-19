from __future__ import annotations

import httpx


class RelayWebClient:
    def __init__(self, base_url: str, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout, headers={"Accept": "application/json"})

    def close(self) -> None:
        self.client.close()

    def post_json(self, path: str, payload: dict) -> dict:
        response = self.client.post(self.base_url + path, json=payload)
        response.raise_for_status()
        return response.json()

    def next_job(self) -> dict | None:
        response = self.client.get(self.base_url + "/api/v1/jobs/next")
        response.raise_for_status()
        data = response.json()
        return data if data.get("request_id") else None

    def complete(self, request_id: str, payload: dict) -> dict:
        return self.post_json(f"/api/v1/jobs/{request_id}/complete", payload)

    def heartbeat(self, payload: dict) -> dict:
        return self.post_json("/api/v1/client/heartbeat", payload)

    def catalog(self, tools: list[dict], studio_connected: bool) -> dict:
        return self.post_json("/api/v1/catalog", {"tools": tools, "studio_connected": studio_connected})
