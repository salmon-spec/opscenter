"""Curated, read-only service plaza for user-facing Web applications."""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.credential_crypto import decrypt_secret, encrypt_secret
from app.auth import get_current_user
from app.database import get_db
from app.models import (
    PlazaCredentialAccess, PlazaProbeResult, PlazaServicePreference,
    PlazaServiceProfile, Server, Service, ServiceSource,
)
from app.topology import _service_relations


router = APIRouter(prefix="/api/v2", tags=["service-plaza"])

_CATALOG_PATH = Path(__file__).with_name("service_catalog.json")
_CACHE_TTL = 30
_cache_lock = threading.Lock()
_cached_at = 0.0
_cached_checks: dict[str, dict] = {}
_refreshing = False
_probe_times: dict[str, float] = {}
_cycle_lock = threading.Lock()


class PlazaVisibilityUpdate(BaseModel):
    hidden: bool


class PlazaServiceUpdate(BaseModel):
    server_id: str | None = None
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=4000)
    category: str | None = Field(None, min_length=1, max_length=50)
    icon: str | None = Field(None, max_length=50)
    entry_url: str | None = Field(None, max_length=2000)
    health_url: str | None = Field(None, max_length=2000)
    username: str | None = Field(None, max_length=200)
    password: str | None = Field(None, max_length=1000)
    clear_password: bool = False
    login_notes: str | None = Field(None, max_length=4000)
    documentation_url: str | None = Field(None, max_length=2000)
    owner: str | None = Field(None, max_length=100)
    tags: list[str] | None = None
    probe_enabled: bool | None = None
    probe_interval_seconds: int | None = Field(None, ge=30, le=3600)
    probe_timeout_seconds: float | None = Field(None, ge=1, le=30)
    probe_success_statuses: str | None = Field(None, min_length=3, max_length=200)
    probe_verify_tls: bool | None = None

    @field_validator("entry_url", "health_url", "documentation_url")
    @classmethod
    def validate_urls(cls, value: str | None):
        if value in (None, ""):
            return value
        if not value.startswith(("http://", "https://")):
            raise ValueError("地址必须以 http:// 或 https:// 开头")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None):
        if value is None:
            return value
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if len(cleaned) > 20 or any(len(item) > 40 for item in cleaned):
            raise ValueError("标签最多 20 个且每项不超过 40 个字符")
        return cleaned

    @field_validator("probe_success_statuses")
    @classmethod
    def validate_success_statuses(cls, value: str | None):
        if value is None:
            return value
        _parse_success_statuses(value)
        return value.replace(" ", "")


def _parse_success_statuses(value: str) -> set[int]:
    codes: set[int] = set()
    for token in (part.strip() for part in value.split(",")):
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            if not left.isdigit() or not right.isdigit():
                raise ValueError("成功状态码格式应为 200-399,401,403")
            start, end = int(left), int(right)
            if start > end or start < 100 or end > 599 or end - start > 499:
                raise ValueError("成功状态码范围必须在 100-599")
            codes.update(range(start, end + 1))
        elif token.isdigit() and 100 <= int(token) <= 599:
            codes.add(int(token))
        else:
            raise ValueError("成功状态码格式应为 200-399,401,403")
    if not codes:
        raise ValueError("至少配置一个成功状态码")
    return codes


def load_catalog() -> list[dict]:
    """Load and validate the checked-in service catalog."""
    rows = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    required = {
        "key", "name", "server_host", "entry_url", "health_url",
        "category", "auth_mode", "enabled",
    }
    keys: set[str] = set()
    for row in rows:
        entry_url_env = row.get("entry_url_env")
        if entry_url_env and os.getenv(entry_url_env):
            row["entry_url"] = os.environ[entry_url_env]
        missing = required.difference(row)
        if missing:
            raise ValueError(f"service catalog {row.get('key', '<unknown>')} missing {sorted(missing)}")
        if row["key"] in keys:
            raise ValueError(f"duplicate service catalog key: {row['key']}")
        keys.add(row["key"])
        if not row["entry_url"].startswith(("http://", "https://", "/")):
            raise ValueError(f"invalid entry URL for {row['key']}")
        if not row["health_url"].startswith(("http://", "https://")):
            raise ValueError(f"invalid health URL for {row['key']}")
    return rows


def _manual_uuid(plaza_key: str) -> uuid.UUID | None:
    if not plaza_key.startswith("manual-"):
        return None
    try:
        return uuid.UUID(plaza_key.removeprefix("manual-"))
    except ValueError:
        return None


def _apply_profile(item: dict, profile: PlazaServiceProfile | None, servers_by_id: dict) -> dict:
    """Overlay only explicitly saved values on a catalog/manual entry."""
    if not profile:
        item["has_credentials"] = False
        item["credential_username"] = ""
        item["login_notes"] = ""
        item["documentation_url"] = ""
        item["owner"] = ""
        item["tags"] = []
        item["profile_updated_at"] = None
        item.update({
            "probe_enabled": True, "probe_interval_seconds": 60,
            "probe_timeout_seconds": 4.0,
            "probe_success_statuses": "200-399,401,403", "probe_verify_tls": True,
        })
        return item
    mapping = {
        "name": "name", "description": "description", "category": "category",
        "icon": "icon", "entry_url": "entry_url", "health_url": "health_url",
    }
    for target, source in mapping.items():
        value = getattr(profile, source)
        if value is not None:
            item[target] = value
    if profile.server_id and profile.server_id in servers_by_id:
        item["server_host"] = servers_by_id[profile.server_id].host
    item["has_credentials"] = bool(profile.secret_ciphertext)
    item["credential_username"] = profile.username or ""
    item["login_notes"] = profile.login_notes or ""
    item["documentation_url"] = profile.documentation_url or ""
    item["owner"] = profile.owner or ""
    item["tags"] = profile.tags or []
    item["profile_updated_at"] = profile.updated_at.isoformat() if profile.updated_at else None
    item["probe_enabled"] = profile.probe_enabled is not False
    item["probe_interval_seconds"] = profile.probe_interval_seconds or 60
    item["probe_timeout_seconds"] = profile.probe_timeout_seconds or 4.0
    item["probe_success_statuses"] = profile.probe_success_statuses or "200-399,401,403"
    item["probe_verify_tls"] = profile.probe_verify_tls is not False
    if profile.username or profile.secret_ciphertext:
        item["auth_mode"] = "local"
    return item


def _manual_item(service: Service, server: Server | None) -> dict:
    health_url = service.health_path or service.url
    if health_url and not health_url.startswith(("http://", "https://")):
        health_url = service.url.rstrip("/") + "/" + health_url.lstrip("/")
    return {
        "key": f"manual-{service.id}", "name": service.name,
        "description": service.description or "手动添加的服务",
        "server_host": server.host if server else service.host_ip or "",
        "entry_url": service.url, "health_url": health_url or service.url,
        "category": service.category or "未分类", "icon": service.icon or "box",
        "auth_mode": "local" if service.account else "none", "enabled": True,
        "manual": True, "service_id": str(service.id), "source": service.source,
    }


def _load_plaza_items() -> tuple[list[dict], dict[str, Server]]:
    catalog = load_catalog()
    with get_db() as db:
        servers = {server.host: server for server in db.query(Server).all()}
        servers_by_id = {server.id: server for server in servers.values()}
        profiles = {row.plaza_key: row for row in db.query(PlazaServiceProfile).all()}
        hidden_catalog_keys = {
            row.catalog_key for row in db.query(PlazaServicePreference).filter(
                PlazaServicePreference.hidden == True,  # noqa: E712
            ).all()
        }
        manual_services = db.query(Service).filter(
            Service.source == ServiceSource.manual.value,
            Service.hidden != True,  # noqa: E712
            Service.url != None, Service.url != "",  # noqa: E711
        ).all()
        catalog = [_apply_profile(dict(item), profiles.get(item["key"]), servers_by_id) for item in catalog]
        catalog_urls = {item["entry_url"].rstrip("/") for item in catalog}
        catalog = [item for item in catalog if item["key"] not in hidden_catalog_keys]
        for service in manual_services:
            if not service.url.startswith(("http://", "https://")) or service.url.rstrip("/") in catalog_urls:
                continue
            key = f"manual-{service.id}"
            catalog.append(_apply_profile(
                _manual_item(service, servers_by_id.get(service.server_id)), profiles.get(key), servers_by_id,
            ))
    return catalog, servers


def _resolve_item(db, plaza_key: str) -> tuple[dict, Service | None, PlazaServiceProfile | None, dict]:
    servers_by_id = {row.id: row for row in db.query(Server).all()}
    profile = db.query(PlazaServiceProfile).filter(PlazaServiceProfile.plaza_key == plaza_key).first()
    service = None
    manual_id = _manual_uuid(plaza_key)
    if manual_id:
        service = db.query(Service).filter(Service.id == manual_id).first()
        if not service:
            raise HTTPException(404, "服务不存在")
        item = _manual_item(service, servers_by_id.get(service.server_id))
    else:
        base = next((row for row in load_catalog() if row["key"] == plaza_key), None)
        if not base:
            raise HTTPException(404, "服务不存在")
        item = dict(base)
    item = _apply_profile(item, profile, servers_by_id)
    server = next((row for row in servers_by_id.values() if row.host == item.get("server_host")), None)
    if not service and server:
        candidates = db.query(Service).filter(Service.server_id == server.id).all()
        service = next((row for row in candidates if (row.url or "").rstrip("/") == item["entry_url"].rstrip("/")), None)
    return item, service, profile, {"by_id": servers_by_id, "selected": server}


def _probe(item: dict) -> dict:
    req = UrlRequest(item["health_url"], method="GET", headers={"User-Agent": "OpsCenter/4 plaza-health"})
    context = None
    if item.get("allow_insecure_tls") or item.get("probe_verify_tls") is False:
        context = ssl._create_unverified_context()
    started = time.monotonic()
    success_codes = _parse_success_statuses(item.get("probe_success_statuses", "200-399,401,403"))
    try:
        with urlopen(req, timeout=float(item.get("probe_timeout_seconds") or 4), context=context) as response:
            code = response.status
        status = "up" if code in success_codes else "down"
        error = ""
    except HTTPError as exc:
        code = exc.code
        status = "up" if code in success_codes else "down"
        error = "" if status == "up" else f"HTTP {code}"
    except (URLError, TimeoutError, OSError) as exc:
        code = None
        status = "down"
        error = exc.__class__.__name__
    return {
        "status": status,
        "http_status": code,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "health_error": error,
        "checked_at": datetime.utcnow().isoformat(),
    }


def _probe_due(item: dict, force: bool = False) -> bool:
    if not item.get("enabled"):
        return False
    if force:
        return True
    if item.get("probe_enabled") is False:
        return False
    with _cache_lock:
        if item["key"] not in _cached_checks:
            return True
    last = _probe_times.get(item["key"], 0)
    return time.monotonic() - last >= int(item.get("probe_interval_seconds") or 60)


def _persist_probe_results(items_by_key: dict[str, dict], checks: dict[str, dict]) -> None:
    try:
        with get_db() as db:
            for key, check in checks.items():
                item = items_by_key[key]
                db.add(PlazaProbeResult(
                    plaza_key=key, status=check["status"], http_status=check.get("http_status"),
                    latency_ms=check.get("latency_ms"), error=check.get("health_error") or None,
                    probe_url=item.get("health_url"),
                ))
            db.commit()
    except Exception as exc:
        # Health checks must remain available during startup migrations or test fakes.
        print(f"[plaza-health] persist failed: {exc}", flush=True)


def _refresh_health_checks(catalog: list[dict], force: bool = False) -> None:
    global _cached_at, _cached_checks, _refreshing
    with _cycle_lock:
        try:
            enabled = [item for item in catalog if _probe_due(item, force)]
            checks = {}
            if enabled:
                with ThreadPoolExecutor(max_workers=min(12, len(enabled))) as pool:
                    checks = dict(zip((item["key"] for item in enabled), pool.map(_probe, enabled)))
                now = time.monotonic()
                for item in enabled:
                    _probe_times[item["key"]] = now
                _persist_probe_results({item["key"]: item for item in enabled}, checks)
            with _cache_lock:
                _cached_checks.update(checks)
                for item in catalog:
                    if item.get("probe_enabled") is False and not force:
                        _cached_checks[item["key"]] = {
                            "status": "disabled", "http_status": None, "latency_ms": None,
                            "health_error": "", "checked_at": None,
                        }
                _cached_at = time.monotonic()
        finally:
            with _cache_lock:
                _refreshing = False


def _invalidate_health(plaza_key: str) -> None:
    global _cached_at
    with _cache_lock:
        _cached_checks.pop(plaza_key, None)
        _cached_at = 0.0
    _probe_times.pop(plaza_key, None)


def _health_checks(catalog: list[dict]) -> dict[str, dict]:
    """Return last-known health immediately and refresh stale data in background."""
    global _refreshing
    now = time.monotonic()
    with _cache_lock:
        snapshot = dict(_cached_checks)
        for item in catalog:
            if item.get("probe_enabled") is False:
                snapshot[item["key"]] = {
                    "status": "disabled", "http_status": None, "latency_ms": None,
                    "health_error": "", "checked_at": None,
                }
        active = [item for item in catalog if item.get("probe_enabled") is not False]
        stale = not _cached_checks or now - _cached_at >= _CACHE_TTL
        if active and stale and not _refreshing:
            _refreshing = True
            threading.Thread(
                target=_refresh_health_checks, args=([dict(item) for item in active],),
                name="plaza-health-refresh", daemon=True,
            ).start()
    return snapshot


def run_plaza_health_cycle(force: bool = False, plaza_key: str | None = None) -> dict[str, dict]:
    catalog, _servers = _load_plaza_items()
    if plaza_key:
        catalog = [item for item in catalog if item["key"] == plaza_key]
        if not catalog:
            raise HTTPException(404, "服务不存在")
    if not force:
        global _refreshing
        with _cache_lock:
            if _refreshing:
                return {item["key"]: dict(_cached_checks.get(item["key"], {})) for item in catalog}
            _refreshing = True
    _refresh_health_checks(catalog, force=force)
    with _cache_lock:
        return {item["key"]: dict(_cached_checks.get(item["key"], {})) for item in catalog}


async def plaza_health_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await asyncio.to_thread(run_plaza_health_cycle)
        except Exception as exc:
            print(f"[plaza-health] cycle failed: {exc}", flush=True)
        await asyncio.sleep(15)


@router.get("/services/plaza")
def list_plaza_services():
    """Return curated and user-created Web entries without credentials."""
    catalog, servers = _load_plaza_items()

    checks = _health_checks(catalog)

    result = []
    for item in catalog:
        if not item.get("enabled"):
            continue
        server = servers.get(item["server_host"])
        health = checks.get(item["key"], {"status": "unknown", "http_status": None, "latency_ms": None, "health_error": ""})
        result.append({
            "id": f"plaza:{item['key']}",
            "key": item["key"],
            "name": item["name"],
            "description": item.get("description", ""),
            "server_id": str(server.id) if server else None,
            "server_name": server.name if server else item["server_host"],
            "entry_url": item["entry_url"],
            "url": item["entry_url"],
            "health_url": item["health_url"],
            "category": item["category"],
            "icon": item.get("icon", "box"),
            "auth_mode": item["auth_mode"],
            "has_credentials": bool(item.get("has_credentials")),
            "enabled": True,
            "manual": item.get("manual", False),
            "service_id": item.get("service_id"),
            "status": health["status"],
            "http_status": health["http_status"],
            "latency_ms": health["latency_ms"],
            "health_error": health["health_error"],
            "owner": item.get("owner", ""),
            "tags": item.get("tags", []),
            "profile_updated_at": item.get("profile_updated_at"),
        })
    return result


@router.get("/services/plaza/{plaza_key}/detail")
def get_plaza_service_detail(plaza_key: str):
    """Return a complete plaza profile while never returning a saved password."""
    with get_db() as db:
        item, service, profile, server_info = _resolve_item(db, plaza_key)
        server = server_info["selected"]
        health = _health_checks([item]).get(
            item["key"], {"status": "unknown", "http_status": None, "latency_ms": None, "health_error": ""},
        )
        running_seconds = None
        if service and service.started_at:
            running_seconds = max(0, int((time.time() - service.started_at.timestamp())))
        cutoff = datetime.utcnow() - timedelta(hours=24)
        recent_probes = db.query(PlazaProbeResult).filter(
            PlazaProbeResult.plaza_key == plaza_key,
        ).order_by(PlazaProbeResult.checked_at.desc()).limit(10).all()
        daily_probes = db.query(PlazaProbeResult).filter(
            PlazaProbeResult.plaza_key == plaza_key,
            PlazaProbeResult.checked_at >= cutoff,
        ).order_by(PlazaProbeResult.checked_at.desc()).limit(5000).all()
        up_count = sum(row.status == "up" for row in daily_probes)
        latencies = [row.latency_ms for row in daily_probes if row.latency_ms is not None]
        return {
            "id": f"plaza:{plaza_key}", "key": plaza_key,
            "service_id": str(service.id) if service else item.get("service_id"),
            "manual": bool(item.get("manual")), "source": service.source if service else "catalog",
            "name": item["name"], "description": item.get("description", ""),
            "category": item["category"], "icon": item.get("icon", "box"),
            "url": item["entry_url"], "entry_url": item["entry_url"],
            "health_url": item["health_url"], "auth_mode": item.get("auth_mode", "none"),
            "status": health["status"], "http_status": health["http_status"],
            "latency_ms": health["latency_ms"], "health_error": health["health_error"],
            "credential_username": item.get("credential_username", ""),
            "has_credentials": bool(item.get("has_credentials")),
            "login_notes": item.get("login_notes", ""),
            "documentation_url": item.get("documentation_url", ""),
            "owner": item.get("owner", ""), "tags": item.get("tags", []),
            "profile_updated_at": item.get("profile_updated_at"),
            "probe_policy": {
                "enabled": item.get("probe_enabled", True),
                "interval_seconds": item.get("probe_interval_seconds", 60),
                "timeout_seconds": item.get("probe_timeout_seconds", 4.0),
                "success_statuses": item.get("probe_success_statuses", "200-399,401,403"),
                "verify_tls": item.get("probe_verify_tls", True),
            },
            "probe_summary": {
                "checks_24h": len(daily_probes),
                "uptime_percent_24h": round(up_count * 100 / len(daily_probes), 2) if daily_probes else None,
                "avg_latency_ms_24h": round(sum(latencies) / len(latencies), 1) if latencies else None,
                "last_checked_at": recent_probes[0].checked_at.isoformat() if recent_probes else health.get("checked_at"),
            },
            "recent_probes": [
                {
                    "id": str(row.id), "checked_at": row.checked_at.isoformat(), "status": row.status,
                    "http_status": row.http_status, "latency_ms": row.latency_ms,
                    "error": row.error or "", "probe_url": row.probe_url,
                } for row in recent_probes
            ],
            "deploy_type": service.deploy_type if service else None,
            "version": service.version if service else None,
            "started_at": service.started_at.isoformat() if service and service.started_at else None,
            "running_seconds": running_seconds,
            "container_name": service.container_name if service else None,
            "image": service.image if service else None,
            "ports": service.ports if service else None,
            "port": service.port if service else None,
            "host_ip": service.host_ip if service else (server.host if server else item.get("server_host")),
            "host_domain": service.host_domain if service else None,
            "server": {
                "id": str(server.id), "name": server.name, "host": server.host,
                "ssh_port": server.ssh_port, "agent_type": server.agent_type, "status": server.status,
            } if server else None,
            "relations": _service_relations(db, service.id) if service else {"outgoing": [], "incoming": []},
        }


@router.put("/services/plaza/{plaza_key}")
def update_plaza_service(plaza_key: str, payload: PlazaServiceUpdate):
    """Persist editable plaza metadata and encrypt an optional login password."""
    values = payload.model_dump(exclude_unset=True)
    with get_db() as db:
        item, service, profile, server_info = _resolve_item(db, plaza_key)
        if not profile:
            profile = PlazaServiceProfile(plaza_key=plaza_key)
            db.add(profile)
        if "server_id" in values:
            raw_id = values.pop("server_id")
            if raw_id:
                try:
                    server_id = uuid.UUID(raw_id)
                except ValueError:
                    raise HTTPException(400, "所属主机格式不正确")
                if server_id not in server_info["by_id"]:
                    raise HTTPException(404, "所属主机不存在")
                profile.server_id = server_id
                if service:
                    service.server_id = server_id
            else:
                profile.server_id = None
        password = values.pop("password", None)
        clear_password = values.pop("clear_password", False)
        field_map = {
            "name": "name", "description": "description", "category": "category", "icon": "icon",
            "entry_url": "entry_url", "health_url": "health_url", "username": "username",
            "login_notes": "login_notes", "documentation_url": "documentation_url",
            "owner": "owner", "tags": "tags", "probe_enabled": "probe_enabled",
            "probe_interval_seconds": "probe_interval_seconds",
            "probe_timeout_seconds": "probe_timeout_seconds",
            "probe_success_statuses": "probe_success_statuses",
            "probe_verify_tls": "probe_verify_tls",
        }
        for source, target in field_map.items():
            if source in values:
                value = values[source]
                if source in {"entry_url", "health_url", "documentation_url"} and value == "":
                    value = None
                setattr(profile, target, value)
        if clear_password:
            profile.secret_ciphertext = ""
        elif password not in (None, ""):
            profile.secret_ciphertext = encrypt_secret(password)
        if service:
            for field in ("name", "description", "category", "icon"):
                if field in values:
                    setattr(service, field, values[field])
            if "entry_url" in values and values["entry_url"]:
                service.url = values["entry_url"]
            if "health_url" in values:
                service.health_path = values["health_url"] or None
            service.account = ""
            service.password = ""
        has_credentials = bool(profile.secret_ciphertext)
        db.commit()
    _invalidate_health(plaza_key)
    return {"ok": True, "key": plaza_key, "has_credentials": has_credentials}


@router.post("/services/plaza/{plaza_key}/credentials/reveal")
def reveal_plaza_credentials(
    plaza_key: str, request: Request, response: Response,
    current_user=Depends(get_current_user),
):
    """Reveal credentials only after an explicit, audited user action."""
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    with get_db() as db:
        _item, _service, profile, _server_info = _resolve_item(db, plaza_key)
        if not profile or not profile.secret_ciphertext:
            raise HTTPException(404, "该服务尚未保存密码")
        try:
            password = decrypt_secret(profile.secret_ciphertext)
        except ValueError as exc:
            raise HTTPException(500, str(exc))
        db.add(PlazaCredentialAccess(
            plaza_key=plaza_key,
            actor=getattr(current_user, "username", None),
            ip=request.client.host if request.client else None,
        ))
        db.commit()
        return {"username": profile.username or "", "password": password}


@router.get("/services/plaza/{plaza_key}/credential-access-history")
def get_credential_access_history(
    plaza_key: str, limit: int = 50, _current_user=Depends(get_current_user),
):
    """Return credential reveal events without ever including a secret."""
    limit = max(1, min(limit, 200))
    with get_db() as db:
        _resolve_item(db, plaza_key)
        rows = db.query(PlazaCredentialAccess).filter(
            PlazaCredentialAccess.plaza_key == plaza_key,
        ).order_by(PlazaCredentialAccess.created_at.desc()).limit(limit).all()
        return [{
            "id": str(row.id), "action": row.action, "actor": row.actor or "-",
            "ip": row.ip or "-", "created_at": row.created_at.isoformat(),
        } for row in rows]


@router.post("/services/plaza/{plaza_key}/probe")
def probe_plaza_service(plaza_key: str, _current_user=Depends(get_current_user)):
    """Run one health check immediately, even when scheduled probing is disabled."""
    checks = run_plaza_health_cycle(force=True, plaza_key=plaza_key)
    return {"key": plaza_key, **checks[plaza_key]}


@router.get("/services/plaza/{plaza_key}/probe-history")
def get_plaza_probe_history(plaza_key: str, hours: int = 24, limit: int = 200):
    hours = max(1, min(hours, 24 * 90))
    limit = max(1, min(limit, 1000))
    with get_db() as db:
        _resolve_item(db, plaza_key)
        rows = db.query(PlazaProbeResult).filter(
            PlazaProbeResult.plaza_key == plaza_key,
            PlazaProbeResult.checked_at >= datetime.utcnow() - timedelta(hours=hours),
        ).order_by(PlazaProbeResult.checked_at.desc()).limit(limit).all()
        return [{
            "id": str(row.id), "checked_at": row.checked_at.isoformat(), "status": row.status,
            "http_status": row.http_status, "latency_ms": row.latency_ms,
            "error": row.error or "", "probe_url": row.probe_url,
        } for row in reversed(rows)]


@router.put("/services/plaza/{catalog_key}/visibility")
def update_catalog_visibility(catalog_key: str, payload: PlazaVisibilityUpdate):
    """Hide or restore a checked-in plaza entry without modifying its catalog."""
    catalog_keys = {item["key"] for item in load_catalog()}
    if catalog_key not in catalog_keys:
        raise HTTPException(404, "Plaza service not found")
    with get_db() as db:
        row = db.query(PlazaServicePreference).filter(
            PlazaServicePreference.catalog_key == catalog_key,
        ).first()
        if row:
            row.hidden = payload.hidden
        else:
            db.add(PlazaServicePreference(catalog_key=catalog_key, hidden=payload.hidden))
        db.commit()
    return {"ok": True, "key": catalog_key, "hidden": payload.hidden}


@router.get("/services/plaza/hidden")
def list_hidden_plaza_services():
    """Return hidden catalog, manual, and scanned services without credentials."""
    catalog = load_catalog()
    with get_db() as db:
        servers_by_id = {server.id: server for server in db.query(Server).all()}
        profiles = {row.plaza_key: row for row in db.query(PlazaServiceProfile).all()}
        hidden_keys = {
            row.catalog_key for row in db.query(PlazaServicePreference).filter(
                PlazaServicePreference.hidden == True,  # noqa: E712
            ).all()
        }
        result = []
        for item in catalog:
            if item["key"] not in hidden_keys:
                continue
            item = _apply_profile(dict(item), profiles.get(item["key"]), servers_by_id)
            server = next((server for server in servers_by_id.values() if server.host == item["server_host"]), None)
            result.append({
                "id": f"plaza:{item['key']}", "key": item["key"], "kind": "catalog",
                "name": item["name"], "description": item.get("description", ""),
                "server_name": server.name if server else item["server_host"],
                "server_host": item["server_host"], "url": item["entry_url"],
                "source": "catalog", "manual": False, "deletable": False,
            })

        for service in db.query(Service).filter(Service.hidden == True).all():  # noqa: E712
            server = servers_by_id.get(service.server_id)
            is_manual = service.source == ServiceSource.manual.value
            key = f"manual-{service.id}" if is_manual else None
            profile = profiles.get(key) if key else None
            result.append({
                "id": str(service.id), "service_id": str(service.id),
                "key": key,
                "kind": "manual" if is_manual else "scanned",
                "name": profile.name if profile and profile.name is not None else service.name,
                "description": profile.description if profile and profile.description is not None else service.description or "",
                "server_name": server.name if server else "",
                "server_host": server.host if server else service.host_ip or "",
                "url": profile.entry_url if profile and profile.entry_url else service.url,
                "ports": service.ports or "", "image": service.image or "",
                "source": service.source, "manual": is_manual, "deletable": is_manual,
            })
    return result

