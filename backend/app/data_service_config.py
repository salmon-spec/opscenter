"""Editable data-service connection configuration (v5.2.3).

Each middleware kind stores at most one encrypted connection profile.  Saving a
profile applies it to the in-process middleware clients immediately; resetting
falls back to the process environment captured at startup.

Secrets are intentionally write-only over the API: GET responses expose only a
blank value plus ``secret_configured`` metadata.  A blank secret on PUT keeps
the previously stored value.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app import config
from app.auth import require_operator
from app.credential_crypto import decrypt_secret, encrypt_secret
from app.database import get_db
from app.models import DataServiceConfig
from app.services import doc_store as doc_store_mod
from app.services import event_bus as event_bus_mod
from app.services import mq as mq_mod
from app.services import nacos_config as nacos_mod
from app.services import object_store as object_store_mod
from app.services import redis_store as redis_mod
from app.services import zk_view as zk_mod


router = APIRouter(prefix="/api/v2/data-services", tags=["data-service-config"])

_KINDS: tuple[str, ...] = ("redis", "rabbitmq", "kafka", "zookeeper", "nacos", "minio", "mongodb")
_NAMES = {
    "redis": "Redis",
    "rabbitmq": "RabbitMQ",
    "kafka": "Kafka",
    "zookeeper": "ZooKeeper",
    "nacos": "Nacos",
    "minio": "MinIO",
    "mongodb": "MongoDB",
}

_FIELDS: dict[str, list[dict[str, Any]]] = {
    "redis": [
        {"key": "url", "label": "连接 URL", "type": "password", "secret": True, "required": True,
         "placeholder": "redis://user:password@host:6379/0"},
        {"key": "enabled", "label": "启用监控", "type": "boolean", "default": True},
    ],
    "rabbitmq": [
        {"key": "url", "label": "AMQP URL", "type": "password", "secret": True, "required": True,
         "placeholder": "amqp://user:password@host:5672/"},
        {"key": "management_url", "label": "Management URL", "type": "password", "secret": True,
         "placeholder": "http://user:password@host:15672"},
        {"key": "queue", "label": "任务队列", "type": "text", "default": "opscenter.tasks",
         "placeholder": "opscenter.tasks"},
        {"key": "dlq", "label": "死信队列", "type": "text", "default": "opscenter.tasks.dlq",
         "placeholder": "opscenter.tasks.dlq"},
        {"key": "enabled", "label": "启用队列能力", "type": "boolean", "default": False},
    ],
    "kafka": [
        {"key": "bootstrap", "label": "Bootstrap Servers", "type": "text", "required": True,
         "placeholder": "kafka-1:9092,kafka-2:9092"},
        {"key": "topic", "label": "事件主题", "type": "text", "default": "opscenter.events",
         "placeholder": "opscenter.events"},
        {"key": "enabled", "label": "启用事件写入", "type": "boolean", "default": False},
    ],
    "zookeeper": [
        {"key": "hosts", "label": "集群地址", "type": "text", "required": True,
         "placeholder": "zk-1:2181,zk-2:2181,zk-3:2181"},
        {"key": "enabled", "label": "启用监控", "type": "boolean", "default": True},
    ],
    "nacos": [
        {"key": "url", "label": "服务地址", "type": "text", "required": True,
         "placeholder": "http://nacos:8848"},
        {"key": "group", "label": "默认 Group", "type": "text", "default": "DEFAULT_GROUP",
         "placeholder": "DEFAULT_GROUP"},
        {"key": "config_data_id", "label": "动态配置 Data ID", "type": "text",
         "default": "opscenter.dynamic.json", "placeholder": "opscenter.dynamic.json"},
        {"key": "username", "label": "用户名", "type": "text", "placeholder": "nacos"},
        {"key": "password", "label": "密码", "type": "password", "secret": True,
         "placeholder": "留空表示不修改"},
        {"key": "enabled", "label": "启用注册与动态配置", "type": "boolean", "default": False},
    ],
    "minio": [
        {"key": "endpoint", "label": "服务地址", "type": "text", "required": True,
         "placeholder": "minio.example.com:9000"},
        {"key": "access_key", "label": "Access Key", "type": "password", "secret": True,
         "placeholder": "留空表示不修改"},
        {"key": "secret_key", "label": "Secret Key", "type": "password", "secret": True,
         "placeholder": "留空表示不修改"},
        {"key": "bucket", "label": "默认 Bucket", "type": "text", "default": "opscenter",
         "placeholder": "opscenter"},
        {"key": "secure", "label": "使用 HTTPS", "type": "boolean", "default": False},
        {"key": "enabled", "label": "启用对象写入", "type": "boolean", "default": False},
    ],
    "mongodb": [
        {"key": "url", "label": "连接 URL", "type": "password", "secret": True, "required": True,
         "placeholder": "mongodb://user:password@host:27017/?authSource=admin"},
        {"key": "db", "label": "默认数据库", "type": "text", "default": "opscenter",
         "placeholder": "opscenter"},
        {"key": "enabled", "label": "启用文档写入", "type": "boolean", "default": False},
    ],
}

_SECRET_FIELDS = {
    kind: {field["key"] for field in fields if field.get("secret")}
    for kind, fields in _FIELDS.items()
}


def _snapshot_defaults() -> dict[str, dict[str, Any]]:
    return {
        "redis": {"url": redis_mod.REDIS_URL, "enabled": bool(redis_mod.MW_ENABLED)},
        "rabbitmq": {
            "url": mq_mod.MQ_URL,
            "management_url": mq_mod.MQ_MANAGEMENT_URL,
            "queue": mq_mod.MQ_QUEUE,
            "dlq": mq_mod.MQ_DLQ,
            "enabled": bool(mq_mod.MQ_ENABLED),
        },
        "kafka": {
            "bootstrap": config.KAFKA_BOOTSTRAP,
            "topic": config.KAFKA_TOPIC,
            "enabled": bool(config.KAFKA_ENABLED),
        },
        "zookeeper": {"hosts": zk_mod.ZOOKEEPER_HOSTS, "enabled": bool(zk_mod.ZOOKEEPER_HOSTS)},
        "nacos": {
            "url": nacos_mod.NACOS_URL,
            "group": nacos_mod.NACOS_GROUP,
            "config_data_id": nacos_mod.NACOS_CONFIG_DATA_ID,
            "username": nacos_mod.NACOS_USERNAME,
            "password": nacos_mod.NACOS_PASSWORD,
            "enabled": bool(nacos_mod.NACOS_ENABLED),
        },
        "minio": {
            "endpoint": object_store_mod.MINIO_ENDPOINT,
            "access_key": object_store_mod.MINIO_ACCESS_KEY,
            "secret_key": object_store_mod.MINIO_SECRET_KEY,
            "bucket": object_store_mod.MINIO_BUCKET,
            "secure": bool(object_store_mod.MINIO_SECURE),
            "enabled": bool(object_store_mod.MINIO_ENABLED),
        },
        "mongodb": {
            "url": config.MONGO_URL,
            "db": config.MONGO_DB,
            "enabled": bool(config.MONGO_ENABLED),
        },
    }


_ENV_DEFAULTS = _snapshot_defaults()
_APPLY_LOCK = __import__("threading").RLock()


def _known_kind(kind: str) -> str:
    value = str(kind or "").strip().lower()
    if value not in _KINDS:
        raise HTTPException(status_code=404, detail=f"未知数据服务: {kind}")
    return value


def _field_specs(kind: str) -> list[dict[str, Any]]:
    return [dict(field) for field in _FIELDS[kind]]


def _is_configured(kind: str, values: dict[str, Any]) -> bool:
    required = [field["key"] for field in _FIELDS[kind] if field.get("required")]
    return all(str(values.get(key) or "").strip() for key in required)


def _source(kind: str, stored: dict[str, Any] | None) -> str:
    if stored is not None:
        return "database"
    return "environment" if _is_configured(kind, _ENV_DEFAULTS[kind]) else "unconfigured"


def _iso(value: datetime | None) -> str:
    return value.isoformat() + "Z" if value else ""


def _redact_url(value: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.username and not parts.password:
        return raw
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    user = parts.username or ""
    netloc = f"{user}:***@{host}" if user else f"***@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _mask_error(kind: str, values: dict[str, Any], error: Any) -> str:
    text = str(error or "")[:500]
    for key in _SECRET_FIELDS[kind]:
        value = str(values.get(key) or "")
        if value:
            text = text.replace(value, "***")
            if key.endswith("url"):
                text = text.replace(_redact_url(value), "***")
                try:
                    parts = urlsplit(value)
                    for secret in (parts.password, parts.username):
                        if secret:
                            text = text.replace(secret, "***")
                except ValueError:
                    pass
    return text[:300]


def _decode_row(row: DataServiceConfig | None) -> dict[str, Any] | None:
    if row is None or not row.config_ciphertext:
        return None
    try:
        value = json.loads(decrypt_secret(row.config_ciphertext))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="数据服务配置无法解密，请检查 CREDENTIAL_KEY") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=500, detail="数据服务配置格式不合法")
    return value


def _stored_row(db, kind: str) -> DataServiceConfig | None:
    return db.query(DataServiceConfig).filter(DataServiceConfig.kind == kind).first()


def _normalize_endpoint(value: Any) -> tuple[str, bool | None]:
    text = str(value or "").strip().rstrip("/")
    secure: bool | None = None
    if text.startswith("https://"):
        secure = True
        text = text[8:]
    elif text.startswith("http://"):
        secure = False
        text = text[7:]
    return text, secure


def _parse_value(kind: str, key: str, value: Any, field: dict[str, Any]) -> Any:
    field_type = field.get("type")
    if field_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise HTTPException(status_code=422, detail=f"{field['label']} 必须是布尔值")
    text = str(value or "").strip()
    if field.get("required") and not text:
        raise HTTPException(status_code=422, detail=f"{field['label']} 不能为空")
    if kind == "minio" and key == "endpoint":
        endpoint, secure = _normalize_endpoint(text)
        if not endpoint or "," in endpoint or "://" in endpoint:
            raise HTTPException(status_code=422, detail="MinIO 服务地址格式不合法")
        return endpoint
    if kind in {"redis", "rabbitmq", "mongodb"} and key == "url":
        scheme = urlsplit(text).scheme.lower()
        allowed = {
            "redis": {"redis", "rediss"},
            "rabbitmq": {"amqp", "amqps"},
            "mongodb": {"mongodb", "mongodb+srv"},
        }[kind]
        if scheme not in allowed:
            raise HTTPException(status_code=422, detail=f"{field['label']} 协议格式不合法")
    if kind == "kafka" and key == "bootstrap":
        if any(not item.strip() or ":" not in item for item in text.split(",")):
            raise HTTPException(status_code=422, detail="Kafka Bootstrap Servers 格式不合法")
    if kind == "zookeeper" and key == "hosts":
        if any(not item.strip() or ":" not in item for item in text.split(",")):
            raise HTTPException(status_code=422, detail="ZooKeeper 集群地址格式不合法")
    return text


def _merge_payload(kind: str, payload: dict[str, Any] | None, existing: dict[str, Any] | None,
                   *, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="配置内容必须是 JSON 对象")
    result = dict(existing or defaults or {})
    if not result and defaults:
        result.update(defaults)
    specs = {field["key"]: field for field in _FIELDS[kind]}
    unknown = sorted(set(payload) - set(specs))
    if unknown:
        raise HTTPException(status_code=422, detail=f"不支持的配置项: {', '.join(unknown)}")
    for key, field in specs.items():
        if key in payload:
            value = payload[key]
            if field.get("secret") and (value is None or str(value).strip() == ""):
                continue
            result[key] = _parse_value(kind, key, value, field)
        elif key not in result and "default" in field:
            result[key] = field["default"]
    result.setdefault("enabled", True)
    if kind == "minio":
        endpoint, inferred_secure = _normalize_endpoint(result.get("endpoint"))
        result["endpoint"] = endpoint
        if "secure" not in result and inferred_secure is not None:
            result["secure"] = inferred_secure
    for key, field in specs.items():
        if field.get("required") and not str(result.get(key) or "").strip():
            raise HTTPException(status_code=422, detail=f"{field['label']} 不能为空")
    return result


def _masked_secret(key: str, value: Any) -> str:
    if not str(value or "").strip():
        return ""
    if key.endswith("url"):
        return _redact_url(str(value))
    return "••••••••"


def _safe_values(kind: str, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bool], dict[str, str]]:
    visible: dict[str, Any] = {}
    secret_configured: dict[str, bool] = {}
    masked: dict[str, str] = {}
    for field in _FIELDS[kind]:
        key = field["key"]
        if field.get("secret"):
            configured = bool(str(values.get(key) or "").strip())
            secret_configured[key] = configured
            masked[key] = _masked_secret(key, values.get(key))
            visible[key] = ""
        else:
            visible[key] = values.get(key, field.get("default"))
    return visible, secret_configured, masked


def _item(kind: str, values: dict[str, Any], row: DataServiceConfig | None) -> dict[str, Any]:
    visible, secret_configured, masked_values = _safe_values(kind, values)
    return {
        "kind": kind,
        "name": _NAMES[kind],
        "configured": _is_configured(kind, values),
        "enabled": bool(values.get("enabled", True)),
        "source": _source(kind, values if row is not None else None),
        "updated_at": _iso(row.config_updated_at if row is not None else None),
        "values": visible,
        "secret_configured": secret_configured,
        "masked_values": masked_values,
        "fields": _field_specs(kind),
    }


def _effective(kind: str, row: DataServiceConfig | None) -> tuple[dict[str, Any], DataServiceConfig | None]:
    stored = _decode_row(row)
    if stored is None:
        return dict(_ENV_DEFAULTS[kind]), None
    values = dict(_ENV_DEFAULTS[kind])
    values.update(stored)
    return values, row


def _close_safely(obj: Any) -> None:
    for name in ("close", "stop"):
        method = getattr(obj, name, None)
        if not callable(method):
            continue
        try:
            method()
            return
        except Exception:
            continue


def _apply_kind(kind: str, values: dict[str, Any]) -> None:
    """Apply one effective profile to the existing in-process clients."""
    enabled = bool(values.get("enabled", True))
    with _APPLY_LOCK:
        if kind == "redis":
            redis_mod.REDIS_URL = str(values.get("url") or "") if enabled else ""
            redis_mod.MW_ENABLED = enabled
            with redis_mod.redis_store._client_lock:
                redis_mod.redis_store._client = None
            redis_mod.redis_store._status_cache = None
            redis_mod.redis_store._status_at = 0.0
            return

        if kind == "rabbitmq":
            mq_mod.task_queue._reset_transport()
            mq_mod.MQ_URL = str(values.get("url") or "") if enabled else ""
            mq_mod.MQ_MANAGEMENT_URL = str(values.get("management_url") or "") if enabled else ""
            mq_mod.MQ_QUEUE = str(values.get("queue") or mq_mod.MQ_QUEUE)
            mq_mod.MQ_DLQ = str(values.get("dlq") or mq_mod.MQ_DLQ)
            mq_mod.MQ_ENABLED = enabled
            mq_mod.task_queue._last_ok = False
            mq_mod.task_queue._last_error = ""
            return

        if kind == "kafka":
            _close_safely(event_bus_mod._producer)
            _close_safely(event_bus_mod._admin)
            event_bus_mod._producer = None
            event_bus_mod._admin = None
            event_bus_mod._CACHE.update(at=0.0, ok=False, latency=None, error="")
            config.KAFKA_BOOTSTRAP = str(values.get("bootstrap") or "") if enabled else ""
            config.KAFKA_TOPIC = str(values.get("topic") or config.KAFKA_TOPIC)
            config.KAFKA_ENABLED = enabled
            return

        if kind == "zookeeper":
            zk_mod.zk_view.close()
            zk_mod.ZOOKEEPER_HOSTS = str(values.get("hosts") or "") if enabled else ""
            zk_mod.zk_view._start_failed = False
            zk_mod.zk_view._status_value = None
            zk_mod.zk_view._status_at = 0.0
            return

        if kind == "nacos":
            _close_safely(nacos_mod.nacos_config._client)
            nacos_mod.NACOS_URL = str(values.get("url") or "").rstrip("/") if enabled else ""
            nacos_mod.NACOS_GROUP = str(values.get("group") or nacos_mod.NACOS_GROUP)
            nacos_mod.NACOS_CONFIG_DATA_ID = str(
                values.get("config_data_id") or nacos_mod.NACOS_CONFIG_DATA_ID
            )
            nacos_mod.NACOS_USERNAME = str(values.get("username") or "")
            nacos_mod.NACOS_PASSWORD = str(values.get("password") or "")
            nacos_mod.NACOS_ENABLED = enabled
            nacos_mod.nacos_config._client = None
            nacos_mod.nacos_config._token = ""
            nacos_mod.nacos_config._token_exp = 0.0
            nacos_mod.nacos_config._status_value = None
            nacos_mod.nacos_config._status_at = 0.0
            return

        if kind == "minio":
            object_store_mod.object_store._client = None
            object_store_mod.object_store._status_value = None
            object_store_mod.object_store._status_at = 0.0
            object_store_mod.MINIO_ENDPOINT = str(values.get("endpoint") or "") if enabled else ""
            object_store_mod.MINIO_ACCESS_KEY = str(values.get("access_key") or "")
            object_store_mod.MINIO_SECRET_KEY = str(values.get("secret_key") or "")
            object_store_mod.MINIO_BUCKET = str(values.get("bucket") or object_store_mod.MINIO_BUCKET)
            object_store_mod.MINIO_SECURE = bool(values.get("secure", False))
            object_store_mod.MINIO_ENABLED = enabled
            return

        if kind == "mongodb":
            doc_store_mod.doc_store.close()
            config.MONGO_URL = str(values.get("url") or "") if enabled else ""
            config.MONGO_DB = str(values.get("db") or config.MONGO_DB)
            config.MONGO_ENABLED = enabled


def apply_persisted_configs() -> int:
    """Load encrypted profiles at startup.  Missing rows keep environment values."""
    applied = 0
    with get_db() as db:
        rows = db.query(DataServiceConfig).all()
        for row in rows:
            if row.kind not in _KINDS:
                continue
            values, _ = _effective(row.kind, row)
            _apply_kind(row.kind, values)
            applied += 1
    return applied


def _test_redis(values: dict[str, Any]) -> tuple[bool, float | None, str]:
    import redis

    client = None
    started = time.perf_counter()
    try:
        client = redis.Redis.from_url(
            values["url"],
            decode_responses=True,
            socket_timeout=float(config.MW_PROBE_TIMEOUT or 4.0),
            socket_connect_timeout=float(config.MW_PROBE_TIMEOUT or 4.0),
        )
        client.ping()
        return True, round((time.perf_counter() - started) * 1000, 1), ""
    except Exception as exc:
        return False, round((time.perf_counter() - started) * 1000, 1), str(exc)
    finally:
        _close_safely(client)


def _test_rabbitmq(values: dict[str, Any]) -> tuple[bool, float | None, str]:
    import pika

    connection = None
    started = time.perf_counter()
    try:
        params = pika.URLParameters(values["url"])
        params.heartbeat = 30
        params.blocked_connection_timeout = float(config.MW_PROBE_TIMEOUT or 4.0)
        connection = pika.BlockingConnection(params)
        connection.channel()
        return True, round((time.perf_counter() - started) * 1000, 1), ""
    except Exception as exc:
        return False, round((time.perf_counter() - started) * 1000, 1), str(exc)
    finally:
        _close_safely(connection)


def _test_kafka(values: dict[str, Any]) -> tuple[bool, float | None, str]:
    from kafka.admin import KafkaAdminClient

    admin = None
    started = time.perf_counter()
    try:
        timeout_ms = max(1000, int(float(config.MW_PROBE_TIMEOUT or 4.0) * 1000))
        admin = KafkaAdminClient(
            bootstrap_servers=[item.strip() for item in values["bootstrap"].split(",") if item.strip()],
            request_timeout_ms=timeout_ms,
        )
        admin.list_topics(timeout=float(config.MW_PROBE_TIMEOUT or 4.0))
        return True, round((time.perf_counter() - started) * 1000, 1), ""
    except Exception as exc:
        return False, round((time.perf_counter() - started) * 1000, 1), str(exc)
    finally:
        _close_safely(admin)


def _test_zookeeper(values: dict[str, Any]) -> tuple[bool, float | None, str]:
    from kazoo.client import KazooClient

    client = None
    started = time.perf_counter()
    timeout = max(1.0, float(config.MW_PROBE_TIMEOUT or 4.0))
    try:
        client = KazooClient(hosts=values["hosts"], timeout=timeout, read_only=True)
        client.start(timeout=timeout)
        if not client.exists("/"):
            raise RuntimeError("根节点不可读")
        return True, round((time.perf_counter() - started) * 1000, 1), ""
    except Exception as exc:
        return False, round((time.perf_counter() - started) * 1000, 1), str(exc)
    finally:
        _close_safely(client)


def _test_nacos(values: dict[str, Any]) -> tuple[bool, float | None, str]:
    started = time.perf_counter()
    timeout = float(config.MW_PROBE_TIMEOUT or 4.0)
    try:
        with httpx.Client(base_url=str(values["url"]).rstrip("/"), timeout=timeout) as client:
            params: dict[str, Any] = {}
            username = str(values.get("username") or "")
            password = str(values.get("password") or "")
            if username:
                login = client.post(
                    "/nacos/v1/auth/login",
                    data={"username": username, "password": password},
                )
                if login.status_code == 200:
                    token = (login.json() or {}).get("accessToken")
                    if token:
                        params["accessToken"] = str(token)
                    else:
                        params.update({"username": username, "password": password})
                else:
                    params.update({"username": username, "password": password})
            response = client.get("/nacos/v1/console/health/readiness", params=params)
            if response.status_code == 200:
                return True, round((time.perf_counter() - started) * 1000, 1), ""
            raise RuntimeError(f"readiness HTTP {response.status_code}")
    except Exception as exc:
        return False, round((time.perf_counter() - started) * 1000, 1), str(exc)


def _test_minio(values: dict[str, Any]) -> tuple[bool, float | None, str]:
    from minio import Minio

    started = time.perf_counter()
    try:
        client = Minio(
            values["endpoint"],
            access_key=str(values.get("access_key") or "") or None,
            secret_key=str(values.get("secret_key") or "") or None,
            secure=bool(values.get("secure", False)),
        )
        bucket = str(values.get("bucket") or "").strip()
        if bucket:
            client.bucket_exists(bucket)
        else:
            client.list_buckets()
        return True, round((time.perf_counter() - started) * 1000, 1), ""
    except Exception as exc:
        return False, round((time.perf_counter() - started) * 1000, 1), str(exc)


def _test_mongodb(values: dict[str, Any]) -> tuple[bool, float | None, str]:
    import pymongo

    client = None
    started = time.perf_counter()
    try:
        timeout_ms = max(1000, int(float(config.MW_PROBE_TIMEOUT or 4.0) * 1000))
        client = pymongo.MongoClient(
            values["url"],
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
            appname="opscenter-config-test",
        )
        client.admin.command("ping")
        return True, round((time.perf_counter() - started) * 1000, 1), ""
    except Exception as exc:
        return False, round((time.perf_counter() - started) * 1000, 1), str(exc)
    finally:
        _close_safely(client)


_TESTERS = {
    "redis": _test_redis,
    "rabbitmq": _test_rabbitmq,
    "kafka": _test_kafka,
    "zookeeper": _test_zookeeper,
    "nacos": _test_nacos,
    "minio": _test_minio,
    "mongodb": _test_mongodb,
}


def _test_connection(kind: str, values: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        available, latency_ms, error = _TESTERS[kind](values)
    except Exception as exc:
        available, latency_ms, error = False, None, str(exc)
    if latency_ms is None:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "kind": kind,
        "available": bool(available),
        "latency_ms": latency_ms,
        "error": "" if available else _mask_error(kind, values, error),
    }


@router.get("/config")
def data_service_configs(_operator: object = Depends(require_operator)) -> dict:
    with get_db() as db:
        rows = {row.kind: row for row in db.query(DataServiceConfig).all()}
        items = []
        for kind in _KINDS:
            values, row = _effective(kind, rows.get(kind))
            items.append(_item(kind, values, row))
    return {"items": items}


@router.get("/{kind}/config")
def data_service_config(kind: str, _operator: object = Depends(require_operator)) -> dict:
    kind = _known_kind(kind)
    with get_db() as db:
        values, row = _effective(kind, _stored_row(db, kind))
        return _item(kind, values, row)


@router.put("/{kind}/config")
def save_data_service_config(
    kind: str,
    payload: dict,
    _operator: object = Depends(require_operator),
) -> dict:
    kind = _known_kind(kind)
    with get_db() as db:
        row = _stored_row(db, kind)
        existing = _decode_row(row)
        values = _merge_payload(kind, payload, existing, defaults=_ENV_DEFAULTS[kind])
        if row is None:
            row = DataServiceConfig(kind=kind)
            db.add(row)
        try:
            row.config_ciphertext = encrypt_secret(json.dumps(values, ensure_ascii=False))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        row.config_updated_at = datetime.utcnow()
        try:
            db.commit()
            db.refresh(row)
        except SQLAlchemyError as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail="数据服务配置保存失败") from exc
        _apply_kind(kind, values)
        return _item(kind, values, row)


@router.post("/{kind}/config/test")
def test_data_service_config(
    kind: str,
    payload: dict | None = None,
    _operator: object = Depends(require_operator),
) -> dict:
    kind = _known_kind(kind)
    with get_db() as db:
        row = _stored_row(db, kind)
        existing = _decode_row(row)
        base = existing if existing is not None else _ENV_DEFAULTS[kind]
        values = _merge_payload(kind, payload or {}, base, defaults=_ENV_DEFAULTS[kind])
    return _test_connection(kind, values)


@router.post("/{kind}/config/reset")
def reset_data_service_config(
    kind: str,
    _operator: object = Depends(require_operator),
) -> dict:
    kind = _known_kind(kind)
    with get_db() as db:
        row = _stored_row(db, kind)
        if row is not None:
            db.delete(row)
            db.commit()
    values = dict(_ENV_DEFAULTS[kind])
    _apply_kind(kind, values)
    return _item(kind, values, None)
