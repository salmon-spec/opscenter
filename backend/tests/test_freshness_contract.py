"""数据新鲜度契约（计划 §8.3 / 任务 D2）。

覆盖 4 个此前缺新鲜度字段的端点：
    1. GET /api/v2/servers/{id}/overview
    2. GET /api/v2/servers
    3. GET /api/v2/services/plaza
    4. GET /api/v2/services/plaza/health-overview

断言口径：字段存在且语义正确 / 空数据与冷缓存下不报错地降级 / 旧字段与旧结构未被删。

本文件不依赖外部数据库：把 app.database.SessionLocal 指向进程内 SQLite，
因此即便全量套件跑在不可达的 PostgreSQL 上（161 errors 基线）也能通过。
plaza 的 _health_checks 被打桩，绝不发起真实探测。

    cd backend
    .venv/Scripts/python.exe -m pytest tests/test_freshness_contract.py -q
"""

import json
import os
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("OPS_AUTH_ENABLED", "false")
os.environ.setdefault("LOCAL_HOST", "127.0.0.1")

import app.database as database  # noqa: E402
import app.plaza as plaza  # noqa: E402
import app.system_control as system_control  # noqa: E402
from app.freshness import (  # noqa: E402
    METRIC_STALENESS_SECONDS, age_seconds, freshness_fields, freshness_headers, to_epoch,
)
from app.main import Base, app  # noqa: E402
from app.models import (  # noqa: E402
    MetricHistory, PlazaHealthState, PlazaProbeResult, PlazaServiceProfile, Server,
)

client = TestClient(app)

# 进程内 SQLite：与 /servers、overview、health-overview 的真实查询路径共用一套表
_SQLITE = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)

# 改造前就存在的字段（兼容性回归线：只许增不许删）
OVERVIEW_TOP_KEYS = {"server", "metrics", "k8s_node", "notes"}
OVERVIEW_SERVER_KEYS = {
    "id", "name", "host", "ssh_port", "ssh_user", "status", "tags", "remark",
    "agent_status", "agent_version", "agent_port", "agent_type", "log_agent_status",
    "lan_ip", "wireguard_ip", "preferred_management_channel", "management_address_override",
    "kubernetes_node_name", "node_role", "runtime_type",
    "last_seen", "last_online_at", "last_error",
}
SERVER_LIST_KEYS = {
    "id", "name", "host", "ssh_port", "ssh_user", "tags", "status", "docker_available",
    "is_local", "agent_type", "last_seen", "service_count", "has_credentials",
    "agent_status", "agent_port", "agent_version", "log_agent_status", "log_agent_version",
    "log_agent_error", "log_agent_checked_at", "remark", "last_error", "lan_ip",
    "wireguard_ip", "preferred_management_channel", "management_address_override",
    "cluster_id", "kubernetes_node_name", "node_role", "runtime_type",
}
PLAZA_ITEM_KEYS = {
    "id", "key", "name", "description", "server_id", "server_name", "entry_url", "url",
    "health_url", "category", "icon", "auth_mode", "has_credentials", "credential_count",
    "sort_order", "enabled", "manual", "scanned", "source", "service_id", "status",
    "http_status", "latency_ms", "health_error", "last_checked_at", "probe_enabled",
    "consecutive_failures", "active_incident_id", "silenced_until", "owner", "tags",
    "profile_updated_at",
}
HEALTH_OVERVIEW_KEYS = {"generated_at", "range_hours", "summary", "items"}

FRESHNESS_KEYS = {"data_timestamp", "cached", "cache_age_seconds",
                  "source_status", "partial_errors", "stale", "staleness_seconds"}
ITEM_FRESHNESS_KEYS = {"status_checked_at", "status_age_seconds", "status_source",
                       "stale", "staleness_seconds"}


@pytest.fixture()
def db(monkeypatch):
    """每次用例重建表，并把全局 SessionLocal 指向进程内 SQLite。"""
    Base.metadata.drop_all(bind=_SQLITE)
    Base.metadata.create_all(bind=_SQLITE)
    factory = sessionmaker(bind=_SQLITE)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with factory() as session:
        yield session


def _server(session, name, host, **fields):
    row = Server(name=name, host=host, enabled=True, **fields)
    session.add(row)
    session.commit()
    return row


def _probe_check(**overrides):
    payload = {"status": "up", "http_status": 200, "latency_ms": 5,
               "health_error": "", "checked_at": datetime.utcnow().isoformat()}
    payload.update(overrides)
    return payload


def _no_network_plaza(monkeypatch, checks):
    """打桩 plaza._health_checks：返回固定快照，绝不触发后台真实探测。"""
    monkeypatch.setattr(plaza, "_health_checks", lambda catalog: dict(checks))
    monkeypatch.setattr(plaza, "_cached_checks", {})
    monkeypatch.setattr(plaza, "_cached_at", 0.0)


# ── 新鲜度原语 ────────────────────────────────────────────────────────────────

def test_freshness_fields_semantics():
    now = time.time()
    fresh = freshness_fields(data_timestamp=now - 5, source_status={"metrics": "ok"})
    assert fresh["data_timestamp"] == pytest.approx(now - 5, abs=0.05)
    assert fresh["cached"] is False and fresh["cache_age_seconds"] == 0
    assert fresh["staleness_seconds"] == METRIC_STALENESS_SECONDS
    assert fresh["stale"] is False
    assert fresh["source_status"] == {"metrics": "ok"} and fresh["partial_errors"] == []

    aged = freshness_fields(data_timestamp=now - (METRIC_STALENESS_SECONDS + 1))
    assert aged["stale"] is True

    missing = freshness_fields(data_timestamp=None)  # 无数据：回退响应时刻且判为过期
    assert missing["stale"] is True
    assert missing["data_timestamp"] == pytest.approx(now, abs=2)

    no_ttl = freshness_fields(data_timestamp=None, staleness_seconds=None)
    assert no_ttl["stale"] is False and no_ttl["staleness_seconds"] is None


def test_freshness_age_accepts_datetime_epoch_and_iso_utc():
    naive_utc = datetime.utcnow()
    assert 0 <= age_seconds(naive_utc) < 5
    assert 0 <= age_seconds(naive_utc.isoformat()) < 5      # 无偏移 ISO 按 UTC 解释
    assert 0 <= age_seconds(naive_utc.isoformat() + "Z") < 5
    assert 0 <= age_seconds(time.time()) < 1
    assert age_seconds(None) is None
    assert age_seconds("") is None
    assert age_seconds("not-a-time") is None
    assert to_epoch(None) is None


def test_freshness_headers_are_json_scalars_with_dashed_names():
    head = freshness_headers(freshness_fields(
        data_timestamp=1000.0, cached=True, cache_age_seconds=12.5,
        source_status={"a": "b"}, staleness_seconds=None,
    ))
    assert head["X-Data-Timestamp"] == "1000.0"
    assert head["X-Cached"] == "true"
    assert head["X-Cache-Age-Seconds"] == "12.5"
    assert json.loads(head["X-Source-Status"]) == {"a": "b"}
    assert head["X-Stale"] == "false"
    assert head["X-Staleness-Seconds"] == "null"
    assert json.loads(head["X-Partial-Errors"]) == []


# ── 1. GET /api/v2/servers/{id}/overview ────────────────────────────────────

def test_server_overview_freshness_ok(db):
    srv = _server(db, "OV", "10.0.0.11")
    sampled = datetime.utcnow() - timedelta(seconds=10)
    db.add(MetricHistory(server_id=srv.id, metric="cpu", value=12.5, timestamp=sampled))
    db.commit()

    r = client.get(f"/api/v2/servers/{srv.id}/overview")
    assert r.status_code == 200
    body = r.json()
    assert OVERVIEW_TOP_KEYS <= set(body)                       # 旧顶层键未删
    assert FRESHNESS_KEYS <= set(body)
    assert set(body["server"]) == OVERVIEW_SERVER_KEYS          # 旧 server 子字段未删
    assert body["metrics"]["cpu"] == 12.5 and body["metrics"]["ts"]
    assert body["source_status"] == {"metrics": "ok", "k8s_node": "skipped"}
    assert body["stale"] is False
    assert body["staleness_seconds"] == METRIC_STALENESS_SECONDS
    assert body["cached"] is False and body["cache_age_seconds"] == 0
    assert body["partial_errors"] == []
    # data_timestamp = 最新一次指标采样时刻，而不是响应时刻
    assert body["data_timestamp"] == pytest.approx(to_epoch(sampled), abs=1)


def test_server_overview_stale_when_sample_too_old(db):
    srv = _server(db, "OLD", "10.0.0.12")
    db.add(MetricHistory(server_id=srv.id, metric="cpu", value=1.0,
                         timestamp=datetime.utcnow() - timedelta(
                             seconds=METRIC_STALENESS_SECONDS + 60)))
    db.commit()
    body = client.get(f"/api/v2/servers/{srv.id}/overview").json()
    assert body["source_status"]["metrics"] == "ok"
    assert body["stale"] is True
    assert body["metrics"]["cpu"] == 1.0


def test_server_overview_empty_degrades_without_error(db, monkeypatch):
    srv = _server(db, "EMPTY", "10.0.0.13")
    monkeypatch.setattr(system_control, "_SUMMARY_CACHE", {})   # 摘要缓存也空
    r = client.get(f"/api/v2/servers/{srv.id}/overview")
    assert r.status_code == 200                                  # 无数据不报错
    body = r.json()
    assert body["source_status"]["metrics"] == "empty"
    assert body["stale"] is True
    assert body["metrics"]["cpu"] is None and body["metrics"]["ts"] is None
    assert any("暂无监控数据" in note for note in body["notes"])
    assert body["partial_errors"]


def test_server_overview_summary_cache_fallback_is_flagged(db, monkeypatch):
    srv = _server(db, "FB", "10.0.0.14")
    monkeypatch.setattr(system_control, "_SUMMARY_CACHE", {
        str(srv.id): {"data": {"metrics": {"cpu": 9.5}, "timestamp": time.time() - 30}},
    })
    body = client.get(f"/api/v2/servers/{srv.id}/overview").json()
    assert body["source_status"]["metrics"] == "fallback_summary_cache"
    assert body["metrics"]["cpu"] == 9.5
    assert body["stale"] is False
    assert any("回退" in note for note in body["notes"])
    assert body["partial_errors"]


def test_server_overview_k8s_node_source_status(db, monkeypatch):
    srv = _server(db, "K8S", "10.0.0.15", kubernetes_node_name="worker2")
    import app.server_details as server_details

    monkeypatch.setattr(server_details, "_lookup_k8s_node",
                        lambda name: ({"name": name, "status": "Ready"}, None))
    body = client.get(f"/api/v2/servers/{srv.id}/overview").json()
    assert body["source_status"]["k8s_node"] == "ok"

    monkeypatch.setattr(server_details, "_lookup_k8s_node", lambda name: (None, "K8s 暂不可用"))
    body2 = client.get(f"/api/v2/servers/{srv.id}/overview").json()
    assert body2["source_status"]["k8s_node"] == "unavailable"
    assert "K8s 暂不可用" in body2["partial_errors"]


# ── 2. GET /api/v2/servers（顶层数组，新鲜度走响应头） ───────────────────────

def test_servers_list_keeps_array_shape_and_adds_freshness_headers(db):
    _server(db, "A", "10.0.0.21")
    _server(db, "B", "10.0.0.22")
    r = client.get("/api/v2/servers")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and len(body) == 2            # 结构未变
    assert SERVER_LIST_KEYS <= set(body[0])                     # 旧字段未删
    assert float(r.headers["x-data-timestamp"]) == pytest.approx(time.time(), abs=10)
    assert r.headers["x-cached"] == "false"
    assert r.headers["x-cache-age-seconds"] == "0.0"
    assert json.loads(r.headers["x-source-status"]) == {"servers_db": "ok"}
    assert r.headers["x-stale"] == "false"                      # 无 TTL ⇒ 不判过期
    assert r.headers["x-staleness-seconds"] == "null"


# ── 3. GET /api/v2/services/plaza ───────────────────────────────────────────

def test_plaza_list_exposes_status_age_per_item(db, monkeypatch):
    now = datetime.utcnow()
    db.add(PlazaHealthState(plaza_key="gitlab", stable_status="up",
                            last_checked_at=now - timedelta(seconds=5)))
    db.add(PlazaHealthState(plaza_key="jenkins", stable_status="down",
                            last_checked_at=now - timedelta(minutes=30)))
    db.add(PlazaServiceProfile(plaza_key="pve", probe_enabled=False))
    db.commit()
    # 探测快照里 jenkins 是 up，但落库 stable_status 是 down ⇒ 必须暴露 stable 的时间
    _no_network_plaza(monkeypatch, {
        "gitlab": _probe_check(), "jenkins": _probe_check(status="up"),
        "pve": _probe_check(),
    })

    r = client.get("/api/v2/services/plaza")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and len(body) >= 3            # 结构未变
    by_key = {item["key"]: item for item in body}

    gitlab = by_key["gitlab"]
    assert PLAZA_ITEM_KEYS <= set(gitlab)                       # 旧字段未删
    assert ITEM_FRESHNESS_KEYS <= set(gitlab)
    assert gitlab["status"] == "up" and gitlab["status_source"] == "stable_state"
    assert gitlab["staleness_seconds"] == 180.0                 # 3 × 默认 60s 探测周期
    assert gitlab["stale"] is False
    assert 4 < gitlab["status_age_seconds"] < 30                # "5 秒前探的"
    assert to_epoch(gitlab["status_checked_at"]) == pytest.approx(
        to_epoch(now - timedelta(seconds=5)), abs=1)

    jenkins = by_key["jenkins"]
    assert jenkins["status"] == "down"                          # 落库状态优先于本轮探测
    assert jenkins["status_source"] == "stable_state"
    assert 1700 < jenkins["status_age_seconds"] < 1900          # "30 分钟前探的"
    assert jenkins["stale"] is True

    pve = by_key["pve"]
    assert pve["status"] == "disabled" and pve["status_source"] == "disabled"
    assert pve["stale"] is False and pve["staleness_seconds"] is None

    # 响应级聚合时刻：最新一次探测时刻
    assert json.loads(r.headers["x-source-status"]) == {"health_probe": "cold"}
    assert r.headers["x-cached"] == "false"
    assert r.headers["x-stale"] == "false"
    assert float(r.headers["x-data-timestamp"]) == pytest.approx(time.time(), abs=10)


def test_plaza_list_cold_cache_and_no_state_degrades(db, monkeypatch):
    _no_network_plaza(monkeypatch, {})                          # 冷缓存 + 无探测结果
    r = client.get("/api/v2/services/plaza")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and body
    assert all(item["status"] == "unknown" for item in body)
    assert all(item["status_source"] == "live_probe" for item in body)
    assert all(item["stale"] is True and item["status_age_seconds"] is None for item in body)
    assert r.headers["x-stale"] == "true"                       # 无时间戳 ⇒ 不新鲜
    assert json.loads(r.headers["x-source-status"]) == {"health_probe": "cold"}


def test_plaza_list_reports_cache_hit(db, monkeypatch):
    monkeypatch.setattr(plaza, "_health_checks", lambda catalog: {})
    monkeypatch.setattr(plaza, "_cached_checks", {"gitlab": _probe_check()})
    monkeypatch.setattr(plaza, "_cached_at", time.monotonic() - 12)
    r = client.get("/api/v2/services/plaza")
    assert r.headers["x-cached"] == "true"
    assert json.loads(r.headers["x-source-status"]) == {"health_probe": "cached"}
    assert 11 < float(r.headers["x-cache-age-seconds"]) < 25


# ── 4. GET /api/v2/services/plaza/health-overview ───────────────────────────

def test_plaza_health_overview_empty_degrades_and_keeps_generated_at(db, monkeypatch):
    monkeypatch.setattr(plaza, "_cached_checks", {})
    r = client.get("/api/v2/services/plaza/health-overview")
    assert r.status_code == 200
    body = r.json()
    assert HEALTH_OVERVIEW_KEYS <= set(body)                    # 旧键未删
    assert FRESHNESS_KEYS <= set(body)
    assert body["generated_at"] and body["range_hours"] == 24
    assert body["source_status"] == {"plaza_probe_results": "empty",
                                     "plaza_health_states": "empty",
                                     "live_probe_cache": "empty"}
    assert body["cached"] is False and body["cache_age_seconds"] == 0
    assert body["stale"] is True and body["staleness_seconds"] == 180.0
    assert body["data_timestamp"] == pytest.approx(time.time(), abs=10)
    assert body["items"]
    for item in body["items"]:
        assert ITEM_FRESHNESS_KEYS <= set(item)
        assert item["stale"] is True and item["status_age_seconds"] is None


def test_plaza_health_overview_distinguishes_fresh_and_stale_probes(db, monkeypatch):
    monkeypatch.setattr(plaza, "_cached_checks", {})
    probed = datetime.utcnow() - timedelta(seconds=20)
    db.add(PlazaProbeResult(plaza_key="gitlab", status="up", checked_at=probed, latency_ms=7.0))
    db.add(PlazaHealthState(plaza_key="gitlab", stable_status="up", last_checked_at=probed))
    db.add(PlazaProbeResult(plaza_key="jenkins", status="down",
                            checked_at=datetime.utcnow() - timedelta(minutes=20)))
    db.commit()

    body = client.get("/api/v2/services/plaza/health-overview?hours=24").json()
    by_key = {item["key"]: item for item in body["items"]}

    assert body["source_status"] == {"plaza_probe_results": "ok",
                                     "plaza_health_states": "ok",
                                     "live_probe_cache": "empty"}
    assert body["stale"] is False                               # 最新探测 20s 前 < 180s
    assert body["data_timestamp"] == pytest.approx(to_epoch(probed), abs=1)

    fresh = by_key["gitlab"]
    assert fresh["status"] == "up" and fresh["status_source"] == "stable_state"
    assert fresh["stale"] is False
    assert 10 < fresh["status_age_seconds"] < 40
    assert fresh["uptime_percent"] == 100.0

    stale = by_key["jenkins"]
    assert stale["status"] == "down" and stale["status_source"] == "live_probe"
    assert stale["status_age_seconds"] > 1000                    # 20 分钟 ≫ 180s
    assert stale["stale"] is True
