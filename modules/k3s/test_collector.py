from datetime import datetime, timezone

from collector import KubernetesReader, KubernetesSnapshot


def test_collects_read_only_summary_and_warning_findings(monkeypatch):
    monkeypatch.setenv("K8S_API_URL", "https://kubernetes.invalid")
    monkeypatch.setenv("K8S_TOKEN_FILE", "/unused/token")
    monkeypatch.setenv("K8S_CA_PATH", "/unused/ca.crt")
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "/api/v1/nodes": {"items": [
            {"status": {"conditions": [{"type": "Ready", "status": "True"}]}},
            {"status": {"conditions": [{"type": "Ready", "status": "False"}]}},
        ]},
        "/api/v1/pods": {"items": [
            {"status": {"phase": "Running"}}, {"status": {"phase": "Failed"}},
        ]},
        "/api/v1/persistentvolumeclaims": {"items": [
            {"status": {"phase": "Bound"}}, {"status": {"phase": "Pending"}},
        ]},
        "/api/v1/events?fieldSelector=type%3DWarning": {"items": [{
            "metadata": {"uid": "event-1"}, "type": "Warning", "reason": "FailedMount",
            "message": "volume unavailable", "eventTime": now,
            "involvedObject": {"namespace": "app", "kind": "Pod", "name": "web-1"},
        }]},
    }
    monkeypatch.setattr(KubernetesReader, "get", lambda self, path: data[path])

    summary, findings = KubernetesReader().collect()

    assert summary["status"] == "degraded"
    assert summary["data"] == {
        "nodes": {"total": 2, "ready": 1},
        "pods": {"total": 2, "running": 1, "failed": 1},
        "pvcs": {"total": 2, "unbound": 1},
        "warning_events": 1,
    }
    assert findings[0]["id"] == "event-1"
    assert findings[0]["resource_id"] == "app/Pod/web-1"


def test_snapshot_reuses_one_collection_and_keeps_last_success_after_failure():
    calls = []

    class Reader:
        def collect(self):
            calls.append(True)
            if len(calls) > 1:
                raise TimeoutError("secret upstream detail")
            return ({"status": "ok", "collected_at": "2026-10-14T00:00:00Z", "data": {"nodes": 5}}, [{"id": "warning-1"}])

    cache = KubernetesSnapshot(reader_factory=Reader)
    assert cache.get()[0]["status"] == "unavailable"
    cache.refresh()
    assert cache.get()[0]["data"] == {"nodes": 5}
    assert cache.get()[1] == [{"id": "warning-1"}]
    assert len(calls) == 1

    cache.refresh()
    summary, findings = cache.get()
    assert summary["status"] == "degraded"
    assert summary["collected_at"] == "2026-10-14T00:00:00Z"
    assert "secret upstream detail" not in summary["error"]
    assert findings == [{"id": "warning-1"}]
