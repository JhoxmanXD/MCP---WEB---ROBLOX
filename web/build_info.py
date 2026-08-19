from __future__ import annotations

import os


DEPLOY_COMMIT = os.environ.get("RENDER_GIT_COMMIT", "unknown")
RENDER_INSTANCE_ID = os.environ.get("RENDER_INSTANCE_ID", "unknown")
AGENT_PROTOCOL_VERSION = "immutable-v1"
