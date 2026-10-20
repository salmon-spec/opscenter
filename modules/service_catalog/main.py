from __future__ import annotations

import json
import os
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from fastapi import Depends, HTTPException, Request as FastAPIRequest, Response as FastAPIResponse
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from runtime.auth import ModuleAuth
from runtime.app import create_module_app
from storage import CatalogStore

def get(path: str, query: dict | None = None):
    url = os.getenv("SERVICE_CATALOG_SOURCE_URL", "http://backend:9091/api/v2").rstrip("/") + path
    if query:
        url += "?" + urlencode(query)
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=8) as response:
            return json.load(response)
    except (HTTPError, TimeoutError, URLError, ValueError) as error:
        raise HTTPException(502, "服务目录数据源暂不可用") from error


def forward_legacy(method: str, path: str, authorization: str, query: dict | None = None,
                   payload: dict | None = None, timeout: float = 8):
    url = os.getenv("SERVICE_CATALOG_SOURCE_URL", "http://backend:9091/api/v2").rstrip("/") + path
    if query:
        url += "?" + urlencode(query)
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {"Accept": "application/json", "Authorization": authorization}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            result = json.loads(raw) if raw else None
    except HTTPError as error:
        if error.code in {401, 403, 404, 409, 422}:
            messages = {401: "请重新登录运维工作台", 403: "服务操作未获授权", 404: "服务或记录不存在",
                        409: "操作冲突，请刷新后重试", 422: "服务操作参数无效"}
            raise HTTPException(error.code, messages[error.code]) from None
        raise HTTPException(502, "服务目录操作暂不可用") from None
    except (TimeoutError, URLError, ValueError) as error:
        raise HTTPException(502, "服务目录操作暂不可用") from error
    return result


def forward_request(method: str, path: str, request: FastAPIRequest, query: dict | None = None,
                    payload: dict | None = None, timeout: float = 8):
    authorization = request.headers.get("authorization", "")
    if not authorization:
        raise HTTPException(401, "请先登录运维工作台")
    return forward_legacy(method, path, authorization, query, payload, timeout)


def services_snapshot(include_hidden: bool = False):
    store = CatalogStore()
    cached = store.read_snapshot("services")
    if not cached:
        raise HTTPException(503, "服务目录尚未初始化")
    services, collected_at = cached
    manual = getattr(store, "list_manual_services", lambda: [])()
    if not isinstance(services, list):
        return services, collected_at
    preferences = store.preferences()
    profile_overrides = store.profile_overrides()
    credential_counts = store.credential_counts()
    rows = services + manual
    for item in rows:
        item.update({"credential_count": 0, "has_credentials": False})
        item.update(credential_counts.get(item.get("key"), {}))
        if "status" in item:
            item.update(service_freshness(item))
        override = profile_overrides.get(item.get("key"), {})
        if override:
            item.update(override)
            if "entry_url" in override:
                item["url"] = override["entry_url"]
    rows.sort(key=lambda item: (preferences.get(item.get("key"), {}).get("sort_order") is None,
                                preferences.get(item.get("key"), {}).get("sort_order") or 0))
    rows = [item for item in rows if not item.get("deleted")]
    visible = rows if include_hidden else [item for item in rows if not preferences.get(item.get("key"), {}).get("hidden")]
    return visible, collected_at


def service_freshness(service: dict):
    checked_at = service.get("status_checked_at") or service.get("last_checked_at")
    try:
        if isinstance(checked_at, (int, float)):
            checked = datetime.fromtimestamp(checked_at, timezone.utc)
        else:
            checked = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
        age = max(0, (datetime.now(timezone.utc) - checked).total_seconds())
    except (TypeError, ValueError, OverflowError, OSError):
        age = None
    threshold = service.get("staleness_seconds") or 180
    disabled = service.get("probe_enabled") is False or service.get("status") == "disabled"
    return {
        "status_age_seconds": round(age, 1) if age is not None else None,
        "stale": False if disabled else age is None or age > float(threshold),
    }


def probe_service(service: dict):
    url = service.get("health_url") or service.get("url") or service.get("entry_url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise HTTPException(422, "服务健康检查地址无效")
    success_codes = set()
    for part in str(service.get("probe_success_statuses") or "200-399,401,403").replace(" ", "").split(","):
        bounds = [int(code) for code in part.split("-")]
        success_codes.update(range(bounds[0], bounds[-1] + 1))
    started = time.monotonic()
    code = None
    error = ""
    try:
        with urlopen(Request(url, headers={"User-Agent": "OpsCenter/service-catalog"}),
                     timeout=float(service.get("probe_timeout_seconds") or 4),
                     context=ssl._create_unverified_context() if service.get("probe_verify_tls") is False else None) as response:
            code = response.status
    except HTTPError as response:
        code = response.code
    except (TimeoutError, URLError, OSError, ValueError):
        error = "网络连接失败或探测超时"
    status = "up" if code in success_codes else "down"
    if code is not None and status == "down":
        error = f"HTTP {code} 状态不符合成功策略"
    return {
        "status": status, "http_status": code,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "error": error, "probe_url": url, "checked_at": datetime.now(timezone.utc),
    }


def run_probe_cycle():
    services, _ = services_snapshot(include_hidden=True)
    store = CatalogStore()
    local = store.probe_summaries(24)
    now = datetime.now(timezone.utc)
    due = []
    for service in services:
        if service.get("probe_enabled") is False or service.get("status") == "disabled":
            continue
        interval = max(30, min(3600, int(service.get("probe_interval_seconds") or 60)))
        checked = local.get(service.get("key"), {}).get("checked_at")
        if checked is not None:
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            age = (now - checked).total_seconds()
        else:
            age = service_freshness(service)["status_age_seconds"]
        if age is None or age >= interval:
            due.append(service)
    if not due:
        return 0
    with ThreadPoolExecutor(max_workers=min(4, len(due))) as pool:
        results = list(pool.map(probe_service, due))
    for service, result in zip(due, results):
        store.save_probe_result(service["key"], result)
    return len(results)


def require_service_key(service_key: str):
    if service_key not in {item.get("key") for item in services_snapshot(include_hidden=True)[0]}:
        raise HTTPException(404, "服务不存在")


def module_health_overview(hours: int):
    now = datetime.now(timezone.utc)
    services, collected_at = services_snapshot()
    store = CatalogStore()
    local_health = store.probe_summaries(hours)
    cached_health = store.read_snapshot(f"health-overview:{hours}")
    cached_payload, cached_at = cached_health if cached_health else ({}, None)
    cached_items = {item.get("key"): item for item in cached_payload.get("items", []) if isinstance(item, dict)}
    counts = {status: 0 for status in ("up", "down", "degraded", "unknown", "disabled")}
    items = []
    availability = []
    timestamps = []
    for service in services if isinstance(services, list) else []:
        probe = local_health.get(service.get("key"), {})
        if probe:
            checked_at = probe["checked_at"]
            first_checked_at = probe["first_checked_at"]
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            if first_checked_at.tzinfo is None:
                first_checked_at = first_checked_at.replace(tzinfo=timezone.utc)
            window_complete = (now - first_checked_at).total_seconds() >= hours * 3600
            service = {
                **service,
                "status": probe["status"],
                "status_checked_at": checked_at.isoformat(),
                "status_age_seconds": round(max(0, (datetime.now(timezone.utc) - checked_at).total_seconds()), 1),
                "stale": (datetime.now(timezone.utc) - checked_at).total_seconds() > float(service.get("staleness_seconds") or 180),
                "checks": probe["checks"],
                "uptime_percent": float(probe["uptime_percent"]) if window_complete else service.get("uptime_percent"),
                "avg_latency_ms": float(probe["avg_latency_ms"]) if probe["avg_latency_ms"] is not None else None,
            }
        status = service.get("status", "unknown")
        if service.get("probe_enabled") is False or status == "disabled":
            status = "disabled"
        elif status not in counts:
            status = "unknown"
        counts[status] += 1
        uptime = service.get("uptime_percent")
        if uptime is None:
            uptime = cached_items.get(service.get("key"), {}).get("uptime_percent")
        if uptime is not None:
            availability.append(float(uptime))
            if service.get("uptime_percent") is None:
                service["uptime_percent"] = float(uptime)
        checked_at = service.get("status_checked_at") or service.get("last_checked_at")
        if checked_at:
            timestamps.append(checked_at)
        items.append({**service, "status": status})
    generated_at = now.isoformat()
    cached_stale = bool(cached_at and (datetime.now(timezone.utc) - cached_at).total_seconds() > 300)
    return {
        "generated_at": generated_at,
        "data_timestamp": cached_payload.get("data_timestamp") or (max(timestamps) if timestamps else collected_at.isoformat()),
        "stale": (datetime.now(timezone.utc) - collected_at).total_seconds() > 300
        or cached_stale or any(item.get("stale") for item in items),
        "range_hours": hours,
        "summary": {
            "total": len(items), **counts, "active_incidents": 0, "silenced": 0,
            "checked": len(availability),
            "average_uptime_percent": round(sum(availability) / len(availability), 2) if availability else None,
        },
        "items": items,
    }


def _snapshot_status(collected_at: datetime):
    age = max(0, int((datetime.now(timezone.utc) - collected_at).total_seconds()))
    stale = age > 300
    error = "服务目录快照超过 5 分钟未刷新" if stale else None
    return age, "degraded" if error else "ok", error


def summary_provider():
    services, collected_at = services_snapshot()
    age, status, error = _snapshot_status(collected_at)
    if not isinstance(services, list):
        return {"status": "degraded", "collected_at": collected_at, "data": {}, "error": "服务目录快照格式异常"}
    return {
        "status": status,
        "collected_at": collected_at,
        "stale_after_seconds": 300,
        "data": {"services_total": len(services), "cache_age_seconds": age},
        "error": error,
    }


def findings_provider():
    services, collected_at = services_snapshot()
    age, status, error = _snapshot_status(collected_at)
    items = []
    for service in services if isinstance(services, list) else []:
        health = service.get("status")
        if health in {"up", "online", "disabled", "unknown"} and not service.get("stale"):
            continue
        observed_at = service.get("status_checked_at") or service.get("last_checked_at") or collected_at
        try:
            observed_at = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
        except ValueError:
            observed_at = collected_at
        items.append({
            "id": f"service:{service.get('key', service.get('id', 'unknown'))}:health",
            "resource_id": str(service.get("service_id") or service.get("id") or "") or None,
            "severity": "critical" if health in {"down", "offline"} else "warning",
            "title": f"{service.get('name') or '服务'}探活异常",
            "summary": f"状态：{health or 'unknown'}" + (" · 数据过期" if service.get("stale") else ""),
            "observed_at": observed_at,
            "url": "/services",
        })
        if len(items) == 1000:
            break
    return {"status": status, "collected_at": collected_at, "items": items, "error": error}


def readiness_provider():
    store = CatalogStore()
    if hasattr(store, "migrate"):
        store.migrate()
    store.ping()
    if not store.read_snapshot("services"):
        raise RuntimeError("service catalog snapshot is not initialized")


app = create_module_app(
    key="service-catalog",
    version="0.7.4",
    summary_provider=summary_provider,
    findings_provider=findings_provider,
    readiness_provider=readiness_provider,
    capabilities=["summary:read", "findings:read", "services:read", "services:create", "services:update", "services:delete", "services:archive", "services:order", "services:visibility", "credentials:manage", "services:probe", "incidents:manage"],
)
auth = ModuleAuth(CatalogStore)
auth.register(app)
_probe_worker = None


class ManualServiceCreate(BaseModel):
    server_id: UUID
    server_name: str = Field(min_length=1, max_length=100)
    server_host: str = Field(default="", max_length=253)
    name: str = Field(min_length=1, max_length=100)
    url: AnyHttpUrl
    category: str = Field(default="应用服务", min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    health_path: str = Field(default="", max_length=200)
    pinned: bool = False

    @field_validator("health_path")
    @classmethod
    def valid_health_path(cls, value):
        if value and (not value.startswith("/") or "\r" in value or "\n" in value):
            raise ValueError("健康检查路径必须以 / 开头")
        return value


class ServiceOrder(BaseModel):
    ordered_keys: list[str] = Field(max_length=5000)


class ServiceVisibility(BaseModel):
    hidden: bool


class CredentialCreate(BaseModel):
    label: str = Field(default="账号", min_length=1, max_length=100)
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=4000)
    is_default: bool = False

    @field_validator("label")
    @classmethod
    def nonempty_label(cls, value):
        if not value.strip():
            raise ValueError("凭证名称不能为空")
        return value.strip()


class CredentialUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=100)
    username: str | None = Field(None, max_length=200)
    password: str | None = Field(None, max_length=1000)
    notes: str | None = Field(None, max_length=4000)
    is_default: bool | None = None
    clear_password: bool = False

    @field_validator("label")
    @classmethod
    def nonempty_optional_label(cls, value):
        if value is not None and not value.strip():
            raise ValueError("凭证名称不能为空")
        return value.strip() if value is not None else value


class ServiceProfileUpdate(BaseModel):
    server_id: UUID | None = None
    server_name: str | None = Field(None, max_length=100)
    server_host: str | None = Field(None, max_length=253)
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=4000)
    category: str | None = Field(None, min_length=1, max_length=50)
    icon: str | None = Field(None, max_length=50)
    entry_url: str | None = Field(None, max_length=2000)
    health_url: str | None = Field(None, max_length=2000)
    owner: str | None = Field(None, max_length=100)
    documentation_url: str | None = Field(None, max_length=2000)
    tags: list[str] | None = None
    probe_enabled: bool | None = None
    probe_interval_seconds: int | None = Field(None, ge=30, le=3600)
    probe_timeout_seconds: float | None = Field(None, ge=1, le=30)
    probe_success_statuses: str | None = Field(None, min_length=3, max_length=200)
    probe_verify_tls: bool | None = None
    probe_failure_threshold: int | None = Field(None, ge=1, le=10)
    probe_recovery_threshold: int | None = Field(None, ge=1, le=5)
    probe_notifications_enabled: bool | None = None

    @field_validator("entry_url", "health_url", "documentation_url")
    @classmethod
    def valid_profile_urls(cls, value):
        if value not in (None, "") and not value.startswith(("http://", "https://")):
            raise ValueError("地址必须以 http:// 或 https:// 开头")
        return value

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value):
        if value is None:
            return value
        cleaned = list(dict.fromkeys(tag.strip() for tag in value if tag.strip()))
        if len(cleaned) > 20 or any(len(tag) > 40 for tag in cleaned):
            raise ValueError("标签最多 20 个且每项不超过 40 个字符")
        return cleaned

    @field_validator("probe_success_statuses")
    @classmethod
    def valid_success_statuses(cls, value):
        if value is None:
            return value
        parts = value.replace(" ", "").split(",")
        try:
            for part in parts:
                bounds = [int(code) for code in part.split("-")]
                if len(bounds) not in {1, 2} or any(code < 100 or code > 599 for code in bounds):
                    raise ValueError
                if len(bounds) == 2 and bounds[0] > bounds[1]:
                    raise ValueError
        except ValueError:
            raise ValueError("成功状态码格式无效") from None
        return ",".join(parts)


@app.get("/api/v1/services")
def list_services():
    return services_snapshot()[0]


@app.delete("/api/v1/services/plaza/{plaza_key}")
def delete_catalog_service(plaza_key: str, user=Depends(app.state.require_editor)):
    if plaza_key not in {item.get("key") for item in services_snapshot(include_hidden=True)[0]}:
        raise HTTPException(404, "服务不存在")
    # Keep a tombstone outside the imported snapshot so a refresh cannot recreate the entry.
    CatalogStore().update_profile(plaza_key, {"deleted": True, "probe_enabled": False}, user["username"])
    return {"ok": True}


@app.get("/api/v1/services/plaza/{plaza_key}/detail")
def service_detail(plaza_key: str, user=Depends(app.state.current_user)):
    detail = next((item for item in services_snapshot(include_hidden=True)[0]
                   if item.get("key") == plaza_key), None)
    if detail is None:
        raise HTTPException(404, "服务不存在")
    detail = dict(detail)
    detail["server"] = {
        "id": detail.get("server_id"),
        "name": detail.get("server_name", ""),
        "host": detail.get("server_host", ""),
    } if detail.get("server_name") or detail.get("server_host") else None
    detail["probe_policy"] = {
        "enabled": detail.get("probe_enabled", True),
        "interval_seconds": detail.get("probe_interval_seconds", 60),
        "timeout_seconds": detail.get("probe_timeout_seconds", 4),
        "success_statuses": detail.get("probe_success_statuses", "200-399,401,403"),
        "verify_tls": detail.get("probe_verify_tls", True),
        "failure_threshold": detail.get("probe_failure_threshold", 3),
        "recovery_threshold": detail.get("probe_recovery_threshold", 1),
        "notifications_enabled": detail.get("probe_notifications_enabled", True),
    }
    cached = CatalogStore().read_snapshot("health-overview:24")
    health_rows = cached[0].get("items", []) if cached and isinstance(cached[0], dict) else []
    health = next((item for item in health_rows if item.get("key") == plaza_key), {})
    detail["probe_summary"] = {
        "checks_24h": health.get("checks", 0),
        "uptime_percent_24h": health.get("uptime_percent"),
        "avg_latency_ms_24h": health.get("avg_latency_ms"),
    }
    store = CatalogStore()
    probes = store.probe_history(plaza_key, hours=24, limit=5000)
    if probes:
        first_checked_at = datetime.fromisoformat(probes[0]["checked_at"].replace("Z", "+00:00"))
        if first_checked_at.tzinfo is None:
            first_checked_at = first_checked_at.replace(tzinfo=timezone.utc)
        window_complete = (datetime.now(timezone.utc) - first_checked_at).total_seconds() >= 24 * 3600
        detail["status"] = probes[-1]["status"]
        detail["status_checked_at"] = probes[-1]["checked_at"]
        detail["status_age_seconds"] = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(
            probes[-1]["checked_at"].replace("Z", "+00:00")).astimezone(timezone.utc)).total_seconds())
        detail["stale"] = False
        detail["recent_probes"] = probes[-20:]
        detail["probe_summary"] = {
            "checks_24h": len(probes),
            "uptime_percent_24h": round(sum(row["status"] == "up" for row in probes) * 100 / len(probes), 2) if window_complete else None,
            "avg_latency_ms_24h": round(sum(row["latency_ms"] or 0 for row in probes) / len(probes), 1),
        }
    detail["credentials"] = store.list_credentials(plaza_key)
    detail["credential_count"] = len(detail["credentials"])
    detail["has_credentials"] = any(item["has_password"] for item in detail["credentials"])
    detail["credential_access_history"] = store.credential_history(plaza_key, 5)
    return detail


@app.get("/api/v1/legacy/services/{service_id}/detail")
def legacy_manual_service_detail(service_id: UUID, request: FastAPIRequest, user=Depends(app.state.current_user)):
    return forward_request("GET", f"/services/{service_id}/detail", request)


@app.put("/api/v1/services/plaza/{plaza_key}")
def update_service_profile(plaza_key: str, payload: ServiceProfileUpdate,
                           user=Depends(app.state.require_editor)):
    if plaza_key not in {item.get("key") for item in services_snapshot(include_hidden=True)[0]}:
        raise HTTPException(404, "服务不存在")
    CatalogStore().update_profile(
        plaza_key, payload.model_dump(exclude_unset=True, mode="json"), user["username"],
    )
    return {"ok": True, "key": plaza_key}


@app.put("/api/v1/legacy/services/{service_id}")
def update_legacy_manual_service(service_id: UUID, payload: dict, request: FastAPIRequest,
                                 user=Depends(app.state.require_editor)):
    return forward_request("PUT", f"/services/{service_id}", request, payload=payload)


@app.delete("/api/v1/legacy/services/{service_id}")
def delete_legacy_manual_service(service_id: UUID, request: FastAPIRequest,
                                 user=Depends(app.state.require_editor)):
    return forward_request("DELETE", f"/services/{service_id}", request)


@app.put("/api/v1/legacy/catalog/{service_key}/visibility")
def restore_legacy_catalog_service(service_key: str, payload: dict, request: FastAPIRequest,
                                   user=Depends(app.state.require_editor)):
    return forward_request("PUT", f"/services/plaza/{quote(service_key, safe='')}/visibility", request, payload=payload)


@app.get("/api/v1/services/plaza/{plaza_key}/credential-access-history")
def credential_access_history(plaza_key: str, request: FastAPIRequest, limit: int = 5,
                              user=Depends(app.state.current_user)):
    if not 1 <= limit <= 200:
        raise HTTPException(400, "查询数量范围无效")
    require_service_key(plaza_key)
    return CatalogStore().credential_history(plaza_key, limit)


@app.get("/api/v1/services/plaza/{plaza_key}/credentials")
def list_credentials(plaza_key: str, user=Depends(app.state.current_user)):
    require_service_key(plaza_key)
    return CatalogStore().list_credentials(plaza_key)


@app.post("/api/v1/services/plaza/{plaza_key}/credentials", status_code=201)
def create_credential(plaza_key: str, payload: CredentialCreate,
                             user=Depends(app.state.require_editor)):
    require_service_key(plaza_key)
    try:
        return CatalogStore().create_credential(plaza_key, payload.model_dump(), user["username"])
    except ValueError as error:
        raise HTTPException(409, str(error)) from None


@app.put("/api/v1/services/plaza/{plaza_key}/credentials/{credential_id}")
def update_credential(plaza_key: str, credential_id: UUID, payload: CredentialUpdate,
                      user=Depends(app.state.require_editor)):
    require_service_key(plaza_key)
    try:
        result = CatalogStore().update_credential(plaza_key, str(credential_id), payload.model_dump(exclude_unset=True), user["username"])
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    if not result:
        raise HTTPException(404, "凭证不存在")
    return result


@app.delete("/api/v1/services/plaza/{plaza_key}/credentials/{credential_id}")
def delete_credential(plaza_key: str, credential_id: UUID, user=Depends(app.state.require_editor)):
    require_service_key(plaza_key)
    if not CatalogStore().delete_credential(plaza_key, str(credential_id), user["username"]):
        raise HTTPException(404, "凭证不存在")
    return {"ok": True}


@app.post("/api/v1/services/plaza/{plaza_key}/credentials/{credential_id}/reveal")
def reveal_credential(plaza_key: str, credential_id: UUID, request: FastAPIRequest,
                      response: FastAPIResponse, user=Depends(app.state.require_editor)):
    require_service_key(plaza_key)
    response.headers.update({"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})
    result = CatalogStore().reveal_credential(plaza_key, str(credential_id), user["username"], request.client.host if request.client else None)
    if not result:
        raise HTTPException(404, "该凭证尚未保存密码")
    return result


@app.post("/api/v1/services/plaza/{plaza_key}/probe")
def probe_catalog_service(plaza_key: str, user=Depends(app.state.require_editor)):
    service = next((item for item in services_snapshot(include_hidden=True)[0]
                    if item.get("key") == plaza_key), None)
    if service is None:
        raise HTTPException(404, "服务不存在")
    result = probe_service(service)
    CatalogStore().save_probe_result(plaza_key, result)
    return {"key": plaza_key, **result, "checked_at": result["checked_at"].isoformat()}


@app.post("/api/v1/services/plaza/incidents/{incident_id}/acknowledge")
def acknowledge_legacy_incident(incident_id: UUID, request: FastAPIRequest,
                                user=Depends(app.state.require_editor)):
    return forward_request("POST", f"/services/plaza/incidents/{incident_id}/acknowledge", request)


@app.post("/api/v1/services/plaza/silences")
def create_legacy_silence(payload: dict, request: FastAPIRequest,
                          user=Depends(app.state.require_editor)):
    return forward_request("POST", "/services/plaza/silences", request, payload=payload)


@app.delete("/api/v1/services/plaza/silences/{silence_id}")
def delete_legacy_silence(silence_id: UUID, request: FastAPIRequest,
                          user=Depends(app.state.require_editor)):
    return forward_request("DELETE", f"/services/plaza/silences/{silence_id}", request)


@app.get("/api/v1/health-overview")
def health_overview(hours: int = 24):
    if not 1 <= hours <= 720:
        raise HTTPException(400, "统计时长范围无效")
    return module_health_overview(hours)


@app.get("/api/v1/hidden")
def hidden_services():
    rows, _ = services_snapshot(include_hidden=True)
    preferences = CatalogStore().preferences()
    return [item for item in rows if preferences.get(item.get("key"), {}).get("hidden")]


@app.on_event("startup")
def initialize_module_storage():
    global _probe_worker
    if os.path.isfile(os.getenv("SERVICE_CATALOG_DATABASE_URL_FILE", "/run/secrets/service_catalog_db_url")):
        CatalogStore().migrate()
        auth.ensure_schema()
        if _probe_worker is None or not _probe_worker.is_alive():
            _probe_worker = threading.Thread(target=probe_loop, daemon=True)
            _probe_worker.start()


def refresh_source_snapshot():
    data = get("/services/plaza")
    if not isinstance(data, list):
        raise HTTPException(502, "服务目录数据格式无效")
    CatalogStore().save_snapshot("services", data, datetime.now(timezone.utc))


def probe_loop():
    last_refresh = 0.0
    while True:
        try:
            if time.monotonic() - last_refresh >= 60:
                refresh_source_snapshot()
                last_refresh = time.monotonic()
            run_probe_cycle()
        except Exception:
            print("service catalog probe cycle failed", flush=True)
        time.sleep(5)


@app.post("/api/v1/services", status_code=201)
def create_service(payload: ManualServiceCreate, user=Depends(app.state.require_editor)):
    import uuid

    service = payload.model_dump()
    service["server_id"] = str(service["server_id"])
    service["url"] = str(service["url"])
    service["id"] = str(uuid.uuid4())
    CatalogStore().create_manual_service(service, user["username"])
    return {"id": service["id"], "name": service["name"]}


@app.put("/api/v1/services/order")
def order_services(payload: ServiceOrder, user=Depends(app.state.require_editor)):
    keys = payload.ordered_keys
    known = {item["key"] for item in services_snapshot()[0]}
    if len(keys) != len(set(keys)) or set(keys) != known:
        raise HTTPException(400, "服务顺序必须包含全部可见服务")
    CatalogStore().set_order(keys, user["username"])
    return {"ok": True}


@app.put("/api/v1/services/{service_key}/visibility")
def visibility(service_key: str, payload: ServiceVisibility, user=Depends(app.state.require_editor)):
    source, _ = services_snapshot(include_hidden=True)
    known = {item["key"] for item in source} if isinstance(source, list) else set()
    if service_key not in known:
        raise HTTPException(404, "服务不存在")
    CatalogStore().set_visibility(service_key, payload.hidden, user["username"])
    return {"ok": True}


@app.put("/api/v1/services/manual/{service_id}")
def update_manual_service(service_id: UUID, payload: ManualServiceCreate, user=Depends(app.state.require_editor)):
    values = payload.model_dump()
    values["server_id"] = str(values["server_id"])
    values["url"] = str(values["url"])
    if not CatalogStore().update_manual_service(str(service_id), values, user["username"]):
        raise HTTPException(404, "手动服务不存在")
    return {"ok": True}


@app.post("/api/v1/services/manual/{service_id}/archive")
def archive_manual_service(service_id: UUID, user=Depends(app.state.require_editor)):
    if not CatalogStore().archive_manual_service(str(service_id), user["username"]):
        raise HTTPException(404, "手动服务不存在")
    return {"ok": True}
