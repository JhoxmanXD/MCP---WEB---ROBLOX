from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict[str, Any]:
    with (ROOT / "config.json").open(encoding="utf-8") as handle:
        return json.load(handle)
