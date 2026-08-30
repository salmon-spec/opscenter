"""Curated, read-only service plaza for user-facing Web applications."""

from __future__ import annotations

import json
import os
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.models import PlazaServicePreference, Server, Service, ServiceSource


router = APIRouter(prefix="/api/v2", tags=["service-plaza"])

_CATALOG_PATH = Path(__file__).with_name("service_catalog.json")
_CACHE_TTL = 30
_cache_lock = threading.Lock()
_cached_at = 0.0
_cached_checks: dict[str, dict] = {}
_refreshing = False


class PlazaVisibilityUpdate(BaseModel):
    hidden: bool


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
        catalog_urls = {item["entry_url"].rstrip("/") for item in catalog}
        catalog = [item for item in catalog if item["key"] not in hidden_catalog_keys]
        for service in manual_services:
            if not service.url.startswith(("http://", "https://")):
                continue
            if service.url.rstrip("/") in catalog_urls:
                continue
            health_url = service.health_path or service.url
            if not health_url.startswith(("http://", "https://")):
                health_url = service.url.rstrip("/") + "/" + health_url.lstrip("/")
            server = next((item for item in servers.values() if item.id == service.server_id), None)
            catalog.append({
                "key": f"manual-{service.id}",
                "name": service.name,
                "description": service.description or "手动添加的服务",
                "server_host": server.host if server else "",
                "entry_url": service.url,
                "health_url": health_url,
                "category": service.category or "未分类",
                "icon": service.icon or "box",
                "auth_mode": "local" if service.account else "none",
                "enabled": True,
                "manual": True,
                "service_id": str(service.id),
            })

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
            "enabled": True,
            "manual": item.get("manual", False),
            "service_id": item.get("service_id"),
            "status": health["status"],
            "http_status": health["http_status"],
            "latency_ms": health["latency_ms"],
            "health_error": health["health_error"],
        })
    return result


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
        hidden_keys = {
            row.catalog_key for row in db.query(PlazaServicePreference).filter(
                PlazaServicePreference.hidden == True,  # noqa: E712
            ).all()
        }
        result = []
        for item in catalog:
            if item["key"] not in hidden_keys:
                continue
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
            result.append({
                "id": str(service.id), "service_id": str(service.id),
                "key": f"manual-{service.id}" if is_manual else None,
                "kind": "manual" if is_manual else "scanned",
                "name": service.name, "description": service.description or "",
                "server_name": server.name if server else "",
                "server_host": server.host if server else service.host_ip or "",
                "url": service.url, "ports": service.ports or "", "image": service.image or "",
                "source": service.source, "manual": is_manual, "deletable": is_manual,
            })
    return result

