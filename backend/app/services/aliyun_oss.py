"""Read-only Aliyun OSS adapter for the first OpsCenter extension module.

The module deliberately exposes no mutation operations. Every requested prefix
and object key is normalised and checked against a fixed bucket/root prefix so
path traversal cannot escape the configured read scope.
"""

from __future__ import annotations

import base64
import mimetypes
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from app.config import OSS_AK, OSS_BUCKET, OSS_ENDPOINT, OSS_PREFIX, OSS_SK

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


class OssModuleError(RuntimeError):
    """A safe module error whose message can be returned to the UI."""


class AliyunOssReader:
    def __init__(self) -> None:
        self._bucket = None
        self._bucket_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status_at = 0.0
        self._status_value: dict | None = None

    @property
    def root_prefix(self) -> str:
        value = (OSS_PREFIX or "").strip().replace("\\", "/").strip("/")
        return f"{value}/" if value else ""

    def configured(self) -> bool:
        return bool(OSS_ENDPOINT and OSS_BUCKET and OSS_AK and OSS_SK)

    def _client(self):
        if not self.configured():
            raise OssModuleError("OSS 凭据或存储桶未配置")
        with self._bucket_lock:
            if self._bucket is None:
                try:
                    import oss2
                except ImportError as exc:  # pragma: no cover - deployment guard
                    raise OssModuleError("OSS SDK 未安装") from exc
                auth = oss2.Auth(OSS_AK, OSS_SK)
                self._bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)
            return self._bucket

    def normalize_prefix(self, value: str | None = None) -> str:
        root = self.root_prefix
        raw = (value or root).strip().replace("\\", "/")
        if raw.startswith("oss://"):
            parts = raw.split("/", 3)
            raw = parts[3] if len(parts) == 4 else ""
        raw = raw.lstrip("/")
        if not raw.endswith("/"):
            raw += "/"
        if "\x00" in raw or ".." in raw.split("/"):
            raise OssModuleError("对象前缀不合法")
        if root and not raw.startswith(root):
            raise OssModuleError("对象前缀超出只读范围")
        return raw

    def normalize_key(self, value: str | None) -> str:
        raw = (value or "").strip().replace("\\", "/")
        if raw.startswith("oss://"):
            parts = raw.split("/", 3)
            raw = parts[3] if len(parts) == 4 else ""
        raw = raw.lstrip("/")
        root = self.root_prefix
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
    def _safe_error(exc: Exception) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        if OSS_SK:
            message = message.replace(OSS_SK, "***")
        if OSS_AK:
            message = message.replace(OSS_AK, "***")
        return message[:300]

    def status(self, force: bool = False) -> dict:
        now = time.monotonic()
        if not force:
            with self._status_lock:
                if self._status_value and now - self._status_at < STATUS_CACHE_SECONDS:
                    return dict(self._status_value)

        result = {
            "configured": self.configured(),
            "available": False,
            "bucket": OSS_BUCKET,
            "prefix": self.root_prefix,
            "endpoint": OSS_ENDPOINT,
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
                self._client().list_objects(
                    prefix=self.root_prefix, delimiter="/", max_keys=1,
                )
                result["available"] = True
            except Exception as exc:
                result["error"] = self._safe_error(exc)
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)

        with self._status_lock:
            self._status_at = now
            self._status_value = dict(result)
        return result

    def list_objects(self, prefix: str | None = None, marker: str | None = None,
                     limit: int = 100) -> dict:
        safe_prefix = self.normalize_prefix(prefix)
        safe_limit = max(1, min(int(limit or 100), 500))
        try:
            result = self._client().list_objects(
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
                "last_modified": None,
                "storage_class": None,
                "mime": "",
            })
        for obj in getattr(result, "object_list", []) or []:
            key = self.normalize_key(getattr(obj, "key", ""))
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
            "bucket": OSS_BUCKET,
        }

    def preview_object(self, key: str | None) -> dict:
        safe_key = self.normalize_key(key)
        kind = self.classify(safe_key)
        mime = self._mime(safe_key)
        try:
            head = self._client().head_object(safe_key)
            size = int(getattr(head, "content_length", 0) or 0)
        except Exception as exc:
            raise OssModuleError(self._safe_error(exc)) from exc

        result = {
            "key": safe_key,
            "name": safe_key.rsplit("/", 1)[-1],
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
                body = self._client().get_object(safe_key, byte_range=(0, read_size - 1)).read()
            except Exception as exc:
                raise OssModuleError(self._safe_error(exc)) from exc
            result["content"] = body.decode("utf-8", errors="replace")
            result["truncated"] = size > read_size
            return result

        if kind in {"image", "audio"} and size <= BINARY_PREVIEW_BYTES:
            try:
                body = self._client().get_object(safe_key).read()
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

    def open_download(self, key: str | None):
        safe_key = self.normalize_key(key)
        try:
            response = self._client().get_object(safe_key)
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
