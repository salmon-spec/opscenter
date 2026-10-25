from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from storage import HostInventoryStore

_sync_lock = threading.Lock()
_last_sync = 0.0
_stale_after_seconds = int(os.getenv("HOST_INVENTORY_STALE_AFTER_SECONDS", "300"))


class HostInventoryReader:
    def __init__(self, store=None):
        self.source_url = os.getenv(
            "HOST_INVENTORY_SOURCE_URL",
            "http://backend:9091/api/v2/servers",
        )
        self.timeout = float(os.getenv("HOST_INVENTORY_TIMEOUT_SECONDS", "5"))
        self.sync_enabled = os.getenv("HOST_INVENTORY_SYNC_ENABLED", "true").lower() == "true"
        self.store = store if store is not None else HostInventoryStore()

    def fetch_source(self) -> list[dict]:
        request = Request(self.source_url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=self.timeout) as response:
            source_hosts = json.load(response)
        if not isinstance(source_hosts, list):
            raise ValueError("invalid source response")
        return [{
            "id": str(host["id"]),
            "name": str(host.get("name") or "未命名主机"),
            "host": str(host.get("host") or ""),
            "ssh_port": host.get("ssh_port") or 22,
            "tags": json.dumps(host.get("tags") or []),
            "status": str(host.get("status") or "unknown"),
            "docker_available": bool(host.get("docker_available")),
            "is_local": bool(host.get("is_local")),
            "agent_status": str(host.get("agent_status") or "unknown"),
            "agent_type": str(host.get("agent_type") or "remote"),
            "last_seen": host.get("last_seen"),
            "service_count": int(host.get("service_count") or 0),
            "agent_version": str(host.get("agent_version") or ""),
            "log_agent_status": str(host.get("log_agent_status") or "unknown"),
            "log_agent_version": str(host.get("log_agent_version") or ""),
            "remark": str(host.get("remark") or ""),
            "lan_ip": str(host.get("lan_ip") or ""),
            "wireguard_ip": str(host.get("wireguard_ip") or ""),
            "preferred_management_channel": str(host.get("preferred_management_channel") or "auto"),
            "management_address_override": str(host.get("management_address_override") or ""),
            "cluster_id": str(host.get("cluster_id") or ""),
            "kubernetes_node_name": str(host.get("kubernetes_node_name") or ""),
            "node_role": str(host.get("node_role") or ""),
            "runtime_type": str(host.get("runtime_type") or ""),
        } for host in source_hosts if host.get("enabled", True)]

    def collect(self) -> tuple[dict, list[dict], list[dict]]:
        global _last_sync
        now = datetime.now(timezone.utc)
        self.store.ensure_schema()
        if self.sync_enabled and time.monotonic() - _last_sync >= 30 and _sync_lock.acquire(blocking=False):
            try:
                _last_sync = time.monotonic()
                try:
                    self.store.save_snapshot(self.fetch_source(), now.isoformat())
                except Exception:
                    self.store.record_failure(now.isoformat())
            finally:
                _sync_lock.release()

        hosts, state = self.store.read_snapshot()
        telemetry_times = [host.get("telemetry_updated_at") for host in hosts]
        if telemetry_times and all(telemetry_times):
            normalized_times = []
            for value in telemetry_times:
                parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
                normalized_times.append(
                    parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None
                    else parsed.astimezone(timezone.utc)
                )
            collected_at = min(normalized_times)
        else:
            collected_at = state["collected_at"] if state else now
        collected_time = collected_at if isinstance(collected_at, datetime) else datetime.fromisoformat(str(collected_at)) if collected_at else now
        if collected_time.tzinfo is None:
            collected_time = collected_time.replace(tzinfo=timezone.utc)
        collected_at = collected_time.isoformat()
        cache_age_seconds = max(0, int((now - collected_time).total_seconds()))
        stale = cache_age_seconds > _stale_after_seconds
        telemetry_current = bool(hosts and all(telemetry_times)) and not stale
        state_current = bool(state and state["status"] == "ok" and not stale)
        status = "ok" if telemetry_current or state_current else "degraded" if hosts else "unavailable"
        error = state["error"] if state and state["status"] != "ok" and not telemetry_current else None
        if stale and not error:
            error = f"主机健康状态超过 {_stale_after_seconds} 秒未更新，当前展示最后成功数据"

        findings = [{
            "id": f"host:{host['id']}:health",
            "resource_id": host["id"],
            "severity": "warning",
            "title": f"{host['name']}状态异常",
            "summary": "健康状态来源超过 300 秒未更新" if stale else "主机已不在当前资产源中，保留快照待核对" if not host["source_seen"] else f"主机状态：{host['status']}；Agent 状态：{host['agent_status']}",
            "observed_at": host.get("telemetry_updated_at") or host.get("last_seen") or collected_at,
        } for host in hosts if stale or not host["source_seen"] or host["status"] != "online" or host["agent_status"] not in {"running", "unknown"}][:1000]
        summary = {
            "status": "unavailable" if status == "unavailable" else "degraded" if status == "degraded" or findings else "ok",
            "collected_at": collected_at,
            "stale_after_seconds": _stale_after_seconds,
            "error": error,
            "data": {
                "hosts_total": len(hosts),
                "hosts_online": sum(host["status"] == "online" for host in hosts),
                "hosts_offline": sum(host["status"] == "offline" for host in hosts),
                "hosts_unknown": sum(host["status"] not in {"online", "offline"} for host in hosts),
                "source_missing": sum(not host["source_seen"] for host in hosts),
                "agent_running": sum(host["agent_status"] == "running" for host in hosts),
                "cache_age_seconds": cache_age_seconds,
            },
        }
        return summary, findings, hosts


def summary_provider():
    return HostInventoryReader().collect()[0]


def findings_provider():
    summary, findings, _ = HostInventoryReader().collect()
    return {"status": summary["status"], "collected_at": summary["collected_at"], "items": findings, "error": summary["error"]}


def hosts_provider():
    summary, _, hosts = HostInventoryReader().collect()
    return {"status": summary["status"], "collected_at": summary["collected_at"], "items": hosts, "error": summary["error"]}
