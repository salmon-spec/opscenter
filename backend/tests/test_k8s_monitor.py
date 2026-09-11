"""K3s 只读监控测试（主控 BE-1 实现）：MockTransport 假 K8s API + 全端点只读断言。"""

import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from app import k8s_client as k8c
from app import k8s_monitor
from app.database import Base, engine
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_db_and_client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    k8s_monitor._CACHE.clear()
    with k8s_monitor._CACHE_LOCK:
        k8s_monitor._NODES["ts"] = 0.0
        k8s_monitor._NODES["items"] = []
        k8s_monitor._SOURCE_STATUS.clear()
    k8c.reset_client()
    yield
    k8c.reset_client()


# ── 假 K8s API fixtures ──────────────────────────────────────

NODES = {"items": [
    {"metadata": {"name": "k8s-master-01", "labels": {"node-role.kubernetes.io/control-plane": "true"},
                  "creationTimestamp": "2026-09-08T10:00:00Z"},
     "status": {"conditions": [{"type": "Ready", "status": "True"}],
                "allocatable": {"cpu": "4", "memory": "8Gi", "pods": "110"},
                "capacity": {"cpu": "4", "memory": "8Gi", "pods": "110"}}},
    {"metadata": {"name": "k8s-infra-01", "labels": {"node-role.kubernetes.io/worker": "true"},
                  "creationTimestamp": "2026-09-09T10:00:00Z"},
     "status": {"conditions": [{"type": "Ready", "status": "True"}, {"type": "MemoryPressure", "status": "True"}],
                "allocatable": {"cpu": "6", "memory": "12Gi", "pods": "110"},
                "capacity": {"cpu": "6", "memory": "12Gi", "pods": "110"}}},
]}

DEPLOYMENTS = {"items": [
    {"metadata": {"name": "web", "namespace": "opscenter", "creationTimestamp": "2026-09-09T08:00:00Z"},
     "spec": {"replicas": 2, "selector": {"matchLabels": {"app": "web"}},
              "template": {"spec": {"containers": [{"image": "opscenter/web:4.8.5"}]}}},
     "status": {"replicas": 2, "readyReplicas": 2, "availableReplicas": 2, "updatedReplicas": 2}},
    {"metadata": {"name": "backend", "namespace": "opscenter", "creationTimestamp": "2026-09-09T08:00:00Z"},
     "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "backend"}},
              "template": {"spec": {"containers": [{"image": "opscenter/backend:4.8.5"}]}}},
     "status": {"replicas": 1, "readyReplicas": 0, "availableReplicas": 0, "updatedReplicas": 1}},
    {"metadata": {"name": "runner", "namespace": "cicd", "creationTimestamp": "2026-09-09T08:00:00Z"},
     "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "runner"}},
              "template": {"spec": {"containers": [{"image": "gitlab-runner:v19"}]}}},
     "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1}},
]}

PODS = {"items": [
    {"metadata": {"name": "web-1", "namespace": "opscenter", "labels": {"app": "web"},
                  "creationTimestamp": "2026-09-09T08:00:00Z"},
     "spec": {"nodeName": "k8s-infra-01", "containers": [{"name": "web"}]},
     "status": {"phase": "Running", "qosClass": "Burstable", "podIP": "10.42.3.61",
                "containerStatuses": [{"name": "web", "ready": True, "restartCount": 3, "state": {"running": {}}}]}},
    {"metadata": {"name": "backend-1", "namespace": "opscenter", "labels": {"app": "backend"},
                  "creationTimestamp": "2026-09-09T08:00:00Z",
                  "ownerReferences": [{"kind": "ReplicaSet", "name": "backend-abc"}]},
     "spec": {"nodeName": "k8s-master-01", "containers": [{"name": "backend"}]},
     "status": {"phase": "Running", "qosClass": "Guaranteed", "podIP": "10.42.0.9",
                "containerStatuses": [{"name": "backend", "ready": True, "restartCount": 0, "state": {"running": {}}}]}},
    {"metadata": {"name": "failed-job-pod", "namespace": "cicd", "creationTimestamp": "2026-09-09T09:00:00Z"},
     "spec": {"nodeName": "k8s-infra-01", "containers": [{"name": "x"}]},
     "status": {"phase": "Failed", "containerStatuses": [{"name": "x", "ready": False, "restartCount": 0,
                                                          "state": {"terminated": {"reason": "Error"}}}]}},
    {"metadata": {"name": "pending-pod", "namespace": "tools", "creationTimestamp": "2026-09-10T01:00:00Z"},
     "spec": {"containers": [{"name": "y"}]},
     "status": {"phase": "Pending", "reason": "Unschedulable"}},
    {"metadata": {"name": "done-pod", "namespace": "tools", "creationTimestamp": "2026-09-10T01:00:00Z"},
     "spec": {"containers": [{"name": "z"}]},
     "status": {"phase": "Succeeded"}},
]}

PVC_PENDING = {"items": [
    {"metadata": {"name": "data-pvc", "namespace": "opscenter", "creationTimestamp": "2026-09-09T08:00:00Z"},
     "spec": {"storageClassName": "local-path", "volumeName": "pvc-abc"},
     "status": {"phase": "Pending"}},
]}

PV_LIST = {"items": [
    {"metadata": {"name": "pvc-abc", "creationTimestamp": "2026-09-09T08:00:00Z"},
     "spec": {"persistentVolumeReclaimPolicy": "Retain", "storageClassName": "local-path",
              "capacity": {"storage": "10Gi"},
              "claimRef": {"namespace": "opscenter", "name": "data-pvc"}},
     "status": {"phase": "Bound"}},
]}

CRONJOBS = {"items": [
    {"metadata": {"name": "gitlab-backup", "namespace": "cicd", "creationTimestamp": "2026-09-09T08:00:00Z"},
     "spec": {"schedule": "0 1 * * *", "suspend": False},
     "status": {"lastScheduleTime": "2026-09-10T01:30:00Z", "active": []}},
]}

EVENTS = {"items": [
    {"metadata": {"namespace": "opscenter"},
     "involvedObject": {"kind": "Pod", "name": "backend-1", "namespace": "opscenter"},
     "reason": "BackOff", "message": "Back-off restarting failed container",
     "count": 7, "type": "Warning",
     "firstTimestamp": "2026-09-10T02:00:00Z", "lastTimestamp": "2026-09-10T03:00:00Z"},
    {"metadata": {"namespace": "tools"},
     "involvedObject": {"kind": "Pod", "name": "done-pod", "namespace": "tools"},
     "reason": "Pulled", "message": "image pulled", "count": 1, "type": "Normal",
     "firstTimestamp": "2026-09-10T01:00:00Z", "lastTimestamp": "2026-09-10T01:00:00Z"},
]}


def _mock_handler(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET", f"只读客户端禁止 {request.method} 请求"
    path = request.url.path
    if path == "/version":
        return httpx.Response(200, json={"gitVersion": "v1.36.4+k3s1"})
    if path == "/api/v1/nodes":
        return httpx.Response(200, json=NODES)
    if path == "/api/v1/namespaces":
        return httpx.Response(200, json={"items": [
            {"metadata": {"name": ns, "creationTimestamp": "2026-09-08T10:00:00Z"}, "status": {"phase": "Active"}}
            for ns in ("default", "opscenter", "cicd")]})
    if path.startswith("/apis/apps/v1/deployments"):
        return httpx.Response(200, json=DEPLOYMENTS)
    if path.startswith("/apis/apps/v1/statefulsets"):
        return httpx.Response(200, json={"items": []})
    if path.startswith("/apis/apps/v1/daemonsets"):
        return httpx.Response(200, json={"items": []})
    if path == "/api/v1/pods":
        return httpx.Response(200, json=PODS)
    if path.startswith("/api/v1/services"):
        return httpx.Response(200, json={"items": [
            {"metadata": {"name": "web", "namespace": "opscenter", "creationTimestamp": "2026-09-09T08:00:00Z"},
             "spec": {"type": "ClusterIP", "clusterIP": "10.43.1.10", "ports": [{"port": 80, "protocol": "TCP"}]}}]})
    if path.startswith("/api/v1/endpoints"):
        return httpx.Response(200, json={"items": [
            {"metadata": {"name": "web", "namespace": "opscenter"},
             "subsets": [{"addresses": [{"ip": "10.42.3.61"}]}]}]})
    if path.startswith("/apis/networking.k8s.io/v1/ingresses"):
        return httpx.Response(200, json={"items": [
            {"metadata": {"name": "web", "namespace": "opscenter", "creationTimestamp": "2026-09-09T08:00:00Z"},
             "spec": {"rules": [{"host": "ops.example", "http": {"paths": [{"path": "/"}]}},
                                 {"host": "", "http": {"paths": []}}],
                      "tls": [{"hosts": ["ops.example"]}]}}]})
    if path == "/api/v1/persistentvolumeclaims":
        return httpx.Response(200, json=PVC_PENDING)
    if path == "/api/v1/persistentvolumes":
        return httpx.Response(200, json=PV_LIST)
    if path.startswith("/apis/batch/v1/jobs"):
        return httpx.Response(200, json={"items": [
            {"metadata": {"name": "backup-ok", "namespace": "cicd", "creationTimestamp": "2026-09-10T01:30:00Z"},
             "status": {"succeeded": 1, "failed": 0, "active": 0, "completionTime": "2026-09-10T01:40:00Z"}}]})
    if path.startswith("/apis/batch/v1/cronjobs"):
        return httpx.Response(200, json=CRONJOBS)
    if path.startswith("/api/v1/events"):
        return httpx.Response(200, json=EVENTS)
    if path.startswith("/apis/networking.k8s.io/v1/networkpolicies"):
        return httpx.Response(200, json={"items": [
            {"metadata": {"name": "allow-ops", "namespace": "opscenter", "creationTimestamp": "2026-09-09T08:00:00Z"},
             "spec": {"policyTypes": ["Ingress", "Egress"], "podSelector": {"matchLabels": {"app": "web"}}}}]})
    if path == "/apis/metrics.k8s.io/v1beta1/nodes":
        return httpx.Response(200, json={"items": [
            {"metadata": {"name": "k8s-master-01"}, "usage": {"cpu": "500m", "memory": "3221225472"}},
            {"metadata": {"name": "k8s-infra-01"}, "usage": {"cpu": "900m", "memory": "6442450944"}}]})
    if path == "/apis/metrics.k8s.io/v1beta1/pods":
        return httpx.Response(200, json={"items": []})
    if path.startswith("/apis/apps/v1/namespaces/") and "replicasets" in path:
        return httpx.Response(200, json={"items": [
            {"metadata": {"name": "backend-abc", "namespace": "opscenter",
                          "ownerReferences": [{"kind": "Deployment", "name": "backend"}]}}]})
    if path.startswith("/api/v1/namespaces/") and path.endswith("/log"):
        return httpx.Response(200, text="line-1\nline-2\n")
    if path.startswith("/api/v1/namespaces/") and "/pods/" in path:
        parts = [p for p in path.split("/") if p]
        # /api/v1/namespaces/{ns}/pods/{name}
        if len(parts) >= 6 and parts[-2] == "pods":
            name = parts[-1]
            for it in PODS["items"]:
                if it["metadata"]["name"] == name:
                    return httpx.Response(200, json=it)
            return httpx.Response(404, json={"message": f"pod {name} not found"})
        return httpx.Response(404, json={"message": "pod not found"})
    return httpx.Response(404, json={"kind": "Status", "message": f"not found: {path}"})


@pytest.fixture()
def k8s_env(monkeypatch):
    """env 模式客户端 + MockTransport。"""
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    handler = _mock_handler
    created = {}

    def fake_get_client():
        if "c" not in created:
            created["c"] = k8c.K8sClient(transport=httpx.MockTransport(handler),
                                         base_url="https://mock:6443", token="test-token")
        return created["c"]

    monkeypatch.setattr(k8c, "get_client", fake_get_client)
    monkeypatch.setattr(k8c, "list_nodes", lambda: fake_get_client().list_nodes())
    return fake_get_client


def _first_cluster_id() -> str:
    res = client.get("/api/v2/clusters")
    assert res.status_code == 200
    rows = res.json()
    assert isinstance(rows, list) and rows, "clusters 必须是裸数组且懒种子至少一条"
    return rows[0]["id"]


# ── 用例 ─────────────────────────────────────────────────────

def test_clusters_bare_array_and_lazy_seed(k8s_env):
    res1 = client.get("/api/v2/clusters")
    assert res1.status_code == 200
    rows = res1.json()
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert row["name"] == "k3s" and row["type"] == "k3s"
    assert row["api_mode"] == "env"
    assert row["status"] == "ok"
    assert "data_timestamp" not in row  # 裸数组，不是信封
    # 幂等：再次请求不重复种子
    res2 = client.get("/api/v2/clusters")
    assert len(res2.json()) == 1


def test_summary_envelope_and_counts(k8s_env):
    cid = _first_cluster_id()
    res = client.get(f"/api/v2/clusters/{cid}/summary")
    assert res.status_code == 200
    body = res.json()
    for key in ("data", "data_timestamp", "cached", "cache_age_seconds", "partial_errors", "source_status"):
        assert key in body
    data = body["data"]
    assert data["nodes"] == {"total": 2, "ready": 2, "roles": ["control-plane", "worker"]}
    assert data["workloads"]["deployments"] == {"desired": 4, "ready": 3}
    assert data["pods"]["running"] == 2 and data["pods"]["failed"] == 1 and data["pods"]["pending"] == 1
    assert data["pods"]["restart_top"][0]["name"] == "web-1" and data["pods"]["restart_top"][0]["restarts"] == 3
    assert data["storage"]["pvc"] == {"bound": 0, "pending": 1, "lost": 0}
    assert data["jobs"]["recent_success"] == 1
    assert data["events"]["warning_count"] == 1
    assert data["netpol"]["covered"] == 1 and data["netpol"]["total"] == 1
    assert body["data_timestamp"] > 1_700_000_000  # epoch 秒


def test_summary_cache_hit(k8s_env):
    cid = _first_cluster_id()
    r1 = client.get(f"/api/v2/clusters/{cid}/summary").json()
    r2 = client.get(f"/api/v2/clusters/{cid}/summary").json()
    assert r1["cached"] is False
    assert r2["cached"] is True
    assert r2["cache_age_seconds"] >= 0


def test_nodes_usage_and_metrics_unavailable(k8s_env, monkeypatch):
    cid = _first_cluster_id()
    res = client.get(f"/api/v2/clusters/{cid}/nodes")
    assert res.status_code == 200
    rows = res.json()["data"]
    by_name = {n["name"]: n for n in rows}
    assert by_name["k8s-master-01"]["usage"]["cpu"] == "0.50"
    assert by_name["k8s-master-01"]["pressure"]["cpu"] == 12.5
    assert by_name["k8s-infra-01"]["pressure"]["memory"] > 0
    # metrics 源 404 → usage=null + source_status 记录，不伪造 0
    def handler_404(request):
        assert request.method == "GET"
        if "metrics" in request.url.path:
            return httpx.Response(404, json={"message": "metrics API not found"})
        return _mock_handler(request)
    monkeypatch.setattr(k8c, "get_client", lambda: k8c.K8sClient(
        transport=httpx.MockTransport(handler_404), base_url="https://mock:6443", token="t"))
    k8s_monitor._CACHE.clear()  # 绕过节点缓存，强制重新采集
    res2 = client.get(f"/api/v2/clusters/{cid}/nodes")
    assert res2.status_code == 200
    body = res2.json()
    assert body["data"][0]["usage"] is None
    assert any("metrics" in e for e in body["partial_errors"])
    assert body["source_status"].get("metrics") == "unavailable"


def test_workloads_filters(k8s_env):
    cid = _first_cluster_id()
    res = client.get(f"/api/v2/clusters/{cid}/workloads?type=deployment")
    assert res.status_code == 200
    rows = res.json()["data"]
    assert len(rows) == 3
    res2 = client.get(f"/api/v2/clusters/{cid}/workloads?type=deployment&status=ready")
    assert [r["name"] for r in res2.json()["data"]] == ["runner", "web"]  # 按 namespace/name 排序
    res3 = client.get(f"/api/v2/clusters/{cid}/workloads?keyword=runner")
    assert [r["name"] for r in res3.json()["data"]] == ["runner"]
    assert client.get(f"/api/v2/clusters/{cid}/workloads?type=bat").status_code == 422


def test_pods_params_validation(k8s_env):
    cid = _first_cluster_id()
    res = client.get(f"/api/v2/clusters/{cid}/pods?phase=running")
    assert res.status_code == 200
    assert {r["name"] for r in res.json()["data"]} == {"web-1", "backend-1"}
    bad = client.get(f"/api/v2/clusters/{cid}/pods/UPPER../x")
    assert bad.status_code == 422
    assert client.get(f"/api/v2/clusters/{cid}/pods/opscenter/backend-1").status_code == 200
    assert client.get(f"/api/v2/clusters/{cid}/pods/opscenter/no-such-pod").status_code in (404, 502)


def test_pod_detail_owner_and_probes(k8s_env):
    cid = _first_cluster_id()
    res = client.get(f"/api/v2/clusters/{cid}/pods/opscenter/backend-1")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["owner"]["kind"] == "Deployment" and data["owner"]["name"] == "backend"
    assert data["containers"][0]["name"] == "backend"
    assert data["phase"] == "Running"


def test_pod_logs_constraints(k8s_env):
    cid = _first_cluster_id()
    res = client.get(f"/api/v2/clusters/{cid}/pods/opscenter/backend-1/logs")
    assert res.status_code == 200
    body = res.json()
    assert body["data"] == "line-1\nline-2\n"
    assert body["truncated"] is False
    assert client.get(f"/api/v2/clusters/{cid}/pods/opscenter/backend-1/logs",
                      params={"tail_lines": 1001}).status_code == 422
    ok = client.get(f"/api/v2/clusters/{cid}/pods/opscenter/backend-1/logs", params={"tail_lines": 1000})
    assert ok.status_code == 200
    assert client.get(f"/api/v2/clusters/{cid}/pods/opscenter/backend-1/logs",
                      params={"tail_lines": 0}).status_code == 422
    assert client.get(f"/api/v2/clusters/{cid}/pods/opscenter/backend-1/logs",
                      params={"since_seconds": 7200}).status_code == 422


def test_services_ingress_storage_jobs_netpol(k8s_env):
    cid = _first_cluster_id()
    svcs = client.get(f"/api/v2/clusters/{cid}/services").json()["data"]
    assert svcs[0]["exposed"] is True and svcs[0]["endpoints_ready"] == 1
    ings = client.get(f"/api/v2/clusters/{cid}/ingresses").json()["data"]
    assert ings[0]["hosts"] == ["ops.example"] and ings[0]["tls"]
    sto = client.get(f"/api/v2/clusters/{cid}/storage").json()["data"]
    assert sto["pvcs"][0]["phase"] == "Pending"
    assert sto["pvs"][0]["reclaim_policy"] == "Retain"
    jobs = client.get(f"/api/v2/clusters/{cid}/jobs").json()["data"]
    assert jobs["cronjobs"][0]["schedule"] == "0 1 * * *"
    assert jobs["recent_success"] == 1
    np = client.get(f"/api/v2/clusters/{cid}/network-policies").json()["data"]
    assert np["total"] == 1 and np["items"][0]["types"] == ["Ingress", "Egress"]


def test_events_filter(k8s_env):
    cid = _first_cluster_id()
    warn = client.get(f"/api/v2/clusters/{cid}/events", params={"type": "Warning"}).json()["data"]
    assert len(warn) == 1 and warn[0]["reason"] == "BackOff"
    all_events = client.get(f"/api/v2/clusters/{cid}/events", params={"type": "all"}).json()["data"]
    assert len(all_events) == 2


def test_unknown_cluster_404(k8s_env):
    assert client.get("/api/v2/clusters/00000000-0000-0000-0000-000000000001/summary").status_code == 404
    assert client.get("/api/v2/clusters/not-a-uuid/summary").status_code == 422


def test_mode_none_returns_503(monkeypatch):
    monkeypatch.setattr(k8c, "get_client", lambda: k8c.K8sClient(transport=httpx.MockTransport(_mock_handler)))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    res = client.get("/api/v2/clusters")
    assert res.status_code == 200  # 种子仍成功（mode none 也允许记录）
    cid = res.json()[0]["id"]
    assert client.get(f"/api/v2/clusters/{cid}/summary").status_code == 503


def test_get_cached_node_seam(k8s_env):
    cid = _first_cluster_id()
    client.get(f"/api/v2/clusters/{cid}/nodes")
    snap = k8s_monitor.get_cached_nodes_snapshot()
    assert snap and snap[0]["name"] in ("k8s-master-01", "k8s-infra-01")
    node = k8s_monitor.get_cached_node("k8s-master-01")
    assert node and node["ready"] is True and node["allocatable"]["cpu"] == "4"
