"""Push Agent health updates to the module that owns host inventory."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

logger = logging.getLogger(__name__)


def active_host_ids():
    """Return authoritative asset IDs, or None when the asset source is unavailable."""
    url = os.getenv("HOST_INVENTORY_INGEST_URL", "").strip()
    token_path = os.getenv("HOST_INVENTORY_INGEST_TOKEN_FILE", "").strip()
    if not url or not token_path:
        return None
    try:
        token = Path(token_path).read_text(encoding="utf-8").strip()
        request = Request(url.rsplit("/", 2)[0] + "/internal/active-hosts",
                          headers={"X-OpsCenter-Module-Token": token})
        with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
            data = json.load(response)
        if data.get("status") != "ok" or not isinstance(data.get("ids"), list):
            return None
        from uuid import UUID
        return {str(UUID(value)) for value in data["ids"]}
    except (OSError, TimeoutError, URLError, ValueError, TypeError):
        return None


def publish_host_inventory_telemetry(server, opener=None) -> bool:
    if not getattr(server, "enabled", True):
        return False
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
        "registration": {
            "cluster_id": str(getattr(server, "cluster_id", None) or ""),
            **{
            key: (getattr(server, key, None) or (22 if key == "ssh_port" else [] if key == "tags" else ""))
            for key in ("name", "host", "ssh_port", "tags", "remark", "lan_ip", "wireguard_ip",
                        "preferred_management_channel", "management_address_override",
                        "kubernetes_node_name", "node_role", "runtime_type")
            },
        },
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
