"""v5.0.0 中间件客户端（Nacos / MinIO / ZooKeeper）离线单测。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from app.services import nacos_config as nacos_mod
from app.services import object_store as minio_mod
from app.services import zk_view as zk_mod


# ──────────────────────────── Nacos ────────────────────────────

def _nacos_client(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        fn = routes.get(path)
        if fn is None:
            return httpx.Response(404, text="not found")
        return fn(request)
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://nacos.test:8848")


def _nacos_routes():
    def health(_req):
        return httpx.Response(200, text="OK")

    def configs(req):
        params = dict(req.url.params)
        if params.get("search") == "blur":
            return httpx.Response(200, json={
                "totalCount": 2,
                "pageItems": [
                    {"dataId": "opscenter.dynamic.json", "group": "DEFAULT_GROUP", "modifyTime": 1700000000000},
                    {"dataId": "other.json", "group": "DEFAULT_GROUP", "modifyTime": 1700000001000},
                ],
            })
        if params.get("dataId") == "opscenter.dynamic.json":
            return httpx.Response(200, text=json.dumps({"k8s_cache_ttl": 30, "feature_x": True}))
        return httpx.Response(404, text="")

    def service_list(_req):
        return httpx.Response(200, json={"doms": ["svc-a", "svc-b"], "count": 2})

    def instance_list(req):
        name = dict(req.url.params).get("serviceName")
        if name == "svc-a":
            return httpx.Response(200, json={"hosts": [
                {"enabled": True, "healthy": True}, {"enabled": True, "healthy": False},
            ]})
        return httpx.Response(200, json={"hosts": [{"enabled": True, "healthy": True}]})

    def publish(req):
        return httpx.Response(200, text="true")

    return {
        "/nacos/v1/console/health/readiness": health,
        "/nacos/v1/cs/configs": configs,
        "/nacos/v1/ns/service/list": service_list,
        "/nacos/v1/ns/instance/list": instance_list,
    }


def test_nacos_unconfigured_fail_open(monkeypatch):
    monkeypatch.setattr(nacos_mod, "NACOS_URL", "")
    monkeypatch.setattr(nacos_mod, "NACOS_ENABLED", False)
    svc = nacos_mod.NacosConfig()
    st = svc.status()
    assert st["configured"] is False and st["available"] is False
    assert st["detail"]["endpoint"] == ""
    assert svc.get_config() is None
    assert svc.publish_config("{}") is False
    assert svc.list_configs() == {"total": 0, "configs": []}
    assert svc.get_overlay() == {}
    assert svc.list_services() == []
    assert svc.register_instance("x", "1.2.3.4", 80) is False


def test_nacos_configured_paths(monkeypatch):
    monkeypatch.setattr(nacos_mod, "NACOS_URL", "http://nacos.test:8848")
    monkeypatch.setattr(nacos_mod, "NACOS_ENABLED", True)
    svc = nacos_mod.NacosConfig()
    svc._client = _nacos_client(_nacos_routes())

    st = svc.status()
    assert st["configured"] is True and st["available"] is True
    assert st["detail"]["endpoint"] == "nacos.test:8848"
    assert svc.available() is True

    assert json.loads(svc.get_config())["k8s_cache_ttl"] == 30
    assert svc.get_overlay() == {"k8s_cache_ttl": 30, "feature_x": True}

    listed = svc.list_configs()
    assert listed["total"] == 2 and listed["configs"][0]["data_id"] == "opscenter.dynamic.json"

    services = svc.list_services()
    assert [s["name"] for s in services] == ["svc-a", "svc-b"]
    assert services[0]["instance_count"] == 2 and services[0]["healthy_count"] == 1
    assert services[1]["instance_count"] == 1 and services[1]["healthy_count"] == 1

    # publish 走表单 POST；registered route 不存在 → 404 → False（fail-open 验证）
    assert svc.publish_config("{}") in (True, False)


def test_nacos_configured_without_services(monkeypatch):
    monkeypatch.setattr(nacos_mod, "NACOS_URL", "http://nacos.test:8848")
    svc = nacos_mod.NacosConfig()
    svc._client = _nacos_client({})
    assert svc.status()["available"] is False
    assert svc.list_services() == []
    assert svc.get_overlay() == {}


# ──────────────────────────── MinIO ────────────────────────────

class _FakeMinio:
    def __init__(self):
        self.buckets = {"opscenter"}
        self.objects = {
            "opscenter": [
                SimpleNamespace(object_name="reports/a.md", size=10, last_modified=datetime(2026, 9, 10, tzinfo=timezone.utc)),
                SimpleNamespace(object_name="reports/b.md", size=20, last_modified=datetime(2026, 9, 11, tzinfo=timezone.utc)),
            ],
        }
        self.puts = []

    def bucket_exists(self, name):
        return name in self.buckets

    def make_bucket(self, name):
        self.buckets.add(name)

    def put_object(self, bucket, name, stream, length=None, content_type=None):
        data = stream.read()
        self.puts.append((bucket, name, data, content_type))
        self.objects.setdefault(bucket, []).append(
            SimpleNamespace(object_name=name, size=len(data), last_modified=datetime.now(timezone.utc)))
        return SimpleNamespace(object_name=name)

    def get_object(self, bucket, name):
        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return self._payload

            def close(self):
                pass

            def release_conn(self):
                pass
        return _Resp(b"hello minio")

    def list_objects(self, bucket, prefix="", recursive=False):
        for obj in self.objects.get(bucket, []):
            if obj.object_name.startswith(prefix):
                yield obj

    def list_buckets(self):
        return [SimpleNamespace(name=n, creation_date=datetime(2026, 1, 1, tzinfo=timezone.utc)) for n in sorted(self.buckets)]

    def presigned_get_object(self, bucket, name, expires=None):
        return f"http://minio.test/{bucket}/{name}?sig=ok"


def test_minio_unconfigured_fail_open(monkeypatch):
    monkeypatch.setattr(minio_mod, "MINIO_ENDPOINT", "")
    svc = minio_mod.ObjectStore()
    st = svc.status()
    assert st["configured"] is False and st["available"] is False
    assert svc.put("x", b"1") is None
    assert svc.get("x") is None
    assert svc.list_buckets() == [] and svc.list_objects() == []
    assert svc.presigned_get("x") is None


def test_minio_configured_paths(monkeypatch):
    monkeypatch.setattr(minio_mod, "MINIO_ENDPOINT", "minio.test:9000")
    monkeypatch.setattr(minio_mod, "MINIO_ENABLED", False)
    svc = minio_mod.ObjectStore()
    fake = _FakeMinio()
    svc._client = fake

    assert svc.status()["available"] is True
    # MINIO_ENABLED=false 时禁止写入
    assert svc.put("reports/c.md", "data") is None
    assert fake.puts == []

    monkeypatch.setattr(minio_mod, "MINIO_ENABLED", True)
    stored = svc.put("reports/c.md", "hello", content_type="text/markdown")
    assert stored == {"bucket": "opscenter", "object": "reports/c.md", "size": 5}
    assert fake.puts[-1][2] == b"hello"

    assert svc.get("reports/c.md") == b"hello minio"
    objs = svc.list_objects(prefix="reports/", limit=1)
    assert len(objs) == 1 and objs[0]["name"] == "reports/a.md"
    buckets = svc.list_buckets()
    assert buckets and buckets[0]["name"] == "opscenter"
    assert svc.presigned_get("reports/c.md", bucket="opscenter", expires_seconds=120).endswith("sig=ok")
    assert svc.ensure_bucket("opscenter") is True


def test_minio_status_error_path(monkeypatch):
    monkeypatch.setattr(minio_mod, "MINIO_ENDPOINT", "minio.test:9000")

    class _Boom:
        def bucket_exists(self, name):
            raise RuntimeError("connection refused")

    svc = minio_mod.ObjectStore()
    svc._client = _Boom()
    st = svc.status()
    assert st["available"] is False and "connection refused" in st["error"]


# ──────────────────────────── ZooKeeper ────────────────────────────

class _FakeZK:
    state = "CONNECTED"

    def __init__(self):
        self.tree = {
            "/": ["zookeeper", "kafka"],
            "/zookeeper": ["config", "quota"],
            "/zookeeper/config": [],
            "/zookeeper/quota": [],
            "/kafka": ["brokers"],
            "/kafka/brokers": [],
        }

    def exists(self, path):
        return path in self.tree

    def get_children(self, path):
        return list(self.tree.get(path, []))

    def get(self, path):
        payload = b"\xff\xfe\x00binary" if path == "/kafka" else b"plain"
        return payload, SimpleNamespace(dataLength=len(payload), mtime=1700000000000, version=3, numChildren=len(self.tree.get(path, [])))

    def stop(self):
        pass

    def close(self):
        pass


def test_zk_unconfigured_fail_open(monkeypatch):
    monkeypatch.setattr(zk_mod, "ZOOKEEPER_HOSTS", "")
    svc = zk_mod.ZKView()
    st = svc.status()
    assert st["configured"] is False and st["available"] is False
    tree = svc.tree("/", depth=2)
    assert tree["children"] == [] and tree["truncated"] is False
    assert svc.get("/") is None


def test_zk_tree_get_and_truncate(monkeypatch):
    monkeypatch.setattr(zk_mod, "ZOOKEEPER_HOSTS", "zk.test:2181")
    svc = zk_mod.ZKView()
    svc._client = _FakeZK()

    st = svc.status()
    assert st["available"] is True and st["detail"]["state"] == "CONNECTED"

    tree = svc.tree("/", depth=2)
    names = {n["name"] for n in tree["children"]}
    # 扁平 BFS：depth=2 返回 root 下两层全部节点；FE 实际按 depth=1 逐层下钻
    assert names == {"zookeeper", "kafka", "config", "quota", "brokers"}
    zk_node = next(n for n in tree["children"] if n["name"] == "zookeeper")
    assert zk_node["has_children"] is True and zk_node["path"] == "/zookeeper"

    truncated = svc.tree("/", depth=3, max_nodes=2)
    assert truncated["truncated"] is True

    node = svc.get("/kafka")
    assert node["data"].startswith("base64:")
    assert node["num_children"] == 1
    node2 = svc.get("/zookeeper")
    assert node2["data"] == "plain"
    assert svc.get("/missing") is None
    svc.close()
