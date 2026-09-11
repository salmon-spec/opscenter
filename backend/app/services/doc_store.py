"""MongoDB 文档存储客户端（v5.0.0）。

用途：
- 审计事件（audit_events）、告警事件（alert_events）、任务历史（agent_tasks）、
  K8s/中间件快照（k8s_events）、报告归档（reports）的文档存储；
- 只读浏览：数据库/集合列表。

环境变量：MONGO_URL、MONGO_DB、MONGO_ENABLED（只读浏览不受 MONGO_ENABLED 限制，
写入（insert）必须 MONGO_ENABLED=true）。
集合白名单：audit_events / alert_events / agent_tasks / k8s_events / reports；
白名单外 insert 直接忽略（返回 None），query 也只允许白名单。

实现：pymongo 4.11，惰性 MongoClient(serverSelectionTimeoutMS=3000, connectTimeoutMS=3000)，
模块级单例。所有方法 fail-open。文档统一补 `ts`（UTC epoch 秒，若缺省）。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app import config

logger = logging.getLogger("opscenter.doc_store")

try:  # v5.0.0 依赖已安装；导入失败时全部方法 fail-open
    import pymongo
except Exception:  # pragma: no cover - 依赖缺失兜底
    pymongo = None  # type: ignore[assignment]

# 集合白名单：insert/query/count 只允许这些集合；白名单外一律忽略
COLLECTIONS = {"audit_events", "alert_events", "agent_tasks", "k8s_events", "reports"}

_QUERY_MAX_LIMIT = 500
_AVAIL_TTL = 10.0
_AVAIL: dict[str, Any] = {"at": 0.0, "ok": False, "latency": None, "error": ""}

_client = None


def _configured() -> bool:
    return bool((config.MONGO_URL or "").strip())


def _parse_uri() -> dict:
    if pymongo is None or not _configured():
        return {}
    try:
        from pymongo.uri_parser import parse_uri

        return parse_uri(config.MONGO_URL) or {}
    except Exception:
        return {}


def _endpoint() -> str:
    """只返回 host:port（不含账号/密码/库名）。"""
    parsed = _parse_uri()
    nodes = parsed.get("nodelist") or []
    if nodes:
        try:
            host, port = nodes[0]
            return f"{host}:{port}"
        except Exception:
            pass
    url = (config.MONGO_URL or "").strip()
    match = re.search(r"@([^/?#]+)", url) or re.match(r"mongodb(?:\+srv)?://([^/?#]+)", url)
    host = (match.group(1) if match else "").split(",", 1)[0]
    return host


def _db_name() -> str:
    """URL path 中的库名；未指定时取 MONGO_DB（authSource 不算库名）。"""
    database = _parse_uri().get("database")
    return str(database) if database else str(config.MONGO_DB or "")


def _sanitize(message: Any) -> str:
    """错误脱敏：不回显密码，任何 user:pass@ 片段一律打码。"""
    text = str(message or "")[:300]
    password = _parse_uri().get("password")
    if password:
        text = text.replace(str(password), "***")
    return re.sub(r"://[^/@\s]+@", "://***@", text)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _get_client():
    """惰性单例 MongoClient；未配置/依赖缺失返回 None（不建连接）。"""
    global _client
    if _client is not None:
        return _client
    if pymongo is None or not _configured():
        return None
    _client = pymongo.MongoClient(
        config.MONGO_URL,
        serverSelectionTimeoutMS=3000,
        connectTimeoutMS=3000,
        appname="opscenter",
    )
    return _client


def _probe(force: bool = False) -> tuple[bool, float | None, str]:
    """admin.ping 探测；available() 复用 10s 缓存，status() force=True 实时探测。"""
    now = time.monotonic()
    if not force and _AVAIL["at"] > 0 and (now - _AVAIL["at"]) < _AVAIL_TTL:
        return bool(_AVAIL["ok"]), _AVAIL["latency"], _AVAIL["error"]
    ok, latency, error = False, None, ""
    started = time.monotonic()
    try:
        client = _get_client()
        if client is not None:
            client.admin.command("ping")
            ok = True
    except Exception as exc:
        error = _sanitize(exc)
    latency = round((time.monotonic() - started) * 1000, 1)
    _AVAIL.update(at=time.monotonic(), ok=ok, latency=latency, error=error)
    return ok, latency, error


class DocStore:
    def available(self) -> bool:
        try:
            if not _configured():
                return False
            return _probe()[0]
        except Exception:
            return False

    def status(self) -> dict:
        """{"kind":"mongodb","configured","available","latency_ms","error",
        "detail":{"endpoint":"host:port","db":...}}"""
        result = {
            "kind": "mongodb",
            "configured": _configured(),
            "available": False,
            "latency_ms": None,
            "error": "",
            "detail": {"endpoint": _endpoint(), "db": _db_name()},
        }
        if not result["configured"]:
            return result
        try:
            ok, latency, error = _probe(force=True)
            result.update(available=ok, latency_ms=latency, error=error)
        except Exception as exc:
            result["error"] = _sanitize(exc)
        return result

    def insert(self, collection: str, doc: dict) -> str | None:
        """白名单集合写入；返回 inserted_id 字符串；不可用/非白名单返回 None。"""
        if not config.MONGO_ENABLED or collection not in COLLECTIONS:
            return None
        try:
            client = _get_client()
            if client is None:
                return None
            payload = dict(doc or {})
            if "ts" not in payload:
                payload["ts"] = int(time.time())
            result = client[config.MONGO_DB][collection].insert_one(payload)
            inserted_id = getattr(result, "inserted_id", None)
            return str(inserted_id) if inserted_id is not None else None
        except Exception as exc:
            logger.warning("mongo insert failed: %s", _sanitize(exc))
            return None

    def query(self, collection: str, *, limit: int = 100, sort_desc: bool = True,
              filters: dict | None = None) -> list[dict]:
        """按 ts 倒序查询（limit 上限 500）；_id 转字符串。"""
        if collection not in COLLECTIONS:
            return []
        try:
            client = _get_client()
            if client is None:
                return []
            bounded = max(1, min(_as_int(limit, 100), _QUERY_MAX_LIMIT))
            cursor = client[config.MONGO_DB][collection].find(dict(filters or {}))
            cursor = cursor.sort("ts", -1 if sort_desc else 1).limit(bounded)
            rows = []
            for item in cursor:
                if not isinstance(item, dict):
                    continue
                row = dict(item)
                if "_id" in row:
                    row["_id"] = str(row["_id"])
                rows.append(row)
            return rows
        except Exception as exc:
            logger.warning("mongo query failed: %s", _sanitize(exc))
            return []

    def count(self, collection: str) -> int:
        if collection not in COLLECTIONS:
            return 0
        try:
            client = _get_client()
            if client is None:
                return 0
            return _as_int(client[config.MONGO_DB][collection].estimated_document_count())
        except Exception:
            return 0

    def list_databases(self) -> list[dict]:
        """[{"name","size_on_disk","collections","empty"}]"""
        client = _get_client()
        if client is None:
            return []
        try:
            raw = client.admin.command("listDatabases") or {}
            result = []
            for item in raw.get("databases") or []:
                if not isinstance(item, dict):
                    continue
                size = _as_int(item.get("sizeOnDisk"))
                result.append({
                    "name": str(item.get("name") or ""),
                    "size_on_disk": size,
                    "collections": _as_int(item.get("collections")),
                    "empty": bool(item.get("empty", size == 0)),
                })
            return result
        except Exception:
            # 权限不足等场景降级：仅有库名，size 记 0
            try:
                names = client.list_database_names()
            except Exception:
                return []
            return [{"name": str(name), "size_on_disk": 0, "collections": 0, "empty": False}
                    for name in names]

    def list_collections(self, db: str | None = None, limit: int = 200) -> list[dict]:
        """[{"name","count","size"}]；默认当前 MONGO_DB。"""
        db_name = str(db or config.MONGO_DB or "").strip()
        client = _get_client()
        if client is None or not db_name:
            return []
        try:
            database = client[db_name]
            names = list(database.list_collection_names())
        except Exception:
            return []
        bounded = max(1, _as_int(limit, 200))
        result = []
        for name in sorted(str(n) for n in names)[:bounded]:
            count, size = 0, 0
            try:
                stats = database.command("collstats", name) or {}
                count = _as_int(stats.get("count"))
                size = _as_int(stats.get("size"))
            except Exception:
                pass
            result.append({"name": name, "count": count, "size": size})
        return result

    def close(self) -> None:
        global _client
        client, _client = _client, None
        _AVAIL.update(at=0.0, ok=False, latency=None, error="")
        if client is None:
            return
        try:
            client.close()
        except Exception:
            pass


doc_store = DocStore()
