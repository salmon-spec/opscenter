"""MinIO 对象存储客户端（v5.0.0）。

用途：
- 报告/日志/备份清单等导出物归档：`put()` / `presigned_get()`；
- 只读浏览：buckets / objects（数据服务页面）。

环境变量：MINIO_ENDPOINT（host:port，不带 scheme）、MINIO_ACCESS_KEY、MINIO_SECRET_KEY、
MINIO_BUCKET、MINIO_SECURE、MINIO_ENABLED（put 需要 true；只读浏览不受开关限制）。

实现：minio 7.2.15 SDK，惰性创建，模块级单例。全部 fail-open。
"""

from __future__ import annotations

import io
import logging
import threading
import time
from datetime import timedelta

from app.config import (
    MINIO_ACCESS_KEY,
    MINIO_BUCKET,
    MINIO_ENABLED,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
    MW_PROBE_TIMEOUT,
)

logger = logging.getLogger("opscenter.minio")

_STATUS_CACHE_TTL = 10.0


class ObjectStore:
    def __init__(self) -> None:
        self._client = None
        self._client_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status_at = 0.0
        self._status_value: dict | None = None

    def _configured(self) -> bool:
        return bool(MINIO_ENDPOINT)

    def _backend(self):
        if not self._configured():
            return None
        with self._client_lock:
            if self._client is None:
                from minio import Minio
                self._client = Minio(
                    MINIO_ENDPOINT,
                    access_key=MINIO_ACCESS_KEY or None,
                    secret_key=MINIO_SECRET_KEY or None,
                    secure=bool(MINIO_SECURE),
                )
            return self._client

    def available(self) -> bool:
        return bool(self.status().get("available"))

    def status(self) -> dict:
        now = time.monotonic()
        with self._status_lock:
            if self._status_value and now - self._status_at < _STATUS_CACHE_TTL:
                return dict(self._status_value)
        result = {
            "kind": "minio",
            "configured": self._configured(),
            "available": False,
            "latency_ms": None,
            "error": "",
            "detail": {"endpoint": MINIO_ENDPOINT, "bucket": MINIO_BUCKET, "secure": bool(MINIO_SECURE)},
        }
        client = self._backend()
        if client is None:
            with self._status_lock:
                self._status_at = now
                self._status_value = dict(result)
            return dict(result)
        started = time.perf_counter()
        try:
            # bucket_exists 成功即服务可达（桶不存在返回 False 不抛）
            client.bucket_exists(MINIO_BUCKET)
            result["available"] = True
        except Exception as exc:
            result["error"] = str(exc)[:300]
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        with self._status_lock:
            self._status_at = now
            self._status_value = dict(result)
        return dict(result)

    def ensure_bucket(self, bucket: str | None = None) -> bool:
        client = self._backend()
        if client is None:
            return False
        name = bucket or MINIO_BUCKET
        try:
            if not client.bucket_exists(name):
                client.make_bucket(name)
            return True
        except Exception:
            return False

    def put(self, object_name: str, data: bytes | str,
            content_type: str = "application/octet-stream") -> dict | None:
        if not MINIO_ENABLED or not self._configured():
            return None
        client = self._backend()
        if client is None:
            return None
        try:
            if isinstance(data, str):
                data = data.encode("utf-8")
            if not self.ensure_bucket():
                return None
            client.put_object(
                MINIO_BUCKET, object_name, io.BytesIO(data), length=len(data),
                content_type=content_type,
            )
            return {"bucket": MINIO_BUCKET, "object": object_name, "size": len(data)}
        except Exception as exc:
            logger.warning("minio put failed: %s", exc)
            return None

    def get(self, object_name: str) -> bytes | None:
        client = self._backend()
        if client is None:
            return None
        try:
            resp = client.get_object(MINIO_BUCKET, object_name)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()
        except Exception:
            return None

    def list_objects(self, *, prefix: str = "", bucket: str | None = None,
                     limit: int = 100) -> list[dict]:
        client = self._backend()
        if client is None:
            return []
        try:
            out = []
            for obj in client.list_objects(bucket or MINIO_BUCKET, prefix=prefix, recursive=True):
                out.append({
                    "name": obj.object_name,
                    "size": obj.size,
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                })
                if len(out) >= max(1, int(limit)):
                    break
            return out
        except Exception:
            return []

    def list_buckets(self) -> list[dict]:
        client = self._backend()
        if client is None:
            return []
        try:
            return [{
                "name": b.name,
                "creation_date": b.creation_date.isoformat() if b.creation_date else None,
            } for b in client.list_buckets()]
        except Exception:
            return []

    def presigned_get(self, object_name: str, expires_seconds: int = 3600,
                      bucket: str | None = None) -> str | None:
        client = self._backend()
        if client is None:
            return None
        try:
            return client.presigned_get_object(
                bucket or MINIO_BUCKET, object_name,
                expires=timedelta(seconds=max(60, int(expires_seconds))),
            )
        except Exception:
            return None


object_store = ObjectStore()
