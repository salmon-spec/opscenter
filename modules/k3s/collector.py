from __future__ import annotations

import json
import os
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.request import Request, urlopen


def _observed_at(event):
    value = event.get("series", {}).get("lastObservedTime") or event.get("lastTimestamp") or event.get("eventTime") or event.get("metadata", {}).get("creationTimestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def _pod_unhealthy(pod, now):
    status, meta = pod.get("status", {}), pod.get("metadata", {})
    if status.get("phase") == "Succeeded" or meta.get("deletionTimestamp"):
        return False
    if status.get("phase") in {"Failed", "Unknown"}:
        return True
    created = _observed_at({"eventTime": meta.get("creationTimestamp")})
    if created and (now - created).total_seconds() < 120:
        return False
    return status.get("phase") != "Running" or not any(
        c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", []))


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
        unready_pods = [pod for pod in pods if _pod_unhealthy(pod, now)]
        pod_by_name = {(p.get("metadata", {}).get("namespace", "default"), p.get("metadata", {}).get("name")): p for p in pods}
        node_by_name = {n.get("metadata", {}).get("name"): n for n in nodes}
        recent_events = []
        for event in events:
            at = _observed_at(event)
            if at is None or not 0 <= (now - at).total_seconds() <= 300:
                continue
            obj = event.get("involvedObject", {})
            pod = pod_by_name.get((obj.get("namespace", "default"), obj.get("name")))
            node = node_by_name.get(obj.get("name"))
            if obj.get("kind") == "Pod" and pod and not _pod_unhealthy(pod, now):
                continue
            if obj.get("kind") == "Node" and node and node in ready_nodes:
                continue
            recent_events.append(event)
        events = sorted(recent_events, key=_observed_at, reverse=True)
        summary = {
            "status": "unavailable" if not nodes else "degraded" if len(ready_nodes) != len(nodes) or unready_pods or unbound_pvcs else "ok",
            "collected_at": now.isoformat(),
            "stale_after_seconds": 120,
            "data": {
                "nodes": {"total": len(nodes), "ready": len(ready_nodes)},
                "pods": {"total": len(pods), "running": len(running_pods), "failed": len(failed_pods), "unready": len(unready_pods)},
                "pvcs": {"total": len(pvcs), "unbound": len(unbound_pvcs)},
                "warning_events": len(events),
            },
        }
        findings = [{
            "id": f"pod-ready:{pod.get('metadata', {}).get('uid') or pod.get('metadata', {}).get('namespace', 'default') + '/' + pod.get('metadata', {}).get('name', 'unknown')}",
            "resource_id": f"{pod.get('metadata', {}).get('namespace', 'default')}/Pod/{pod.get('metadata', {}).get('name', 'unknown')}",
            "severity": "warning", "title": "Pod 未就绪",
            "summary": f"Pod {pod.get('metadata', {}).get('name', 'unknown')} 无法提供就绪服务，阶段 {pod.get('status', {}).get('phase', 'Unknown')}",
            "observed_at": now.isoformat(),
        } for pod in unready_pods[:100]]
        alert_targets = []
        restart_counts = {}
        restart_windows = {}
        for node in nodes:
            name = node.get('metadata', {}).get('name', 'unknown')
            ready = any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in node.get('status', {}).get('conditions', []))
            alert_targets.append({'id': f'node/{name}', 'name': name, 'metric': 'k3s_node_ready', 'value': int(ready), 'observed_at': now.isoformat(), 'url': '/kubernetes/overview'})
        for pod in pods:
            meta, status = pod.get('metadata', {}), pod.get('status', {})
            name = f"{meta.get('namespace', 'default')}/{meta.get('name', 'unknown')}"
            containers = status.get('containerStatuses', []) + status.get('initContainerStatuses', [])
            failed = _pod_unhealthy(pod, now)
            alert_targets.append({'id': f"pod/{meta.get('uid') or name}", 'name': name, 'metric': 'k3s_pod_failed', 'value': int(failed), 'observed_at': now.isoformat(), 'url': '/kubernetes/pods'})
            for container in containers:
                key = f"{meta.get('uid') or name}/{container.get('name', 'container')}"
                count = container.get('restartCount')
                old = getattr(self, 'previous_restarts', {}).get(key)
                restart_counts[key] = count
                increase = max(0, count - old) if isinstance(count, int) and isinstance(old, int) else None
                window = [(at, value) for at, value in getattr(self, 'previous_restart_windows', {}).get(key, []) if now.timestamp() - at <= 300]
                if increase is not None:
                    window.append((now.timestamp(), increase))
                restart_windows[key] = window
                alert_targets.append({'id': f'restart/{key}', 'name': f"{name}/{container.get('name', '')}",
                                      'metric': 'k3s_restarts', 'value': sum(value for _, value in window) if increase is not None else None,
                                      'observed_at': now.isoformat(), 'url': '/kubernetes/pods'})
        for pvc in pvcs:
            meta = pvc.get('metadata', {})
            name = f"{meta.get('namespace', 'default')}/{meta.get('name', 'unknown')}"
            alert_targets.append({'id': f"pvc/{meta.get('uid') or name}", 'name': name, 'metric': 'k3s_pvc_pending', 'value': int(pvc.get('status', {}).get('phase') != 'Bound'), 'observed_at': now.isoformat(), 'url': '/kubernetes/storage'})
        self.alert_targets = alert_targets
        self.restart_counts = restart_counts
        self.restart_windows = restart_windows
        for event in events[:max(0, 100 - len(findings))]:
            involved = event.get("involvedObject", {})
            observed_at = _observed_at(event).isoformat()
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
        self._targets = []
        self._restart_counts = {}
        self._restart_windows = {}
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
            reader = self.reader_factory()
            with self._lock:
                previous = self._value[0]['collected_at'] if self._value else None
                try:
                    at = datetime.fromisoformat(str(previous).replace('Z', '+00:00'))
                    recent = (datetime.now(timezone.utc) - at).total_seconds() <= 120
                except (ValueError, TypeError):
                    recent = False
                reader.previous_restarts = self._restart_counts if recent else {}
                reader.previous_restart_windows = self._restart_windows if recent else {}
            value = reader.collect()
        except Exception:
            with self._lock:
                self._error = True
        else:
            with self._lock:
                self._value = value
                self._targets = getattr(reader, 'alert_targets', [])
                self._restart_counts = getattr(reader, 'restart_counts', {})
                self._restart_windows = getattr(reader, 'restart_windows', {})
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

    def alert_targets(self):
        with self._lock:
            if self._value is None:
                return {'status': 'unavailable', 'collected_at': datetime.now(timezone.utc), 'items': [], 'error': '首次采集中'}
            summary = self._value[0]
            return {'status': summary['status'], 'collected_at': summary['collected_at'],
                    'items': self._targets, 'error': '采集失败' if self._error else None}


snapshot = KubernetesSnapshot()


def summary_provider():
    summary, _ = snapshot.get()
    return summary


def findings_provider():
    summary, findings = snapshot.get()
    return {"status": summary["status"], "collected_at": summary["collected_at"], "items": findings, "error": summary.get("error")}
