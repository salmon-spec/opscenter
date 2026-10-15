from __future__ import annotations

import json
import os
import re
import ssl
import time
from datetime import datetime, timezone
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (HTTPRedirectHandler, HTTPSHandler, ProxyHandler,
                            Request, build_opener)

from fastapi.staticfiles import StaticFiles
from pathlib import Path
from runtime.app import create_module_app
from storage import PveStore


try:
    STALE_AFTER_DAYS = max(1, min(int(os.getenv("PVE_BACKUP_STALE_AFTER_DAYS", "7")), 365))
except ValueError:
    STALE_AFTER_DAYS = 7
_cache_lock = Lock()
_cache_until = 0.0
_cached_report = None
_last_good_report = None
_store = PveStore()


class PveConfigurationError(Exception):
    pass


class PveApiError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class PveClient:
    def __init__(self, base_url: str, token_id: str, token_secret: str, ca_file: str = "", timeout: float = 4):
        parsed = urlsplit(base_url.strip())
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise PveConfigurationError("PVE API 地址必须是 HTTPS origin")
        if not token_id.strip() or not token_secret.strip():
            raise PveConfigurationError("PVE 只读 API 凭据未配置")
        if any(char in token_id or char in token_secret for char in "\r\n"):
            raise PveConfigurationError("PVE 只读 API 凭据格式无效")
        try:
            port = parsed.port or 8006
        except ValueError as error:
            raise PveConfigurationError("PVE API 地址无效") from error

        hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        self.origin = f"https://{hostname}:{port}"
        self.base_path = "/api2/json"
        self.token = f"PVEAPIToken={token_id.strip()}={token_secret.strip()}"
        self.timeout = max(1, min(float(timeout), 15))
        try:
            if ca_file and os.path.isfile(ca_file) and os.path.getsize(ca_file):
                context = ssl.create_default_context(cafile=ca_file)
            else:
                context = ssl.create_default_context()
        except (OSError, ssl.SSLError) as error:
            raise PveConfigurationError("PVE CA 配置无效") from error
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())

    @classmethod
    def from_env(cls):
        url = os.getenv("PVE_API_URL", "").strip()
        token_id = os.getenv("PVE_API_TOKEN_ID", "").strip()
        secret_path = os.getenv("PVE_API_TOKEN_SECRET_FILE", "").strip()
        secret = os.getenv("PVE_API_TOKEN_SECRET", "").strip()
        if secret_path:
            try:
                with open(secret_path, encoding="utf-8") as secret_file:
                    secret = secret_file.read().strip()
            except OSError:
                secret = ""
        if not url and not token_id and not secret:
            return None
        return cls(url, token_id, secret, os.getenv("PVE_API_CA_FILE", "").strip(),
                   float(os.getenv("PVE_API_TIMEOUT_SECONDS", "4")))

    def get(self, path: str, params: dict | None = None):
        if not path.startswith("/") or ".." in path.split("/"):
            raise PveApiError("PVE API path invalid")
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.origin}{self.base_path}{path}{query}",
            headers={"Authorization": self.token, "Accept": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise PveApiError("PVE API response too large")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or "data" not in payload:
                raise PveApiError("PVE API response invalid")
            return payload["data"]
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            raise PveApiError("PVE API request failed") from error


def _list(value, label: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise PveApiError(f"PVE {label} response invalid")
    return value


def _vmid(backup: dict):
    value = backup.get("vmid")
    if value is not None:
        return str(value)
    match = re.search(r"vzdump-(?:qemu|lxc)-(\d+)-", str(backup.get("volid", "")))
    return match.group(1) if match else None


def _timestamp(value):
    try:
        return datetime.fromtimestamp(float(value), timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _is_true(value) -> bool:
    return value is True or value == 1 or str(value).lower() in {"1", "true", "yes"}


def collect_report(client: PveClient, now: datetime | None = None,
                   external_backups: list[dict] | None = None,
                   external_collected_at: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    guests_raw = _list(client.get("/cluster/resources", {"type": "vm"}), "VM")
    nodes_raw = _list(client.get("/cluster/resources", {"type": "node"}), "node")
    guests = []
    for item in guests_raw:
        if item.get("type") not in {"qemu", "lxc", "vm"} or item.get("vmid") is None:
            continue
        guests.append({
            "vmid": str(item["vmid"]), "name": str(item.get("name") or item["vmid"]),
            "type": str(item.get("type") or "vm"), "node": str(item.get("node") or ""),
            "status": str(item.get("status") or "unknown"),
        })

    nodes = sorted({str(item.get("node") or item.get("id", "").removeprefix("node/"))
                    for item in nodes_raw if item.get("node") or item.get("id")})
    storages = []
    backups = []
    tasks = []
    errors = []
    shared_storages = set()
    for node in nodes:
        node_path = quote(node, safe="")
        try:
            node_storages = _list(client.get(f"/nodes/{node_path}/storage"), "storage")
        except PveApiError:
            errors.append("部分存储数据不可用")
            continue
        for item in node_storages:
            content = item.get("content", "")
            content_types = content if isinstance(content, list) else str(content).split(",")
            storage_id = str(item.get("storage") or item.get("storageid") or "")
            shared = _is_true(item.get("shared"))
            if shared and storage_id in shared_storages:
                continue
            if shared:
                shared_storages.add(storage_id)
            capacity = {}
            if storage_id and _is_true(item.get("active", True)):
                try:
                    capacity = client.get(
                        f"/nodes/{node_path}/storage/{quote(storage_id, safe='')}/status"
                    )
                    if not isinstance(capacity, dict):
                        raise PveApiError("PVE storage status response invalid")
                except PveApiError:
                    errors.append("部分存储容量不可用")
            total_bytes = capacity.get("total")
            used_bytes = capacity.get("used")
            if total_bytes is None or used_bytes is None:
                errors.append("部分存储容量不可用")
            storage = {
                "node": node, "storage": storage_id, "type": item.get("type"),
                "status": item.get("status"), "content": content_types, "shared": shared,
                "total_bytes": int(total_bytes) if total_bytes is not None else None,
                "used_bytes": int(used_bytes) if used_bytes is not None else None,
                "free_bytes": int(capacity["avail"]) if capacity.get("avail") is not None else None,
            }
            storages.append(storage)
            if "backup" not in content_types or not storage_id:
                continue
            try:
                rows = _list(client.get(
                    f"/nodes/{node_path}/storage/{quote(storage_id, safe='')}/content", {"content": "backup"}
                ), "backup content")
            except PveApiError:
                errors.append("部分备份文件清单不可用")
                continue
            for row in rows:
                vmid = _vmid(row)
                created = _timestamp(row.get("ctime"))
                if not vmid or not created:
                    continue
                backups.append({
                    "vmid": vmid, "node": node, "storage": storage_id,
                    "volid": str(row.get("volid") or ""), "size_bytes": int(row.get("size") or 0),
                    "created_at": created, "format": str(row.get("format") or ""), "source": "pve",
                })
        try:
            task_rows = _list(client.get(f"/nodes/{node_path}/tasks", {"typefilter": "vzdump", "limit": 100}), "tasks")
            for task in task_rows:
                tasks.append({
                    "upid": str(task.get("upid") or ""), "node": node,
                    "status": str(task.get("status") or ("running" if task.get("endtime") is None else "unknown")),
                    "starttime": _timestamp(task.get("starttime")),
                    "endtime": _timestamp(task.get("endtime")),
                    "type": str(task.get("type") or "vzdump"),
                })
        except PveApiError:
            errors.append("部分备份任务历史不可用")

    try:
        jobs_raw = _list(client.get("/cluster/backup"), "backup jobs")
        jobs = [{key: item[key] for key in ("id", "enabled", "schedule", "storage", "vmid", "mode") if key in item}
                for item in jobs_raw]
    except PveApiError:
        jobs = []
        errors.append("备份计划不可用")

    if external_backups is not None:
        backups.extend(external_backups)
    if external_collected_at is None:
        errors.append("OSS 备份清单尚未导入，覆盖情况可能不完整")
    elif (now - external_collected_at).total_seconds() > 86400:
        errors.append("OSS 备份清单超过 24 小时未更新")
    latest = {}
    for item in backups:
        if item["vmid"] not in latest or item["created_at"] > latest[item["vmid"]]["created_at"]:
            latest[item["vmid"]] = item
    coverage = []
    for guest in guests:
        backup = latest.get(guest["vmid"])
        age_days = max(0, int((now - backup["created_at"]).total_seconds() // 86400)) if backup else None
        state = ("unknown" if external_collected_at is None else "missing") if not backup else (
            "current" if age_days <= STALE_AFTER_DAYS else "stale")
        coverage.append({
            **guest, "coverage": state, "latest_backup_at": backup["created_at"] if backup else None,
            "backup_age_days": age_days, "backup_source": backup["source"] if backup else None,
            "recovery_test": "unverified",
        })
    storages.sort(key=lambda item: (item["node"], item["storage"]))
    backups.sort(key=lambda item: item["created_at"], reverse=True)
    tasks.sort(key=lambda item: item["starttime"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    capacity_complete = all(item["total_bytes"] is not None and item["used_bytes"] is not None
                            for item in storages)
    total_bytes = sum(item["total_bytes"] or 0 for item in storages) if capacity_complete else None
    used_bytes = sum(item["used_bytes"] or 0 for item in storages) if capacity_complete else None
    current = sum(item["coverage"] == "current" for item in coverage)
    missing = sum(item["coverage"] == "missing" for item in coverage)
    stale = sum(item["coverage"] == "stale" for item in coverage)
    unknown = sum(item["coverage"] == "unknown" for item in coverage)
    data = {
        "vm_total": len(guests), "vm_running": sum(item["status"] == "running" for item in guests),
        "storage_total_bytes": total_bytes, "storage_used_bytes": used_bytes,
        "storage_used_percent": round(used_bytes * 100 / total_bytes, 1)
        if capacity_complete and total_bytes else None,
        "backup_coverage_current": current, "backup_coverage_missing": missing,
        "backup_coverage_stale": stale, "backup_coverage_unknown": unknown,
        "backup_files_total": len(backups),
        "backup_oss_total": sum(item["source"] == "oss" for item in backups),
        "oss_manifest_collected_at": external_collected_at,
        "restore_unverified": len(guests), "guests": coverage, "storages": storages,
        "backups": backups[:500], "jobs": jobs, "tasks": tasks[:100],
    }
    status = "degraded" if errors or missing or stale or unknown else "ok"
    return {"status": status, "collected_at": now, "data": data,
            "error": "；".join(dict.fromkeys(errors)) or None}


def get_report() -> dict:
    global _cache_until, _cached_report, _last_good_report
    with _cache_lock:
        now = time.monotonic()
        if _cached_report is not None and now < _cache_until:
            return _cached_report
        if _last_good_report is None and _store.configured():
            try:
                _last_good_report = _store.read()
            except Exception:
                pass
        try:
            client = PveClient.from_env()
            if client is None:
                report = {"status": "unavailable", "collected_at": None, "data": {},
                          "error": "PVE 只读 API 尚未配置"}
            else:
                if _store.configured():
                    objects, manifest_at = _store.oss_manifest()
                else:
                    objects, manifest_at = [], None
                report = collect_report(client, external_backups=objects,
                                        external_collected_at=manifest_at)
                if _store.configured():
                    evidence = _store.evidence()
                    guests = report["data"]["guests"]
                    for guest in guests:
                        item = evidence.get(guest["vmid"])
                        if item:
                            guest["recovery_test"] = item["level"]
                            guest["recovery_evidence_at"] = item["verified_at"]
                    report["data"]["restore_unverified"] = sum(
                        guest["recovery_test"] == "unverified" for guest in guests)
                    _store.save(report)
        except (PveConfigurationError, PveApiError, OSError, ValueError, ssl.SSLError):
            if _last_good_report:
                report = {**_last_good_report, "status": "degraded", "error": "PVE 数据暂不可刷新"}
            else:
                report = {"status": "unavailable", "collected_at": None, "data": {},
                          "error": "PVE 只读数据源暂不可用"}
        _cached_report = report
        _cache_until = now + (5 if report["status"] == "unavailable" else 30)
        if report["status"] != "unavailable":
            _last_good_report = report
        return report


def summary_provider():
    report = get_report()
    return {**report, "stale_after_seconds": 300}


def findings_provider():
    report = get_report()
    now = report.get("collected_at") or datetime.now(timezone.utc)
    data = report.get("data", {})
    items = []
    for guest in data.get("guests", []):
        if guest["coverage"] != "current":
            missing = guest["coverage"] == "missing"
            items.append({
                "id": f"pve-backup-coverage:{guest['vmid']}", "resource_id": guest["vmid"],
                "severity": "critical" if missing else "warning",
                "title": f"{guest['name']} 备份覆盖不足",
                "summary": "未发现备份文件" if missing else (
                    "OSS 清单不可用，无法确认覆盖" if guest["coverage"] == "unknown"
                    else f"最新备份距今 {guest['backup_age_days']} 天"),
                "observed_at": guest["latest_backup_at"] or now, "url": "/pve-backup",
            })
    if data.get("restore_unverified"):
        items.append({
            "id": "pve-restore-verification:unverified", "severity": "warning",
            "title": "恢复演练证据未验证",
            "summary": "当前只读数据源未提供恢复演练结果；不能据备份文件存在推断可恢复。",
            "observed_at": now, "url": "/pve-backup",
        })
    for task in data.get("tasks", []):
        if task["status"].lower() not in {"ok", "running"}:
            items.append({
                "id": f"pve-backup-task:{task['upid']}", "severity": "critical",
                "title": "PVE 备份任务失败", "summary": f"节点 {task['node']} · {task['status']}",
                "observed_at": task["starttime"] or now, "url": "/pve-backup",
            })
    return {"status": report["status"], "collected_at": now, "items": items[:1000], "error": report.get("error")}


app = create_module_app(
    key="pve-backup", version="0.1.8", summary_provider=summary_provider,
    findings_provider=findings_provider,
    capabilities=["summary:read", "findings:read", "vm:read", "storage:read", "backup:read"],
    readiness_provider=lambda: _store.ping() if _store.configured() else None,
)
app.mount("/ui", StaticFiles(directory=Path(__file__).with_name("ui"), html=True), name="ui")


@app.on_event("startup")
def initialize_storage():
    if _store.configured():
        _store.migrate()


@app.get("/api/v1/overview")
def overview():
    return get_report()


@app.get("/api/v1/guests")
def guests():
    return get_report().get("data", {}).get("guests", [])


@app.get("/api/v1/storages")
def storages():
    return get_report().get("data", {}).get("storages", [])


@app.get("/api/v1/backups")
def backups():
    return get_report().get("data", {}).get("backups", [])
