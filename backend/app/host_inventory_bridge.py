"""Push Agent health updates to the module that owns host inventory."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

logger = logging.getLogger(__name__)


def publish_host_inventory_telemetry(server, opener=None) -> bool:
    url = os.getenv("HOST_INVENTORY_INGEST_URL", "").strip()
    token_path = os.getenv("HOST_INVENTORY_INGEST_TOKEN_FILE", "").strip()
    if not url or not token_path:
        return False

    try:
        token = Path(token_path).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not token:
        return False

    host_id = str(getattr(server, "id", "") or "")
    if not host_id:
        return False

    last_seen = getattr(server, "last_seen", None)
    payload = {
        "id": host_id,
        "status": str(getattr(server, "status", "unknown") or "unknown"),
        "docker_available": bool(getattr(server, "docker_available", False)),
        "is_local": bool(getattr(server, "is_local", False)),
        "agent_status": str(getattr(server, "agent_status", "unknown") or "unknown"),
        "agent_type": str(getattr(server, "agent_type", "remote") or "remote"),
        "last_seen": last_seen.isoformat() if last_seen else None,
        "agent_version": str(getattr(server, "agent_version", "") or ""),
        "log_agent_status": str(getattr(server, "log_agent_status", "unknown") or "unknown"),
        "log_agent_version": str(getattr(server, "log_agent_version", "") or ""),
    }
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-OpsCenter-Module-Token": token},
        method="POST",
    )
    try:
        direct_opener = opener or build_opener(ProxyHandler({})).open
        with direct_opener(request, timeout=2.5) as response:
            return 200 <= response.status < 300
    except (OSError, TimeoutError, URLError, ValueError) as error:
        logger.warning("host_inventory_publish_failed host_id=%s error=%s", host_id, type(error).__name__)
        return False
