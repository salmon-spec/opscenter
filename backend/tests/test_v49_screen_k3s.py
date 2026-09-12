"""v4.8.5 大屏增强（BE-3）：K3s 优先聚合 + 双层服务健康 + ETag/304 + 5s 响应缓存。

运行（独立 DB 文件，避免与并行 agent 冲突）：
  cd backend
  New-Item -ItemType Directory -Force -Path .tmp
  $env:DATABASE_URL="sqlite:///./.tmp/local-test-be3.db"
  python -m pytest tests/test_v49_screen_k3s.py tests/test_v48_screen_summary.py tests/test_smoke.py -q

说明：
- k8s_client（BE-1 契约）尚未落地时，k3s 必须为 None + source_status.k3s=='unavailable'，
  且大屏现有字段照常返回 200 —— 未打桩的用例即验证该降级路径。
- services_dual 的名称匹配是启发式：plaza 服务名 == k8s Service 名（忽略大小写/首尾空白）。
"""
import time
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import Base, SessionLocal, app, engine
from app.models import PlazaHealthState, Server
from app import topology
from app.topology import _LAST_AGENT_SNAPSHOT, reset_screen_cache

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _LAST_AGENT_SNAPSHOT.clear()
    topology._WG_TOPOLOGY_SNAPSHOT = {"summary": {}, "generated_at": None, "partial_errors": []}
    topology._WG_TOPOLOGY_SNAPSHOT_AT = time.time()
    topology._WG_TOPOLOGY_REFRESHING = False
    reset_screen_cache()
    yield
    _LAST_AGENT_SNAPSHOT.clear()
    reset_screen_cache()


class FakeK8sClient:
    """按 BE-1 契约模拟 app.k8s_client.K8sClient（只读 status/list_*，失败抛脱敏异常）。"""

    def __init__(self, data=None, fail=False, mode="in_cluster"):
        self.data = data or {}
        self.fail = fail
        self.mode = mode

    def status(self):
        return {"mode": self.mode, "ok": not self.fail, "error": None,
                "base_url": "https://kubernetes.default.svc"}

    def _items(self, key):
        if self.fail:
            raise RuntimeError("模拟 K8s API 故障")
        return {"items": self.data.get(key, [])}

    def list_nodes(self):
        return self._items("nodes")

    def list_namespaces(self):
        return self._items("namespaces")

    def list_workloads(self, kind, ns=None):
        return self._items(kind)

    def list_pods(self, ns=None, field_selector=None, label_selector=None):
        return self._items("pods")

    def list_services(self, ns=None):
        return self._items("services")

    def list_pvcs(self, ns=None):
        return self._items("pvcs")

    def list_jobs(self, ns=None):
        return self._items("jobs")

    def list_cronjobs(self, ns=None):
        return self._items("cronjobs")

    def list_events(self, ns=None, field_selector=None):
        return self._items("events")

    def list_network_policies(self, ns=None):
        return self._items("netpols")


def k3s_fixture_data():
    """fixture：2 节点 1 ready、3 deployments 2 ready、5 pods（3 running/1 pending/1 failed）、
    1 pending PVC、1 suspended CronJob、1 成功 1 失败 Job、1 Warning 事件、netpol 覆盖 1/3。"""
    return {
        "nodes": [
            {"metadata": {"name": "master", "labels": {"node-role.kubernetes.io/control-plane": "true"}},
             "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
            {"metadata": {"name": "worker1", "labels": {}},
             "status": {"conditions": [{"type": "Ready", "status": "False"}]}},
        ],
        "namespaces": [{"metadata": {"name": n}} for n in ("default", "kube-system", "opscenter")],
        "deployments": [
            {"metadata": {"name": "web", "namespace": "opscenter"}, "spec": {"replicas": 2},
             "status": {"replicas": 2, "readyReplicas": 2}},
            {"metadata": {"name": "backend", "namespace": "opscenter"}, "spec": {"replicas": 1},
             "status": {"replicas": 1, "readyReplicas": 1}},
            {"metadata": {"name": "pending-app", "namespace": "kube-system"}, "spec": {"replicas": 3},
             "status": {"replicas": 3, "readyReplicas": 0}},
        ],
        "statefulsets": [],
        "daemonsets": [],
        "pods": [
            {"metadata": {"name": "web-1", "namespace": "opscenter"},
             "status": {"phase": "Running", "containerStatuses": [{"restartCount": 5}]}},
            {"metadata": {"name": "backend-1", "namespace": "opscenter"},
             "status": {"phase": "Running", "containerStatuses": [{"restartCount": 0}]}},
            {"metadata": {"name": "loki-0", "namespace": "opscenter"},
             "status": {"phase": "Running", "containerStatuses": [{"restartCount": 2}, {"restartCount": 0}]}},
            {"metadata": {"name": "pending-1", "namespace": "default"}, "status": {"phase": "Pending"}},
            {"metadata": {"name": "failed-1", "namespace": "default"},
             "status": {"phase": "Failed", "containerStatuses": [{"restartCount": 0}]}},
        ],
        "services": [
            {"metadata": {"name": "gitlab", "namespace": "opscenter"},
             "spec": {"clusterIP": "10.43.0.11", "ports": [{"port": 80}]}},
            {"metadata": {"name": "headless-svc", "namespace": "opscenter"},
             "spec": {"clusterIP": "None", "ports": [{"port": 80}]}},
        ],
        "pvcs": [{"metadata": {"name": "data-1", "namespace": "opscenter"}, "status": {"phase": "Pending"}}],
        "jobs": [
            {"metadata": {"name": "ok-job", "namespace": "opscenter"}, "status": {"succeeded": 1}},
            {"metadata": {"name": "bad-job", "namespace": "opscenter"}, "status": {"failed": 2}},
        ],
        "cronjobs": [
            {"metadata": {"name": "pg-backup", "namespace": "opscenter"},
             "spec": {"schedule": "0 2 * * *", "suspend": True},
             "status": {"lastScheduleTime": "2026-09-10T01:00:00Z"}},
        ],
        "events": [
            {"type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container",
             "count": 7, "lastTimestamp": "2026-09-10T02:00:00Z",
             "involvedObject": {"kind": "Pod", "name": "failed-1", "namespace": "default"},
             "metadata": {}},
            {"type": "Normal", "reason": "Scheduled", "message": "ok", "metadata": {}},
        ],
        "netpols": [{"metadata": {"name": "default-deny", "namespace": "opscenter"}}],
    }


def add_host(name, host, **extra):
    payload = {"name": name, "host": host, "ssh_port": 22, "auto_deploy_agent": False, **extra}
    r = client.post("/api/v2/servers", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


PLAZA_ITEMS = [
    {"key": "gitlab", "name": "GitLab", "enabled": True, "probe_enabled": True,
     "entry_url": "http://gitlab.example.test", "category": "devops", "server_host": ""},
    {"key": "nexus", "name": "Nexus", "enabled": True, "probe_enabled": True,
     "entry_url": "http://nexus.example.test", "category": "devops", "server_host": ""},
    {"key": "vaultwarden", "name": "Vaultwarden", "enabled": True, "probe_enabled": True,
     "entry_url": "http://vault.example.test", "category": "security", "server_host": ""},
    {"key": "broken", "name": "Broken", "enabled": True, "probe_enabled": True,
     "entry_url": "http://broken.example.test", "category": "devops", "server_host": ""},
]


def fake_load_plaza_items():
    return [dict(item) for item in PLAZA_ITEMS], {}


def seed_plaza_states():
    with SessionLocal() as db:
        db.add(PlazaHealthState(plaza_key="gitlab", stable_status="up",
                                last_latency_ms=12.34, last_checked_at=datetime.utcnow()))
        db.add(PlazaHealthState(plaza_key="nexus", stable_status="down",
                                last_error="connect timeout", last_checked_at=datetime.utcnow()))
        db.add(PlazaHealthState(plaza_key="vaultwarden", stable_status="unknown"))
        db.add(PlazaHealthState(plaza_key="broken", stable_status="up",
                                last_latency_ms=5.0, last_checked_at=datetime.utcnow()))
        db.commit()


def test_k3s_section_full_counts(monkeypatch):
    fake = FakeK8sClient(k3s_fixture_data())
    monkeypatch.setattr(topology, "_k8s_client_or_none", lambda: (fake, None))
    r = client.get("/api/v2/screen/summary")
    assert r.status_code == 200, r.text
    data = r.json()
    k3s = data["k3s"]
    assert k3s is not None
    assert data["source_status"]["k3s"] == "ok"
    assert set(k3s.keys()) == {
        "nodes", "workloads", "pods", "storage", "jobs", "cronjobs", "warning_events", "netpol",
    }
    assert k3s["nodes"] == {"total": 2, "ready": 1, "roles": ["control-plane", "worker"]}
    assert k3s["workloads"]["deployments"] == {"desired": 6, "ready": 3}
    assert k3s["workloads"]["statefulsets"] == {"desired": 0, "ready": 0}
    assert k3s["workloads"]["daemonsets"] == {"desired": 0, "ready": 0}
    assert k3s["pods"]["total"] == 5
    assert k3s["pods"]["running"] == 3
    assert k3s["pods"]["pending"] == 1
    assert k3s["pods"]["failed"] == 1
    assert k3s["pods"]["unknown"] == 0
    assert k3s["pods"]["restart_top"][0] == {"name": "web-1", "namespace": "opscenter", "restarts": 5}
    assert len(k3s["pods"]["restart_top"]) == 2
    assert k3s["storage"]["pvc"] == {"bound": 0, "pending": 1, "lost": 0}
    assert k3s["jobs"] == {"recent_success": 1, "recent_failed": 1}
    assert k3s["cronjobs"] == [{
        "name": "pg-backup", "namespace": "opscenter", "schedule": "0 2 * * *",
        "suspend": True, "last_schedule_time": "2026-09-10T01:00:00Z",
    }]
    assert len(k3s["warning_events"]) == 1
    assert k3s["warning_events"][0]["reason"] == "BackOff"
    assert k3s["netpol"] == {"covered": 1, "total": 3}
    # 响应级统一字段（§10）
    assert data["data_timestamp"]
    assert data["cached"] is False
    assert data["cache_age_seconds"] == 0.0
    assert set(data["source_status"].keys()) >= {"k3s", "hosts", "services", "wireguard"}


def test_k8s_unavailable_keeps_screen_alive(monkeypatch):
    monkeypatch.setattr(topology, "_k8s_client_or_none", lambda: (None, "ModuleNotFoundError"))
    r = client.get("/api/v2/screen/summary")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["k3s"] is None
    assert data["source_status"]["k3s"] == "unavailable"
    assert any("K3s" in p for p in data["partial_errors"])
    # 现有字段照常返回
    assert data["hosts_summary"]["total"] == 0
    assert "trends" in data and "servers" in data and "services" in data


def test_k8s_api_failure_degrades_to_unavailable(monkeypatch):
    fake = FakeK8sClient(k3s_fixture_data(), fail=True)
    monkeypatch.setattr(topology, "_k8s_client_or_none", lambda: (fake, None))
    r = client.get("/api/v2/screen/summary")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["k3s"] is None
    assert data["source_status"]["k3s"] == "unavailable"
    assert any(p.startswith("K3s ") for p in data["partial_errors"])


def test_k8s_disabled_mode(monkeypatch):
    fake = FakeK8sClient(k3s_fixture_data(), mode="disabled")
    monkeypatch.setattr(topology, "_k8s_client_or_none", lambda: (fake, None))
    data = client.get("/api/v2/screen/summary").json()
    assert data["k3s"] is None
    assert data["source_status"]["k3s"] == "disabled"


def test_services_dual_external_not_in_cluster(monkeypatch):
    add_host("node", "10.66.66.20")  # 无纳管主机时 services 保持旧空屏契约
    seed_plaza_states()
    monkeypatch.setattr("app.plaza._load_plaza_items", fake_load_plaza_items)
    fake = FakeK8sClient(k3s_fixture_data(), mode="kubeconfig")
    monkeypatch.setattr(topology, "_k8s_client_or_none", lambda: (fake, None))
    data = client.get("/api/v2/screen/summary").json()
    dual = data["services_dual"]
    assert len(dual) == 4
    by_name = {d["name"]: d for d in dual}
    gitlab = by_name["GitLab"]
    assert gitlab["status"] == "up"
    assert gitlab["external"] == {
        "ok": True, "latency_ms": 12.3, "url": "http://gitlab.example.test", "error": None,
    }
    assert gitlab["internal"] is None  # 不在集群内 → null=不可探测，不是 false
    nexus = by_name["Nexus"]
    assert nexus["status"] == "down"
    assert nexus["external"]["ok"] is False
    assert nexus["external"]["error"] == "connect timeout"
    assert nexus["internal"] is None
    assert by_name["Vaultwarden"]["external"] is None
    # 旧 services_summary 字段保持语义
    assert data["services_summary"] == {"total": 4, "up": 2, "down": 1, "incidents": 0}


def test_services_dual_internal_probe_success_and_failure(monkeypatch):
    add_host("node", "10.66.66.20")
    seed_plaza_states()
    monkeypatch.setattr("app.plaza._load_plaza_items", fake_load_plaza_items)
    fixture = k3s_fixture_data()
    fixture["services"] = [
        {"metadata": {"name": "gitlab", "namespace": "opscenter"},
         "spec": {"clusterIP": "10.43.0.11", "ports": [{"port": 80}]}},
        {"metadata": {"name": "broken", "namespace": "opscenter"},
         "spec": {"clusterIP": "10.43.0.99", "ports": [{"port": 8080}]}},
        {"metadata": {"name": "headless-svc", "namespace": "opscenter"},
         "spec": {"clusterIP": "None", "ports": [{"port": 80}]}},
    ]
    fake = FakeK8sClient(fixture)
    monkeypatch.setattr(topology, "_k8s_client_or_none", lambda: (fake, None))
    calls = []

    def fake_probe(host, port, timeout=2.0):
        calls.append((host, port))
        if host == "10.43.0.11":
            return True, 1.2, None
        return False, None, "ConnectionRefusedError"

    monkeypatch.setattr(topology, "_probe_tcp", fake_probe)
    data = client.get("/api/v2/screen/summary").json()
    by_name = {d["name"]: d for d in data["services_dual"]}
    assert by_name["GitLab"]["internal"] == {
        "ok": True, "latency_ms": 1.2, "via": "clusterip:10.43.0.11:80", "error": None,
    }
    assert by_name["Broken"]["internal"] == {
        "ok": False, "latency_ms": None, "via": "clusterip:10.43.0.99:8080",
        "error": "ConnectionRefusedError",
    }
    # k8s 无同名 Service（启发式匹配不到）→ 不探测、internal=None
    assert by_name["Nexus"]["internal"] is None
    assert by_name["Vaultwarden"]["internal"] is None
    assert len(calls) == 2


def _expire_screen_cache(seconds=10):
    """任务 D：TTL 内命中不再探 DB 指纹，写库后最坏晚 _SCREEN_CACHE_TTL(5s) 才失效。

    这里手动让响应缓存过期，才能验证"业务数据变了就失效"。
    """
    topology._SCREEN_CACHE["stored_at"] -= seconds


def test_etag_304_and_change():
    r1 = client.get("/api/v2/screen/summary")
    assert r1.status_code == 200, r1.text
    etag1 = r1.headers["ETag"]
    assert etag1.startswith('"') and etag1.endswith('"')
    body1 = r1.json()
    assert body1["cached"] is False
    assert body1["cache_age_seconds"] == 0.0
    assert body1["data_timestamp"]

    # 5s 内重复请求命中响应缓存
    r1b = client.get("/api/v2/screen/summary")
    assert r1b.status_code == 200
    body1b = r1b.json()
    assert body1b["cached"] is True
    assert body1b["cache_age_seconds"] >= 0.0

    # If-None-Match 命中 → 304 空体；W/ 弱校验前缀同样命中
    r304 = client.get("/api/v2/screen/summary", headers={"If-None-Match": etag1})
    assert r304.status_code == 304
    assert r304.content == b""
    assert r304.headers["ETag"] == etag1
    r304b = client.get("/api/v2/screen/summary", headers={"If-None-Match": f"W/{etag1}"})
    assert r304b.status_code == 304

    # 业务数据变化 → ETag 变化（缓存随 DB 水位自动失效；新语义下需缓存已过期才会探指纹）
    add_host("extra-node", "10.66.66.30")
    _expire_screen_cache()
    r2 = client.get("/api/v2/screen/summary", headers={"If-None-Match": etag1})
    assert r2.status_code == 200
    assert r2.headers["ETag"] != etag1
    assert r2.json()["hosts_summary"]["total"] == 1


def test_docker_hosts_count():
    assert client.get("/api/v2/screen/summary").json()["docker_hosts_count"] == 0
    id1 = add_host("docker1", "10.66.66.21")
    id2 = add_host("plain1", "10.66.66.22")
    with SessionLocal() as db:
        db.query(Server).filter(Server.id == uuid.UUID(id1)).update({"docker_available": True})
        db.commit()
    _expire_screen_cache()
    assert client.get("/api/v2/screen/summary").json()["docker_hosts_count"] == 1
    with SessionLocal() as db:
        db.query(Server).filter(Server.id == uuid.UUID(id2)).update({"docker_available": True})
        db.commit()
    _expire_screen_cache()
    assert client.get("/api/v2/screen/summary").json()["docker_hosts_count"] == 2


def test_backward_compatible_fields_preserved():
    r = client.get("/api/v2/screen/summary")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["generated_at"]
    assert set(data.keys()) >= {
        "generated_at", "freshness", "partial_errors",
        "hosts_summary", "containers_summary", "databases_summary",
        "services_summary", "logs_summary", "wireguard_summary", "alerts_summary",
        "servers", "services", "active_alerts", "trends",
    }
    assert data["hosts_summary"] == {"total": 0, "online": 0, "offline": 0, "stale": 0}
    assert data["containers_summary"] == {"running": 0, "stopped": 0, "unknown_hosts": 0}
    assert data["databases_summary"]["total"] == 0
    assert data["alerts_summary"]["firing"] == 0
    assert set(data["trends"].keys()) == {"cpu", "memory", "net_rx", "net_tx"}
    assert data["servers"] == []
    assert data["services"] == []
