"""Read-only Aliyun OSS adapter for the first OpsCenter extension module.

The module deliberately exposes no mutation operations. Every requested bucket,
prefix, and object key is normalised and checked against a server-side
allow-list so a client cannot browse an arbitrary bucket or escape the
configured read scope.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from app.config import (
    OSS_AK,
    OSS_BUCKET,
    OSS_BUCKET_ENDPOINTS,
    OSS_BUCKET_PREFIXES,
    OSS_BUCKETS,
    OSS_ENDPOINT,
    OSS_PREFIX,
    OSS_SK,
)

TEXT_EXTENSIONS = {".md", ".txt", ".log", ".csv", ".yaml", ".yml", ".xml", ".json"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
AUDIO_MIME_EXTENSIONS = {
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}
PDF_EXTENSIONS = {".pdf"}
TEXT_PREVIEW_BYTES = 256 * 1024
BINARY_PREVIEW_BYTES = 5 * 1024 * 1024
STATUS_CACHE_SECONDS = 10.0
SUMMARY_CACHE_SECONDS = 300.0
SUMMARY_PAGE_SIZE = 1000
SUMMARY_MAX_PAGES = 1000
BUCKET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")


class OssModuleError(RuntimeError):
    """A safe module error whose message can be returned to the UI."""


class AliyunOssReader:
    def __init__(self) -> None:
        self._clients: dict[str, object] = {}
        self._client_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status_at: dict[str, float] = {}
        self._status_values: dict[str, dict] = {}
        self._summary_lock = threading.Lock()
        self._summary_cache: dict[tuple[str, str], tuple[float, dict]] = {}
        self._summary_generation = 0
        self._config_lock = threading.Lock()
        self._runtime_config: dict | None = None

    def _config_snapshot(self) -> dict | None:
        with self._config_lock:
            return dict(self._runtime_config) if self._runtime_config else None

    def configure(self, config: dict | None) -> None:
        """Apply a module-managed OSS configuration and invalidate clients."""
        normalized = dict(config) if isinstance(config, dict) else None
        with self._config_lock:
            if normalized == self._runtime_config:
                return
            self._runtime_config = normalized
            self._clients.clear()
            self._status_at.clear()
            self._status_values.clear()
        with self._summary_lock:
            self._summary_cache.clear()
            self._summary_generation += 1

    def _credentials(self) -> tuple[str, str]:
        config = self._config_snapshot() or {}
        return (
            str(config.get("access_key_id") or OSS_AK).strip(),
            str(config.get("access_key_secret") or OSS_SK).strip(),
        )

    @staticmethod
    def _mask_access_key(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if len(raw) <= 10:
            return f"{raw[:2]}****{raw[-2:]}"
        return f"{raw[:6]}...{raw[-4:]}"

    def config_summary(self) -> dict:
        """Return non-secret credential metadata for the module UI."""
        config = self._config_snapshot()
        access_key, _ = self._credentials()
        return {
            "configured": self.configured(),
            "source": "module" if config else "environment",
            "access_key_masked": self._mask_access_key(access_key),
            "endpoint": str((config or {}).get("endpoint") or OSS_ENDPOINT).rstrip("/"),
            "bucket_count": len(self.allowed_buckets()),
        }

    @staticmethod
    def _clean_prefix(value: str | None) -> str:
        raw = (value or "").strip().replace("\\", "/").strip("/")
        return f"{raw}/" if raw else ""

    def allowed_buckets(self) -> list[str]:
        """Return the configured server-side bucket allow-list."""
        config = self._config_snapshot()
        raw_values = (
            list(config.get("buckets") or [])
            if config else [OSS_BUCKET, *(OSS_BUCKETS or "").split(",")]
        )
        result: list[str] = []
        for raw in raw_values:
            value = str(raw or "").strip()
            if not value or value in result:
                continue
            if not BUCKET_NAME_RE.fullmatch(value):
                continue
            result.append(value)
        return result

    def resolve_bucket(self, value: str | None = None) -> str:
        """Resolve a requested bucket and reject anything outside the allow-list."""
        raw = str(value or "").strip()
        if raw.startswith("oss://"):
            raw = raw[6:].split("/", 1)[0].strip()
        bucket = raw or str(OSS_BUCKET or "").strip()
        if not raw:
            config = self._config_snapshot() or {}
            buckets = self.allowed_buckets()
            bucket = str(config.get("default_bucket") or (buckets[0] if buckets else OSS_BUCKET)).strip()
        if not bucket:
            raise OssModuleError("OSS 存储桶未配置")
        if bucket not in self.allowed_buckets():
            raise OssModuleError("存储桶不在只读白名单内")
        return bucket

    def _endpoint_map(self) -> dict[str, str]:
        config = self._config_snapshot()
        if config:
            value = config.get("bucket_endpoints") or {}
            if not isinstance(value, dict):
                raise OssModuleError("模块 Endpoint 配置不合法")
            return {
                str(bucket).strip(): str(endpoint).strip().rstrip("/")
                for bucket, endpoint in value.items()
                if str(bucket).strip() and str(endpoint).strip()
            }
        raw = (OSS_BUCKET_ENDPOINTS or "").strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise OssModuleError("OSS_BUCKET_ENDPOINTS 配置不是合法 JSON") from exc
        if not isinstance(value, dict):
            raise OssModuleError("OSS_BUCKET_ENDPOINTS 配置必须是对象")
        return {
            str(bucket).strip(): str(endpoint).strip().rstrip("/")
            for bucket, endpoint in value.items()
            if str(bucket).strip() and str(endpoint).strip()
        }

    def _endpoint_for(self, bucket: str | None = None) -> str:
        safe_bucket = self.resolve_bucket(bucket)
        endpoints = self._endpoint_map()
        if endpoints:
            endpoint = endpoints.get(safe_bucket)
            if not endpoint:
                raise OssModuleError(f"存储桶 {safe_bucket} 未配置访问 Endpoint")
            return endpoint
        config = self._config_snapshot() or {}
        configured_endpoint = str(config.get("endpoint") or "").strip().rstrip("/")
        if configured_endpoint:
            return configured_endpoint
        if OSS_ENDPOINT:
            return OSS_ENDPOINT
        raise OssModuleError("OSS Endpoint 未配置")

    def _prefix_map(self) -> dict[str, str]:
        raw = (OSS_BUCKET_PREFIXES or "").strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise OssModuleError("OSS_BUCKET_PREFIXES 配置不是合法 JSON") from exc
        if not isinstance(value, dict):
            raise OssModuleError("OSS_BUCKET_PREFIXES 配置必须是对象")
        return {
            str(bucket).strip(): self._clean_prefix(str(prefix or ""))
            for bucket, prefix in value.items()
            if str(bucket).strip()
        }

    def prefix_for(self, bucket: str | None = None) -> str:
        """Return a bucket's read root, with legacy OSS_PREFIX fallback."""
        safe_bucket = self.resolve_bucket(bucket)
        prefixes = self._prefix_map()
        if safe_bucket in prefixes:
            return prefixes[safe_bucket]
        config = self._config_snapshot()
        if config:
            configured_prefixes = config.get("bucket_prefixes") or {}
            if isinstance(configured_prefixes, dict):
                return self._clean_prefix(str(configured_prefixes.get(safe_bucket) or ""))
            return ""
        if safe_bucket == str(OSS_BUCKET or "").strip():
            return self._clean_prefix(OSS_PREFIX)
        return ""

    @property
    def root_prefix(self) -> str:
        """Backward-compatible alias for the default bucket's read root."""
        return self.prefix_for(OSS_BUCKET)

    def configured(self) -> bool:
        access_key, secret_key = self._credentials()
        if not (access_key and secret_key and self.allowed_buckets()):
            return False
        try:
            self._endpoint_for()
            return True
        except OssModuleError:
            return False

    def _client(self, bucket: str | None = None):
        if not self.configured():
            raise OssModuleError("OSS 凭据或存储桶未配置")
        safe_bucket = self.resolve_bucket(bucket)
        with self._client_lock:
            client = self._clients.get(safe_bucket)
            if client is None:
                try:
                    import oss2
                except ImportError as exc:  # pragma: no cover - deployment guard
                    raise OssModuleError("OSS SDK 未安装") from exc
                access_key, secret_key = self._credentials()
                auth = oss2.Auth(access_key, secret_key)
                client = oss2.Bucket(auth, self._endpoint_for(safe_bucket), safe_bucket)
                self._clients[safe_bucket] = client
            return client

    @staticmethod
    def _path_from_uri(raw: str) -> tuple[str | None, str]:
        if not raw.startswith("oss://"):
            return None, raw
        parts = raw[6:].split("/", 1)
        bucket = parts[0].strip() or None
        path = parts[1] if len(parts) == 2 else ""
        return bucket, path

    def normalize_prefix(self, value: str | None = None, bucket: str | None = None) -> str:
        safe_bucket = self.resolve_bucket(bucket)
        root = self.prefix_for(safe_bucket)
        raw = (value or root).strip().replace("\\", "/")
        uri_bucket, uri_path = self._path_from_uri(raw)
        if uri_bucket and uri_bucket != safe_bucket:
            raise OssModuleError("URL 中的存储桶与当前桶不一致")
        if uri_bucket:
            raw = uri_path
        raw = raw.lstrip("/")
        if raw and not raw.endswith("/"):
            raw += "/"
        if "\x00" in raw or ".." in raw.split("/"):
            raise OssModuleError("对象前缀不合法")
        if root and not raw.startswith(root):
            raise OssModuleError("对象前缀超出只读范围")
        return raw

    def normalize_key(self, value: str | None, bucket: str | None = None) -> str:
        safe_bucket = self.resolve_bucket(bucket)
        raw = (value or "").strip().replace("\\", "/")
        uri_bucket, uri_path = self._path_from_uri(raw)
        if uri_bucket and uri_bucket != safe_bucket:
            raise OssModuleError("URL 中的存储桶与当前桶不一致")
        if uri_bucket:
            raw = uri_path
        raw = raw.lstrip("/")
        root = self.prefix_for(safe_bucket)
        if not raw or "\x00" in raw or ".." in raw.split("/"):
            raise OssModuleError("对象 Key 不合法")
        if root and not raw.startswith(root):
            raise OssModuleError("对象 Key 超出只读范围")
        return raw

    @staticmethod
    def classify(key: str) -> str:
        lowered = key.lower()
        if lowered.endswith(".json"):
            return "json"
        suffix_index = lowered.rfind(".")
        suffix = lowered[suffix_index:] if suffix_index >= 0 else ""
        if suffix in IMAGE_EXTENSIONS:
            return "image"
        if suffix in AUDIO_EXTENSIONS:
            return "audio"
        if suffix in PDF_EXTENSIONS:
            return "pdf"
        if suffix in TEXT_EXTENSIONS:
            return "text"
        return "other"

    @staticmethod
    def _mime(key: str) -> str:
        suffix_index = key.lower().rfind(".")
        suffix = key.lower()[suffix_index:] if suffix_index >= 0 else ""
        if suffix in AUDIO_MIME_EXTENSIONS:
            return AUDIO_MIME_EXTENSIONS[suffix]
        return mimetypes.guess_type(key)[0] or "application/octet-stream"


    @staticmethod
    def _sanitize_error(exc: Exception, *secrets: str) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        for secret in secrets:
            if secret:
                message = message.replace(secret, "***")
        return message[:300]

    def _safe_error(self, exc: Exception) -> str:
        access_key, secret_key = self._credentials()
        return self._sanitize_error(exc, secret_key, access_key)

    @staticmethod
    def _endpoint_for_location(location: str, fallback: str) -> str:
        region = str(location or "").strip()
        if not region:
            return fallback
        suffix = "-internal.aliyuncs.com" if "-internal.aliyuncs.com" in fallback else ".aliyuncs.com"
        return f"https://{region}{suffix}"

    @staticmethod
    def discover_buckets(
        access_key_id: str,
        access_key_secret: str,
        endpoint: str | None = None,
    ) -> dict:
        """Validate credentials and discover bucket endpoints from ListBuckets."""
        access_key = str(access_key_id or "").strip()
        secret_key = str(access_key_secret or "").strip()
        base_endpoint = str(endpoint or OSS_ENDPOINT or "").strip().rstrip("/")
        if not access_key or not secret_key:
            raise OssModuleError("AccessKey ID 和 AccessKey Secret 不能为空")
        if not base_endpoint:
            raise OssModuleError("OSS Endpoint 未配置")
        try:
            import oss2
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise OssModuleError("OSS SDK 未安装") from exc
        try:
            result = oss2.Service(oss2.Auth(access_key, secret_key), base_endpoint).list_buckets()
        except Exception as exc:
            raise OssModuleError(AliyunOssReader._sanitize_error(exc, secret_key, access_key)) from exc

        buckets: list[str] = []
        bucket_endpoints: dict[str, str] = {}
        for item in getattr(result, "buckets", []) or []:
            name = str(getattr(item, "name", "") or "").strip()
            if not BUCKET_NAME_RE.fullmatch(name) or name in bucket_endpoints:
                continue
            location = str(getattr(item, "location", "") or "").strip()
            buckets.append(name)
            bucket_endpoints[name] = AliyunOssReader._endpoint_for_location(location, base_endpoint)
        if not buckets:
            raise OssModuleError("凭据有效，但账号下没有可访问的 OSS Bucket")
        return {
            "buckets": sorted(buckets),
            "bucket_endpoints": bucket_endpoints,
            "endpoint": base_endpoint,
        }
    def status(self, force: bool = False, bucket: str | None = None) -> dict:
        safe_bucket = self.resolve_bucket(bucket) if (bucket or self.allowed_buckets()) else ""
        prefix = self.prefix_for(safe_bucket) if safe_bucket else ""
        now = time.monotonic()
        if not force and safe_bucket:
            with self._status_lock:
                status_at = self._status_at.get(safe_bucket, 0.0)
                status_value = self._status_values.get(safe_bucket)
                if status_value and now - status_at < STATUS_CACHE_SECONDS:
                    return dict(status_value)

        result = {
            "configured": self.configured(),
            "available": False,
            "bucket": safe_bucket,
            "buckets": self.allowed_buckets(),
            "prefix": prefix,
            "endpoint": self._endpoint_for(safe_bucket) if safe_bucket else "",
            "mode": "readonly",
            "latency_ms": None,
            "error": "",
            "checked_at": time.time(),
        }
        if not self.configured():
            result["error"] = "OSS 凭据或存储桶未配置"
        else:
            started = time.perf_counter()
            try:
                self._client(safe_bucket).list_objects(
                    prefix=prefix, delimiter="/", max_keys=1,
                )
                result["available"] = True
            except Exception as exc:
                result["error"] = self._safe_error(exc)
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)

        if safe_bucket:
            with self._status_lock:
                self._status_at[safe_bucket] = now
                self._status_values[safe_bucket] = dict(result)
        return result

    def summary(self, prefix: str | None = None, bucket: str | None = None, force: bool = False) -> dict:
        """Return recursive object totals and first-level directory counts."""
        safe_bucket = self.resolve_bucket(bucket)
        safe_prefix = self.normalize_prefix(prefix, safe_bucket)
        cache_key = (safe_bucket, safe_prefix)
        now = time.monotonic()
        with self._summary_lock:
            cached = self._summary_cache.get(cache_key)
            if not force and cached and now - cached[0] < SUMMARY_CACHE_SECONDS:
                result = dict(cached[1])
                result["cached"] = True
                return result
            generation = self._summary_generation
        total_size = 0
        object_count = 0
        direct_object_count = 0
        folders: dict[str, dict] = {}
        token = ""
        pages = 0
        try:
            client = self._client(safe_bucket)
            while True:
                page = client.list_objects_v2(
                    prefix=safe_prefix,
                    delimiter="",
                    continuation_token=token,
                    max_keys=SUMMARY_PAGE_SIZE,
                )
                for obj in getattr(page, "object_list", []) or []:
                    key = str(getattr(obj, "key", "") or "")
                    if not key.startswith(safe_prefix) or key.endswith("/"):
                        continue
                    size = int(getattr(obj, "size", 0) or 0)
                    relative = key[len(safe_prefix):]
                    object_count += 1
                    total_size += size
                    if "/" not in relative:
                        direct_object_count += 1
                        continue
                    folder_key = f"{safe_prefix}{relative.split('/', 1)[0]}/"
                    folder = folders.setdefault(folder_key, {
                        "key": folder_key,
                        "name": folder_key.rstrip("/").rsplit("/", 1)[-1],
                        "count": 0,
                        "size": 0,
                    })
                    folder["count"] += 1
                    folder["size"] += size
                pages += 1
                if not bool(getattr(page, "is_truncated", False)):
                    break
                token = str(getattr(page, "next_continuation_token", "") or "")
                if not token:
                    raise OssModuleError("OSS 分页统计缺少 continuation token")
                if pages >= SUMMARY_MAX_PAGES:
                    raise OssModuleError("OSS 对象过多，无法在单次统计上限内完成，请缩小目录范围")
        except OssModuleError:
            raise
        except Exception as exc:
            raise OssModuleError(self._safe_error(exc)) from exc

        result = {
            "bucket": safe_bucket,
            "prefix": safe_prefix,
            "total_size": total_size,
            "object_count": object_count,
            "direct_object_count": direct_object_count,
            "folders": folders,
            "pages": pages,
            "cached": False,
            "scanned_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        with self._summary_lock:
            if generation == self._summary_generation:
                self._summary_cache[cache_key] = (now, dict(result))
        return result
    def list_objects(
        self,
        prefix: str | None = None,
        marker: str | None = None,
        limit: int = 100,
        bucket: str | None = None,
    ) -> dict:
        safe_bucket = self.resolve_bucket(bucket)
        safe_prefix = self.normalize_prefix(prefix, safe_bucket)
        safe_limit = max(1, min(int(limit or 100), 500))
        try:
            result = self._client(safe_bucket).list_objects(
                prefix=safe_prefix, delimiter="/", max_keys=safe_limit,
                marker=marker or "",
            )
        except OssModuleError:
            raise
        except Exception as exc:
            raise OssModuleError(self._safe_error(exc)) from exc

        items: list[dict] = []
        for raw_prefix in getattr(result, "prefix_list", []) or []:
            key = str(getattr(raw_prefix, "key", raw_prefix))
            items.append({
                "type": "folder",
                "name": key.rstrip("/").split("/")[-1],
                "key": key,
                "size": None,
                "file_count": None,
                "total_size": None,
                "last_modified": None,
                "storage_class": None,
                "mime": "",
            })
        for obj in getattr(result, "object_list", []) or []:
            key = self.normalize_key(getattr(obj, "key", ""), safe_bucket)
            modified = getattr(obj, "last_modified", None)
            if isinstance(modified, (int, float)):
                timestamp = modified / 1000 if modified > 100_000_000_000 else modified
                modified = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            elif modified is not None and getattr(modified, "tzinfo", None) is None:
                modified = modified.replace(tzinfo=timezone.utc)
            items.append({
                "type": self.classify(key),
                "name": key.rsplit("/", 1)[-1],
                "key": key,
                "size": int(getattr(obj, "size", 0) or 0),
                "last_modified": modified.isoformat() if modified else None,
                "storage_class": getattr(obj, "storage_class", None),
                "mime": self._mime(key),
            })

        return {
            "prefix": safe_prefix,
            "items": items,
            "next_marker": getattr(result, "next_marker", "") or "",
            "truncated": bool(getattr(result, "is_truncated", False)),
            "bucket": safe_bucket,
        }

    def preview_object(self, key: str | None, bucket: str | None = None) -> dict:
        safe_bucket = self.resolve_bucket(bucket)
        safe_key = self.normalize_key(key, safe_bucket)
        kind = self.classify(safe_key)
        mime = self._mime(safe_key)
        try:
            head = self._client(safe_bucket).head_object(safe_key)
            size = int(getattr(head, "content_length", 0) or 0)
        except Exception as exc:
            raise OssModuleError(self._safe_error(exc)) from exc

        result = {
            "key": safe_key,
            "name": safe_key.rsplit("/", 1)[-1],
            "bucket": safe_bucket,
            "type": kind,
            "mime": mime,
            "size": size,
            "previewable": kind in {"text", "json", "image", "audio"},
            "content": None,
            "data_url": None,
            "truncated": False,
            "message": "",
        }
        if kind in {"text", "json"}:
            read_size = min(size, TEXT_PREVIEW_BYTES)
            if read_size <= 0:
                result["content"] = ""
                return result
            try:
                body = self._client(safe_bucket).get_object(
                    safe_key, byte_range=(0, read_size - 1),
                ).read()
            except Exception as exc:
                raise OssModuleError(self._safe_error(exc)) from exc
            result["content"] = body.decode("utf-8", errors="replace")
            result["truncated"] = size > read_size
            return result

        if kind in {"image", "audio"} and size <= BINARY_PREVIEW_BYTES:
            try:
                body = self._client(safe_bucket).get_object(safe_key).read()
            except Exception as exc:
                raise OssModuleError(self._safe_error(exc)) from exc
            result["data_url"] = f"data:{mime};base64,{base64.b64encode(body).decode('ascii')}"
            return result

        if kind == "pdf":
            result["message"] = "PDF 已就绪，可下载后查看完整文档"
        elif kind in {"image", "audio"}:
            result["message"] = "对象超过在线预览大小上限，可下载后查看"
        else:
            result["message"] = "该格式暂不支持在线预览，可下载后查看"
        return result

    def open_download(self, key: str | None, bucket: str | None = None):
        safe_bucket = self.resolve_bucket(bucket)
        safe_key = self.normalize_key(key, safe_bucket)
        try:
            response = self._client(safe_bucket).get_object(safe_key)
        except Exception as exc:
            raise OssModuleError(self._safe_error(exc)) from exc

        def chunks():
            try:
                for chunk in response:
                    if chunk:
                        yield chunk
            finally:
                response.close()

        return chunks(), safe_key.rsplit("/", 1)[-1], self._mime(safe_key)

    @staticmethod
    def content_disposition(filename: str) -> str:
        return f"attachment; filename*=UTF-8''{quote(filename)}"


aliyun_oss = AliyunOssReader()
