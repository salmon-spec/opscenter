"""Curated, read-only service plaza for user-facing Web applications."""

from __future__ import annotations

import json
import os
import ssl
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from app.credential_crypto import decrypt_secret, encrypt_secret
from app.database import get_db
from app.models import PlazaServicePreference, PlazaServiceProfile, Server, Service, ServiceSource
from app.topology import _service_relations


router = APIRouter(prefix="/api/v2", tags=["service-plaza"])

_CATALOG_PATH = Path(__file__).with_name("service_catalog.json")
_CACHE_TTL = 30
_cache_lock = threading.Lock()
_cached_at = 0.0
_cached_checks: dict[str, dict] = {}
_refreshing = False


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
    req = Request(item["health_url"], method="GET", headers={"User-Agent": "OpsCenter/4 plaza-health"})
    context = None
    if item.get("allow_insecure_tls"):
        context = ssl._create_unverified_context()
    started = time.monotonic()
    try:
        with urlopen(req, timeout=4, context=context) as response:
            code = response.status
        status = "up" if 200 <= code < 400 else "down"
        error = ""
    except HTTPError as exc:
        code = exc.code
        status = "up" if code in (401, 403) else "down"
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
    }


def _refresh_health_checks(catalog: list[dict]) -> None:
    global _cached_at, _cached_checks, _refreshing
    try:
        enabled = [item for item in catalog if item.get("enabled")]
        with ThreadPoolExecutor(max_workers=min(12, len(enabled) or 1)) as pool:
            checks = dict(zip((item["key"] for item in enabled), pool.map(_probe, enabled)))
        with _cache_lock:
            _cached_checks = checks
            _cached_at = time.monotonic()
    finally:
        with _cache_lock:
            _refreshing = False


def _health_checks(catalog: list[dict]) -> dict[str, dict]:
    """Return last-known health immediately and refresh stale data in background."""
    global _refreshing
    now = time.monotonic()
    with _cache_lock:
        snapshot = dict(_cached_checks)
        stale = not _cached_checks or now - _cached_at >= _CACHE_TTL
        if stale and not _refreshing:
            _refreshing = True
            threading.Thread(
                target=_refresh_health_checks, args=([dict(item) for item in catalog],),
                name="plaza-health-refresh", daemon=True,
            ).start()
    return snapshot


@router.get("/services/plaza")
def list_plaza_services():
    """Return curated and user-created Web entries without credentials."""
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
            Service.url != None,  # noqa: E711
            Service.url != "",
        ).all()
        catalog = [_apply_profile(dict(item), profiles.get(item["key"]), servers_by_id) for item in catalog]
        catalog_urls = {item["entry_url"].rstrip("/") for item in catalog}
        catalog = [item for item in catalog if item["key"] not in hidden_catalog_keys]
        for service in manual_services:
            if not service.url.startswith(("http://", "https://")):
                continue
            if service.url.rstrip("/") in catalog_urls:
                continue
            key = f"manual-{service.id}"
            item = _manual_item(service, servers_by_id.get(service.server_id))
            catalog.append(_apply_profile(item, profiles.get(key), servers_by_id))

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
            "owner": "owner", "tags": "tags",
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
    return {"ok": True, "key": plaza_key, "has_credentials": has_credentials}


@router.post("/services/plaza/{plaza_key}/credentials/reveal")
def reveal_plaza_credentials(plaza_key: str, response: Response):
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
        return {"username": profile.username or "", "password": password}


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

