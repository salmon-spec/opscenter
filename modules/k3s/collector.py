from __future__ import annotations

import json
import os
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.request import Request, urlopen


class KubernetesReader:
    def __init__(self):
        self.base_url = os.environ["K8S_API_URL"].rstrip("/")
        self.token_file = os.environ["K8S_TOKEN_FILE"]
        self.ca_file = os.environ["K8S_CA_PATH"]
        self.timeout = float(os.getenv("K8S_TIMEOUT_SECONDS", "5"))

    def get(self, path: str) -> dict:
        with open(self.token_file, encoding="utf-8") as token_file:
            token = token_file.read().strip()
        request = Request(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        with urlopen(request, timeout=self.timeout, context=ssl.create_default_context(cafile=self.ca_file)) as response:
            return json.load(response)

    def collect(self) -> tuple[dict, list[dict]]:
        paths = (
            "/api/v1/nodes",
            "/api/v1/pods",
            "/api/v1/persistentvolumeclaims",
            "/api/v1/events?fieldSelector=type%3DWarning",
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            nodes, pods, pvcs, events = [result.get("items", []) for result in executor.map(self.get, paths)]

        ready_nodes = [
            node for node in nodes
            if any(condition.get("type") == "Ready" and condition.get("status") == "True"
                   for condition in node.get("status", {}).get("conditions", []))
        ]
        running_pods = [pod for pod in pods if pod.get("status", {}).get("phase") == "Running"]
        failed_pods = [pod for pod in pods if pod.get("status", {}).get("phase") == "Failed"]
        unbound_pvcs = [pvc for pvc in pvcs if pvc.get("status", {}).get("phase") != "Bound"]
        now = datetime.now(timezone.utc)
        summary = {
            "status": "unavailable" if not nodes else "degraded" if len(ready_nodes) != len(nodes) or failed_pods or unbound_pvcs or events else "ok",
            "collected_at": now.isoformat(),
            "stale_after_seconds": 120,
            "data": {
                "nodes": {"total": len(nodes), "ready": len(ready_nodes)},
                "pods": {"total": len(pods), "running": len(running_pods), "failed": len(failed_pods)},
                "pvcs": {"total": len(pvcs), "unbound": len(unbound_pvcs)},
                "warning_events": len(events),
            },
        }
        findings = []
        for event in events[:100]:
            involved = event.get("involvedObject", {})
            observed_at = event.get("eventTime") or event.get("lastTimestamp") or event.get("metadata", {}).get("creationTimestamp") or now.isoformat()
            findings.append({
                "id": event.get("metadata", {}).get("uid") or f"{involved.get('namespace', 'default')}:{involved.get('name', 'unknown')}:{event.get('reason', 'Warning')}",
                "resource_id": f"{involved.get('namespace', 'cluster')}/{involved.get('kind', 'object')}/{involved.get('name', 'unknown')}",
                "severity": "warning",
                "title": (event.get("reason") or "Kubernetes Warning")[:200],
                "summary": (event.get("message") or "")[:1000],
                "observed_at": observed_at,
            })
        return summary, findings


class KubernetesSnapshot:
    def __init__(self, reader_factory=KubernetesReader, interval=30):
        self.reader_factory = reader_factory
        self.interval = interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._value = None
        self._error = False

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="k3s-snapshot", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=6)

    def _run(self):
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self.interval)

    def refresh(self):
        try:
            value = self.reader_factory().collect()
        except Exception:
            with self._lock:
                self._error = True
        else:
            with self._lock:
                self._value = value
                self._error = False

    def get(self):
        with self._lock:
            value, error = self._value, self._error
        if value is None:
            now = datetime.now(timezone.utc).isoformat()
            detail = "K3s 数据源暂不可用" if error else "K3s 首次采集中"
            return {"status": "unavailable", "collected_at": now, "data": {}, "error": detail}, []
        summary, findings = value
        if error:
            summary = {**summary, "status": "degraded", "error": "K3s 数据源暂不可用，显示上次成功采集"}
        return summary, findings


snapshot = KubernetesSnapshot()


def summary_provider():
    summary, _ = snapshot.get()
    return summary


def findings_provider():
    summary, findings = snapshot.get()
    return {"status": summary["status"], "collected_at": summary["collected_at"], "items": findings, "error": summary.get("error")}
