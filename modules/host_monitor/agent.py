"""Direct, bounded OpsAgent sampling owned by host-monitor."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from storage import MonitorStore


FIELD_ALIASES = {"cpu_usage": "cpu_percent", "memory_usage": "memory_percent",
                 "disk_usage": "disk_percent", "load_1m": "load1",
                 "load_5m": "load5", "load_15m": "load15",
                 "uptime_seconds": "uptime"}


class AgentSampler:
    def __init__(self, store=None, interval=30):
        self.store = store or MonitorStore()
        self.interval = interval
        self.host_id = os.getenv("HOST_MONITOR_AGENT_ID", "")
        self.token_file = os.getenv("HOST_MONITOR_AGENT_TOKEN_FILE", "")
        self._stop = threading.Event()
        self._thread = None
        self.last_error = False

    def start(self):
        if self.host_id and self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="host-monitor-agent", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=6)

    def _loop(self):
        next_rollup = time.monotonic()
        while not self._stop.is_set():
            try:
                self.refresh()
                self.last_error = False
            except Exception:
                self.last_error = True
            if time.monotonic() >= next_rollup:
                try:
                    self.store.build_rollups()
                    next_rollup = time.monotonic() + 300
                except Exception:
                    self.last_error = True
            self._stop.wait(self.interval)

    def refresh(self):
        host = self.store.host(self.host_id)
        if not host or not host["host"] or not self.token_file:
            raise RuntimeError("Agent sampling is not configured")
        token = Path(self.token_file).read_text(encoding="utf-8").strip()
        if not token:
            raise RuntimeError("Agent token is empty")
        url = f"http://{host['host']}:{host['agent_port']}/api/v1/system/summary"
        request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("Agent response too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Agent response is invalid")
        values = {FIELD_ALIASES.get(key, key): value for key, value in payload.items()}
        at = datetime.now(timezone.utc).replace(tzinfo=None)
        summary, metrics = normalize(values, self.host_id, at)
        self.store.save_sample(self.host_id, summary, metrics, at)
        return summary


def normalize(data: dict, host_id: str, at: datetime):
    interfaces = data.get("network_interfaces") or []
    if not isinstance(interfaces, list):
        interfaces = []
    interfaces = [{key: item[key] for key in (
        "interface", "name", "address", "ip", "is_uplink", "rx_rate_mbps", "tx_rate_mbps",
        "rx_bytes", "tx_bytes", "bytes_recv", "bytes_sent") if key in item}
                  for item in interfaces[:64] if isinstance(item, dict)]
    uplinks = [item for item in interfaces if item.get("is_uplink") is True]
    if not uplinks:
        uplinks = [item for item in interfaces if str(item.get("interface") or item.get("name") or "").startswith(
            ("eth", "ens", "enp", "eno", "wlan", "wlp", "bond"))]
    rx = sum(float(item.get("rx_rate_mbps") or 0) * 125_000 for item in uplinks)
    tx = sum(float(item.get("tx_rate_mbps") or 0) * 125_000 for item in uplinks)
    network_summary = data.get("network_summary")
    if isinstance(network_summary, dict):
        rx = float(network_summary.get("rx_bytes_per_sec") or rx)
        tx = float(network_summary.get("tx_bytes_per_sec") or tx)
        network_summary = {key: network_summary[key] for key in (
            "interfaces", "rx_bytes_per_sec", "tx_bytes_per_sec", "rx_bytes", "tx_bytes",
            "daily_rx_bytes", "daily_tx_bytes") if key in network_summary}
    else:
        network_summary = {"rx_bytes_per_sec": rx, "tx_bytes_per_sec": tx}
    rx = float(data.get("net_rx_rate") or rx)
    tx = float(data.get("net_tx_rate") or tx)
    metric_fields = {
        "cpu": "cpu_percent", "memory": "memory_percent", "disk": "disk_percent",
        "swap": "swap_percent", "load1": "load1", "load5": "load5", "load15": "load15",
    }
    metrics = {name: float(data.get(field) or 0) for name, field in metric_fields.items()}
    metrics.update(net_rx=rx, net_tx=tx)
    for name, field in (("net_rx_raw", "net_rx_bytes"), ("net_tx_raw", "net_tx_bytes"),
                        ("disk_read_raw", "disk_read_bytes"), ("disk_write_raw", "disk_write_bytes")):
        metrics[name] = float(data.get(field) or 0)
    for name in ("disk_read", "disk_write"):
        metrics[name] = float(data.get(f"{name}_rate") or 0)
    disks = data.get("disks")
    disk_devices = data.get("disk_devices")
    summary = {
        "server_id": host_id, "source": "agent", "timestamp": at.replace(tzinfo=timezone.utc).timestamp(),
        "agent_version": str(data.get("agent_version") or ""),
        "hostname": str(data.get("hostname") or ""), "platform": str(data.get("platform") or ""),
        "kernel": str(data.get("kernel") or ""),
        "metrics": {
            **metrics, "cpu_count": data.get("cpu_count") or 0,
            "memory_total": data.get("memory_total") or 0, "memory_used": data.get("memory_used") or 0,
            "memory_avail": data.get("memory_available") or data.get("memory_avail") or 0,
            "swap_total": data.get("swap_total") or 0, "swap_used": data.get("swap_used") or 0,
            "disk_total": data.get("disk_total") or 0, "disk_used": data.get("disk_used") or 0,
            "disk_avail": data.get("disk_avail") or 0, "uptime": data.get("uptime") or 0,
        },
        "disks": [item for item in disks[:64] if isinstance(item, dict)] if isinstance(disks, list) else [],
        "disk_devices": [item for item in disk_devices[:64] if isinstance(item, dict)] if isinstance(disk_devices, list) else [],
        "network_summary": network_summary, "network_interfaces": interfaces,
        "cached": False, "stale": False, "cache_age_seconds": 0,
    }
    return summary, metrics
