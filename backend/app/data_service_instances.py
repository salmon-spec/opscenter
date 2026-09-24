"""额外监控实例（v5.3.0）。

每种中间件的 ``data_service_configs`` 记录仍是**主实例**：它继续驱动系统内部
集成（Redis Store、任务队列、事件总线、Nacos、MinIO、MongoDB）。本模块管理的
``data_service_instances`` 是**仅用于监控**的额外实例，可同类多开。

设计约束：
* 额外实例的采集不修改任何模块级全局变量，客户端按目标分桶，避免并发串台。
* 返回的指标结构与主实例保持一致（``{key,label,value,unit,dimensions,source}``），
  维度里带 ``instance_id`` / ``instance``，供时间序列按实例过滤。
* 数据浏览类能力（keys/queues/topics/...）仍只对主实例开放。
"""

from __future__ import annotations

import itertools
import json
import time
import uuid
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from app import config
from app import data_service_config as profiles
from app.auth import require_operator
from app.credential_crypto import decrypt_secret, encrypt_secret
from app.database import get_db
from app.models import DataServiceConfig, DataServiceInstance
from app.services import redis_store

router = APIRouter(prefix="/api/v2/data-services", tags=["data-service-instances"])

_KINDS: tuple[str, ...] = profiles._KINDS
_NAMES: dict[str, str] = profiles._NAMES
_MAX_PER_KIND = 20
_PROBE_TIMEOUT = float(config.MW_PROBE_TIMEOUT or 4.0)


# ── 行与载荷 ──────────────────────────────────────────────────


def _decrypt_row(row: DataServiceInstance) -> dict[str, Any]:
    if row is None or not row.config_ciphertext:
        return {}
    try:
        value = json.loads(decrypt_secret(row.config_ciphertext))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="实例配置无法解密，请检查 CREDENTIAL_KEY") from exc
    return value if isinstance(value, dict) else {}


def _merge_instance_payload(kind: str, payload: dict | None,
                            existing: dict[str, Any] | None) -> dict[str, Any]:
    """额外实例不接受环境变量兜底：只保留字段默认值，必填项必须显式提供。"""
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="配置内容必须是 JSON 对象")
    specs = {field["key"]: field for field in profiles._FIELDS[kind]}
    unknown = sorted(set(payload) - set(specs) - {"name", "monitor_enabled"})
    if unknown:
        raise HTTPException(status_code=422, detail=f"不支持的配置项: {', '.join(unknown)}")
    result = dict(existing or {})
    for key, field in specs.items():
        if key in payload:
            value = payload[key]
            if field.get("secret") and (value is None or str(value).strip() == ""):
                continue
            result[key] = profiles._parse_value(kind, key, value, field)
        elif key not in result and "default" in field:
            result[key] = field["default"]
    result.setdefault("enabled", True)
    if kind == "minio":
        endpoint, inferred_secure = profiles._normalize_endpoint(result.get("endpoint"))
        result["endpoint"] = endpoint
        if "secure" not in result and inferred_secure is not None:
            result["secure"] = inferred_secure
    for key, field in specs.items():
        if field.get("required") and not str(result.get(key) or "").strip():
            raise HTTPException(status_code=422, detail=f"{field['label']} 不能为空")
    return result


def instance_item(row: DataServiceInstance, values: dict[str, Any] | None = None) -> dict:
    values = values if values is not None else _decrypt_row(row)
    kind = str(row.kind)
    visible, secret_configured, masked_values = profiles._safe_values(kind, values)
    return {
        "id": str(row.id),
        "kind": kind,
        "name": row.name,
        "is_primary": False,
        "configured": profiles._is_configured(kind, values),
        "monitor_enabled": bool(row.monitor_enabled),
        "enabled": bool(values.get("enabled", True)),
        "source": "instance",
        "updated_at": profiles._iso(row.config_updated_at),
        "values": visible,
        "secret_configured": secret_configured,
        "masked_values": masked_values,
        "fields": profiles._field_specs(kind),
    }


def _instance_row(db, instance_id: str) -> DataServiceInstance:
    try:
        key = uuid.UUID(str(instance_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="实例不存在")
    row = db.query(DataServiceInstance).filter(DataServiceInstance.id == key).first()
    if row is None:
        raise HTTPException(status_code=404, detail="实例不存在")
    return row


def _name_conflict(db, kind: str, name: str, *, exclude_id: Any = None) -> bool:
    query = db.query(DataServiceInstance).filter(
        DataServiceInstance.kind == kind, DataServiceInstance.name == name,
    )
    if exclude_id is not None:
        query = query.filter(DataServiceInstance.id != exclude_id)
    return query.first() is not None


# ── 解析：主实例 + 额外实例 ───────────────────────────────────


def primary_profile(kind: str) -> dict[str, Any]:
    """主实例的生效配置（数据库优先，其次环境变量）——只读。"""
    with get_db() as db:
        row = profiles._stored_row(db, kind)
        values, _ = profiles._effective(kind, row)
    return values


def primary_item(kind: str) -> dict:
    with get_db() as db:
        row = profiles._stored_row(db, kind)
        values, stored = profiles._effective(kind, row)
        item = profiles._item(kind, values, stored)
    item.update({"id": "primary", "is_primary": True, "monitor_enabled": True})
    return item


def instance_rows(kind: str | None = None) -> list[DataServiceInstance]:
    with get_db() as db:
        query = db.query(DataServiceInstance)
        if kind:
            query = query.filter(DataServiceInstance.kind == kind)
        return query.order_by(DataServiceInstance.kind.asc(), DataServiceInstance.created_at.asc()).all()


def resolve_target(kind: str, instance_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 (values, meta)。instance_id 为空/"primary" 时指向主实例。"""
    wanted = str(instance_id or "").strip()
    if not wanted or wanted == "primary":
        return primary_profile(kind), {"id": "primary", "name": f"{_NAMES[kind]}(默认)",
                                       "is_primary": True, "monitor_enabled": True}
    with get_db() as db:
        row = _instance_row(db, wanted)
        if row.kind != kind:
            raise HTTPException(status_code=404, detail="实例不属于该数据服务")
        values = _decrypt_row(row)
    return values, {"id": str(row.id), "name": row.name, "is_primary": False,
                    "monitor_enabled": bool(row.monitor_enabled)}


def monitor_targets(kind: str) -> list[dict[str, Any]]:
    """采样用：主实例 + 所有启用监控的额外实例。"""
    targets = [{"meta": {"id": "primary", "name": f"{_NAMES[kind]}(默认)",
                         "is_primary": True, "monitor_enabled": True},
                "values": primary_profile(kind)}]
    with get_db() as db:
        rows = (db.query(DataServiceInstance)
                .filter(DataServiceInstance.kind == kind,
                        DataServiceInstance.monitor_enabled == True)  # noqa: E712
                .order_by(DataServiceInstance.created_at.asc())
                .all())
        for row in rows:
            targets.append({
                "meta": {"id": str(row.id), "name": row.name, "is_primary": False,
                         "monitor_enabled": True},
                "values": _decrypt_row(row),
            })
    return targets


# ── 额外实例采集器 ────────────────────────────────────────────


def _dims(values: dict[str, Any], meta: dict[str, Any], **extra: Any) -> dict[str, Any]:
    dims = {"instance": meta.get("name") or meta.get("id"), "instance_id": str(meta.get("id"))}
    dims.update(extra)
    return dims


def _row(key: str, label: str, value: Any, unit: str, dims: dict[str, Any],
         source: str) -> dict | None:
    if value is None or value == "":
        return None
    return {"key": key, "label": label, "value": value, "unit": unit, "dimensions": dims,
            "source": source}


def _collect_rabbitmq(values: dict[str, Any], meta: dict[str, Any]) -> tuple[list[dict], list[str]]:
    management = str(values.get("management_url") or "").strip()
    if not management:
        return [], ["未配置 Management URL，无法采集队列指标"]
    rows: list[dict] = []
    errors: list[str] = []
    base = management.rstrip("/")
    with httpx.Client(base_url=base, timeout=_PROBE_TIMEOUT) as client:
        try:
            overview = client.get("/api/overview").json()
        except Exception as exc:
            return [], [f"overview: {exc}"]
        for key, label, path, unit in (
            ("rabbitmq_messages_ready", "待消费消息", ("queue_totals", "messages_ready"), "count"),
            ("rabbitmq_messages_unacked", "未确认消息", ("queue_totals", "messages_unacknowledged"), "count"),
            ("rabbitmq_messages_total", "消息总数", ("queue_totals", "messages"), "count"),
            ("rabbitmq_consumers", "消费者数", ("object_totals", "consumers"), "count"),
            ("rabbitmq_connections", "连接数", ("object_totals", "connections"), "count"),
            ("rabbitmq_queues", "队列数", ("object_totals", "queues"), "count"),
        ):
            node: Any = overview
            for part in path:
                node = node.get(part) if isinstance(node, dict) else None
            row = _row(key, label, node, unit, _dims(values, meta), "management")
            if row:
                rows.append(row)
        try:
            queues = client.get("/api/queues?page=1&page_size=200&columns=name,messages,messages_ready,"
                                "messages_unacknowledged,consumers").json().get("items") or []
        except Exception as exc:
            errors.append(f"queues: {exc}")
            queues = []
        for item in queues[:50]:
            name = str(item.get("name") or "")
            if not name:
                continue
            dims = _dims(values, meta, queue=name)
            for key, label, field in (
                ("rabbitmq_queue_messages", "队列消息数", "messages"),
                ("rabbitmq_queue_ready", "队列待消费", "messages_ready"),
                ("rabbitmq_queue_unacked", "队列未确认", "messages_unacknowledged"),
                ("rabbitmq_queue_consumers", "队列消费者", "consumers"),
            ):
                row = _row(key, label, item.get(field), "count", dims, "management")
                if row:
                    rows.append(row)
    return rows, errors


def _collect_kafka(values: dict[str, Any], meta: dict[str, Any]) -> tuple[list[dict], list[str]]:
    servers = [item.strip() for item in str(values.get("bootstrap") or "").split(",") if item.strip()]
    if not servers:
        return [], ["未配置 Bootstrap Servers"]
    rows: list[dict] = []
    errors: list[str] = []
    admin = None
    try:
        from kafka.admin import KafkaAdminClient

        timeout_ms = max(1000, int(_PROBE_TIMEOUT * 1000))
        admin = KafkaAdminClient(bootstrap_servers=servers, request_timeout_ms=timeout_ms)
        topics = admin.list_topics(timeout=_PROBE_TIMEOUT)
        rows.append(_row("kafka_topics_total", "Topic 数", len(topics), "count",
                         _dims(values, meta), "admin"))
        partitions = 0
        under_replicated = 0
        for name in topics:
            if str(name).startswith("__"):
                continue
            try:
                detail = admin.describe_topics([name])
            except Exception:
                continue
            for topic in detail or []:
                for partition in topic.get("partitions") or []:
                    partitions += 1
                    replicas = partition.get("replicas") or partition.get("isr") or []
                    isr = partition.get("isr") or []
                    if len(isr) < len(replicas):
                        under_replicated += 1
        rows.append(_row("kafka_partitions_total", "分区数", partitions, "count",
                         _dims(values, meta), "admin"))
        rows.append(_row("kafka_partitions_under_replicated", "副本不足分区",
                         under_replicated, "count", _dims(values, meta), "admin"))
        try:
            groups = admin.list_consumer_groups()
            group_rows = groups[0] if isinstance(groups, tuple) else groups
            rows.append(_row("kafka_consumer_groups_total", "消费组数", len(group_rows or []),
                             "count", _dims(values, meta), "admin"))
        except Exception as exc:
            errors.append(f"consumer groups: {exc}")
    except Exception as exc:
        errors.append(f"kafka admin: {exc}")
    finally:
        try:
            if admin is not None:
                admin.close()
        except Exception:
            pass
    return rows, errors


def _collect_zookeeper(values: dict[str, Any], meta: dict[str, Any]) -> tuple[list[dict], list[str]]:
    hosts = str(values.get("hosts") or "").strip()
    if not hosts:
        return [], ["未配置集群地址"]
    rows: list[dict] = []
    client = None
    try:
        from kazoo.client import KazooClient

        client = KazooClient(hosts=hosts, timeout=max(1.0, _PROBE_TIMEOUT), read_only=True)
        client.start(timeout=max(1.0, _PROBE_TIMEOUT))
        rows.append(_row("zookeeper_connected", "连接状态", 1, "bool", _dims(values, meta), "zk"))
        try:
            data, stat = client.get("/")
            rows.append(_row("zookeeper_root_children", "根节点子节点数",
                             getattr(stat, "numChildren", None), "count", _dims(values, meta), "zk"))
            rows.append(_row("zookeeper_root_data_bytes", "根节点数据大小", len(data or b""), "B",
                             _dims(values, meta), "zk"))
        except Exception:
            pass
        count = 0
        try:
            _, stat = client.get("/")
            frontier = [f"/{child}" for child in (client.get_children("/") or [])[:200]]
            count = len(frontier)
            while frontier and count < 5000:
                path = frontier.pop()
                children = client.get_children(path) or []
                count += len(children)
                frontier.extend(f"{path}/{child}" for child in children[:50])
        except Exception:
            pass
        rows.append(_row("zookeeper_znode_count", "ZNode 数", count, "count",
                         _dims(values, meta), "zk"))
    except Exception as exc:
        return rows, [f"zookeeper: {exc}"]
    finally:
        if client is not None:
            try:
                client.stop()
                client.close()
            except Exception:
                pass
    return rows, []


def _nacos_params(client: httpx.Client, values: dict[str, Any]) -> dict[str, str]:
    username = str(values.get("username") or "")
    if not username:
        return {}
    try:
        resp = client.post("/nacos/v1/auth/login",
                           data={"username": username, "password": str(values.get("password") or "")})
        token = (resp.json() or {}).get("accessToken") if resp.status_code == 200 else None
        if token:
            return {"accessToken": str(token)}
    except Exception:
        pass
    return {"username": username, "password": str(values.get("password") or "")}


def _collect_nacos(values: dict[str, Any], meta: dict[str, Any]) -> tuple[list[dict], list[str]]:
    url = str(values.get("url") or "").strip()
    if not url:
        return [], ["未配置服务地址"]
    rows: list[dict] = []
    errors: list[str] = []
    with httpx.Client(base_url=url.rstrip("/"), timeout=_PROBE_TIMEOUT) as client:
        params = _nacos_params(client, values)
        group = str(values.get("group") or "DEFAULT_GROUP")
        try:
            services = client.get("/nacos/v1/ns/service/list",
                                  params={**params, "pageNo": 1, "pageSize": 1000,
                                          "groupName": ""}).json()
            names = services.get("doms") or []
            rows.append(_row("nacos_services_total", "服务数", services.get("count", len(names)),
                             "count", _dims(values, meta), "openapi"))
            healthy = 0
            total_instances = 0
            for name in names[:100]:
                try:
                    detail = client.get("/nacos/v1/ns/instance/list",
                                        params={**params, "serviceName": name}).json()
                except Exception:
                    continue
                hosts = detail.get("hosts") or []
                total_instances += len(hosts)
                healthy += sum(1 for item in hosts if item.get("healthy"))
            if names:
                rows.append(_row("nacos_instances_total", "实例数", total_instances, "count",
                                 _dims(values, meta), "openapi"))
                rows.append(_row("nacos_healthy_instances", "健康实例数", healthy, "count",
                                 _dims(values, meta), "openapi"))
                rows.append(_row("nacos_unhealthy_instances", "异常实例数",
                                 max(0, total_instances - healthy), "count", _dims(values, meta), "openapi"))
        except Exception as exc:
            errors.append(f"services: {exc}")
        try:
            configs = client.get("/nacos/v1/cs/configs",
                                 params={**params, "search": "accurate", "pageNo": 1,
                                         "pageSize": 1, "group": group, "dataId": ""}).json()
            rows.append(_row("nacos_configs_total", "配置数", configs.get("totalCount"),
                             "count", _dims(values, meta), "openapi"))
        except Exception as exc:
            errors.append(f"configs: {exc}")
    return rows, errors


def _collect_minio(values: dict[str, Any], meta: dict[str, Any]) -> tuple[list[dict], list[str]]:
    endpoint = str(values.get("endpoint") or "").strip()
    if not endpoint:
        return [], ["未配置服务地址"]
    rows: list[dict] = []
    errors: list[str] = []
    client = None
    try:
        from minio import Minio

        client = Minio(endpoint,
                       access_key=str(values.get("access_key") or "") or None,
                       secret_key=str(values.get("secret_key") or "") or None,
                       secure=bool(values.get("secure", False)))
        buckets = [item.name for item in client.list_buckets()]
        rows.append(_row("minio_buckets_total", "Bucket 数", len(buckets), "count",
                         _dims(values, meta), "minio"))
        total_objects = total_bytes = 0
        for name in buckets[:10]:
            try:
                objects = list(itertools.islice(client.list_objects(name, recursive=True), 2000))
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                continue
            size = sum(int(getattr(item, "size", 0) or 0) for item in objects)
            total_objects += len(objects)
            total_bytes += size
            dims = _dims(values, meta, bucket=name)
            rows.append(_row("minio_bucket_object_count_sampled", "Bucket 对象数（采样）",
                             len(objects), "count", dims, "minio"))
            rows.append(_row("minio_bucket_size_bytes_sampled", "Bucket 容量（采样）", size, "B",
                             dims, "minio"))
        if buckets:
            rows.append(_row("minio_object_count_sampled", "对象数（采样）", total_objects, "count",
                             _dims(values, meta), "minio"))
            rows.append(_row("minio_size_bytes_sampled", "容量（采样）", total_bytes, "B",
                             _dims(values, meta), "minio"))
    except Exception as exc:
        return rows, errors + [f"minio: {exc}"]
    return rows, errors


def _collect_mongodb(values: dict[str, Any], meta: dict[str, Any]) -> tuple[list[dict], list[str]]:
    url = str(values.get("url") or "").strip()
    if not url:
        return [], ["未配置连接 URL"]
    rows: list[dict] = []
    errors: list[str] = []
    client = None
    try:
        import pymongo

        timeout_ms = max(1000, int(_PROBE_TIMEOUT * 1000))
        client = pymongo.MongoClient(url, serverSelectionTimeoutMS=timeout_ms,
                                     connectTimeoutMS=timeout_ms, appname="opscenter-instance-monitor")
        server = client.admin.command("serverStatus")
        connections = server.get("connections") or {}
        rows.append(_row("mongodb_connections_current", "当前连接数",
                         connections.get("current"), "count", _dims(values, meta), "serverStatus"))
        rows.append(_row("mongodb_connections_available", "可用连接数",
                         connections.get("available"), "count", _dims(values, meta), "serverStatus"))
        for key, label, field in (("mongodb_op_insert", "插入次数", "insert"),
                                  ("mongodb_op_query", "查询次数", "query"),
                                  ("mongodb_op_update", "更新次数", "update"),
                                  ("mongodb_op_delete", "删除次数", "delete")):
            value = (server.get("opcounters") or {}).get(field)
            rows.append(_row(key, label, value, "count", _dims(values, meta), "serverStatus"))
        rows.append(_row("mongodb_mem_resident_mb", "常驻内存",
                         (server.get("mem") or {}).get("resident"), "MB", _dims(values, meta),
                         "serverStatus"))
        rows.append(_row("mongodb_uptime_seconds", "运行时长", server.get("uptime"), "s",
                         _dims(values, meta), "serverStatus"))
        database = str(values.get("db") or "").strip()
        if database:
            stats = client[database].command("dbStats")
            dims = _dims(values, meta, database=database)
            rows.append(_row("mongodb_db_data_size_bytes", "数据大小", stats.get("dataSize"), "B",
                             dims, "dbStats"))
            rows.append(_row("mongodb_db_storage_size_bytes", "存储大小", stats.get("storageSize"),
                             "B", dims, "dbStats"))
            rows.append(_row("mongodb_db_index_size_bytes", "索引大小", stats.get("indexSize"), "B",
                             dims, "dbStats"))
            rows.append(_row("mongodb_db_collections", "集合数", stats.get("collections"), "count",
                             dims, "dbStats"))
            rows.append(_row("mongodb_db_objects", "文档数", stats.get("objects"), "count",
                             dims, "dbStats"))
    except Exception as exc:
        return rows, errors + [f"mongodb: {exc}"]
    finally:
        try:
            if client is not None:
                client.close()
        except Exception:
            pass
    return rows, errors


_EXTRA_COLLECTORS = {
    "rabbitmq": _collect_rabbitmq,
    "kafka": _collect_kafka,
    "zookeeper": _collect_zookeeper,
    "nacos": _collect_nacos,
    "minio": _collect_minio,
    "mongodb": _collect_mongodb,
}


def collect_instance(kind: str, values: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """采集单个额外实例：返回 {available, latency_ms, version, endpoint, metrics, errors}。"""
    started = time.perf_counter()
    if not bool(values.get("enabled", True)):
        return {"available": False, "latency_ms": None, "version": "", "endpoint": "",
                "metrics": [], "errors": [], "disabled": True}
    if kind == "redis":
        profile = dict(values)
        status = redis_store.status(profile)
        metrics = redis_store.monitor_metrics(profile)
        return {
            "available": bool(status.get("available")),
            "latency_ms": status.get("latency_ms"),
            "version": str((status.get("detail") or {}).get("version") or ""),
            "endpoint": str((status.get("detail") or {}).get("endpoint") or ""),
            "metrics": metrics,
            "errors": ([] if status.get("available") else [status.get("error") or "不可用"]),
        }
    rows, errors = _EXTRA_COLLECTORS[kind](values, meta)
    latency = round((time.perf_counter() - started) * 1000, 1)
    return {
        "available": bool(rows) and not (errors and not rows),
        "latency_ms": latency,
        "version": "",
        "endpoint": _endpoint_label(kind, values),
        "metrics": rows,
        "errors": errors,
    }


def _endpoint_label(kind: str, values: dict[str, Any]) -> str:
    if kind == "redis":
        return redis_store._endpoint(dict(values))
    if kind == "rabbitmq":
        return str(values.get("management_url") or values.get("url") or "")
    if kind == "kafka":
        return str(values.get("bootstrap") or "")
    if kind == "zookeeper":
        return str(values.get("hosts") or "")
    if kind == "nacos":
        return str(values.get("url") or "")
    if kind == "minio":
        return str(values.get("endpoint") or "")
    return str(values.get("url") or "")


# ── API ───────────────────────────────────────────────────────


def _unique_instance_name(db, kind: str, base_name: str) -> str:
    name = base_name[:80]
    index = 2
    while _name_conflict(db, kind, name):
        suffix = f" {index}"
        name = f"{base_name[:80 - len(suffix)]}{suffix}"
        index += 1
    return name


@router.get("/instances")
def list_data_service_instances(
    kind: str | None = Query(None),
    _operator: object = Depends(require_operator),
) -> dict:
    if kind:
        kind = profiles._known_kind(kind)
    items = []
    for row in instance_rows(kind):
        try:
            items.append(instance_item(row))
        except HTTPException:
            continue
    return {"items": items}


@router.post("/{kind}/instances/test")
def test_new_data_service_instance(
    kind: str,
    payload: dict,
    _operator: object = Depends(require_operator),
) -> dict:
    """Test an unsaved extra instance without applying it to process globals."""
    kind = profiles._known_kind(kind)
    values = _merge_instance_payload(kind, payload, None)
    meta = {"id": "pending", "name": str(payload.get("name") or "待保存实例"),
            "is_primary": False, "monitor_enabled": True}
    result = profiles._test_connection(kind, values)
    collected = collect_instance(kind, values, meta)
    result["metrics_total"] = len(collected.get("metrics") or [])
    return result


@router.post("/{kind}/instances")
def create_data_service_instance(
    kind: str,
    payload: dict,
    _operator: object = Depends(require_operator),
) -> dict:
    kind = profiles._known_kind(kind)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="实例名称不能为空")
    if len(name) > 80:
        raise HTTPException(status_code=422, detail="实例名称过长")
    with get_db() as db:
        existing = db.query(DataServiceInstance).filter(DataServiceInstance.kind == kind).count()
        if existing >= _MAX_PER_KIND:
            raise HTTPException(status_code=422, detail=f"每种数据服务最多 {_MAX_PER_KIND} 个实例")
        if _name_conflict(db, kind, name):
            raise HTTPException(status_code=409, detail="同类型下实例名称已存在")
        values = _merge_instance_payload(kind, payload, None)
        row = DataServiceInstance(
            kind=kind, name=name,
            monitor_enabled=bool(payload.get("monitor_enabled", True)),
            config_ciphertext=encrypt_secret(json.dumps(values, ensure_ascii=False)),
            config_updated_at=datetime.utcnow(),
        )
        db.add(row)
        try:
            db.commit()
            db.refresh(row)
        except SQLAlchemyError as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail="实例保存失败") from exc
    return instance_item(row, values)


@router.put("/instances/{instance_id}")
def update_data_service_instance(
    instance_id: str,
    payload: dict,
    _operator: object = Depends(require_operator),
) -> dict:
    with get_db() as db:
        row = _instance_row(db, instance_id)
        current = _decrypt_row(row)
        name = str(payload.get("name") or row.name).strip() or row.name
        if _name_conflict(db, row.kind, name, exclude_id=row.id):
            raise HTTPException(status_code=409, detail="同类型下实例名称已存在")
        values = _merge_instance_payload(row.kind, payload, current)
        row.name = name
        if "monitor_enabled" in payload:
            row.monitor_enabled = bool(payload.get("monitor_enabled"))
        row.config_ciphertext = encrypt_secret(json.dumps(values, ensure_ascii=False))
        row.config_updated_at = datetime.utcnow()
        try:
            db.commit()
            db.refresh(row)
        except SQLAlchemyError as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail="实例保存失败") from exc
    return instance_item(row, values)


@router.post("/instances/{instance_id}/promote")
def promote_data_service_instance(
    instance_id: str,
    _operator: object = Depends(require_operator),
) -> dict:
    """Promote an extra instance and keep the previous primary as an extra target."""
    with get_db() as db:
        row = _instance_row(db, instance_id)
        kind = row.kind
        selected = _decrypt_row(row)
        old_row = profiles._stored_row(db, kind)
        old_values, _ = profiles._effective(kind, old_row)
        now = datetime.utcnow()
        if profiles._is_configured(kind, old_values) and old_values != selected:
            old_name = _unique_instance_name(db, kind, f"{_NAMES[kind]}(原主实例)")
            db.add(DataServiceInstance(
                kind=kind, name=old_name, monitor_enabled=True,
                config_ciphertext=encrypt_secret(json.dumps(old_values, ensure_ascii=False)),
                config_updated_at=now,
            ))
        if old_row is None:
            old_row = DataServiceConfig(kind=kind)
            db.add(old_row)
        old_row.config_ciphertext = encrypt_secret(json.dumps(selected, ensure_ascii=False))
        old_row.config_updated_at = now
        db.delete(row)
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail="主实例切换失败") from exc
    profiles._apply_kind(kind, selected)
    return primary_item(kind)


@router.delete("/instances/{instance_id}")
def delete_data_service_instance(
    instance_id: str,
    _operator: object = Depends(require_operator),
) -> dict:
    with get_db() as db:
        row = _instance_row(db, instance_id)
        values = _decrypt_row(row)
        kind = row.kind
        db.delete(row)
        db.commit()
    if kind == "redis":
        redis_store.evict_instance_client(dict(values))
    return {"deleted": True, "id": str(instance_id)}


@router.post("/instances/{instance_id}/test")
def test_data_service_instance(
    instance_id: str,
    _operator: object = Depends(require_operator),
) -> dict:
    with get_db() as db:
        row = _instance_row(db, instance_id)
        kind = row.kind
        values = _decrypt_row(row)
        meta = {"id": str(row.id), "name": row.name, "is_primary": False, "monitor_enabled": True}
    result = profiles._test_connection(kind, values)
    collected = collect_instance(kind, values, meta)
    result["metrics_total"] = len(collected.get("metrics") or [])
    return result
