from datetime import datetime, timedelta, timezone
from collector import KubernetesReader


def test_not_ready_running_pod_is_not_healthy(monkeypatch):
    reader = KubernetesReader.__new__(KubernetesReader)
    node = {"metadata": {"name": "node"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
    pod = {"metadata": {"name": "app"}, "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "False"}]}}
    monkeypatch.setattr(reader, "get", lambda path: {"items": [node] if path.endswith('/nodes') else [pod] if path.endswith('/pods') else []})
    summary, findings = reader.collect()
    assert summary["status"] == "degraded" and summary["data"]["pods"]["unready"] == 1
    assert next(t['value'] for t in reader.alert_targets if t['metric']=='k3s_pod_failed') == 1
    assert any(item['title']=='Pod 未就绪' for item in findings)


def test_recovered_nodes_and_old_warnings_do_not_degrade_current_health(monkeypatch):
    reader = KubernetesReader.__new__(KubernetesReader)
    node = {"metadata": {"name": "node"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
    now = datetime.now(timezone.utc)
    events = [{"metadata": {"uid": "recent"}, "lastTimestamp": now.isoformat(), "involvedObject": {"kind": "Node", "name": "node"}},
              {"metadata": {"uid": "old"}, "lastTimestamp": (now-timedelta(hours=1)).isoformat()}]
    monkeypatch.setattr(reader, "get", lambda path: {"items": [node] if path.endswith('/nodes') else events if '/events?' in path else []})
    summary, findings = reader.collect()
    assert summary["status"] == "ok" and findings == [] and summary['data']['warning_events'] == 0
