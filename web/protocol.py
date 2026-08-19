from __future__ import annotations

import json
from typing import Any


def parse_scalar(value: str) -> Any:
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        if lowered and (lowered.isdigit() or (lowered[0] in "+-" and lowered[1:].isdigit())):
            return int(lowered)
        if any(char in lowered for char in (".", "e")):
            return float(lowered)
    except (ValueError, IndexError):
        pass
    return value


def parse_arguments(raw_args: str | None, query: dict[str, str], excluded: set[str]) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    if raw_args:
        decoded = json.loads(raw_args)
        if not isinstance(decoded, dict):
            raise ValueError("args must decode to a JSON object")
        arguments.update(decoded)
    # Explicit query values win consistently over the JSON object.
    for key, value in query.items():
        if key not in excluded:
            arguments[key] = parse_scalar(value)
    return arguments
