"""OpsCenter v4.0 - Main API server."""
import os, uuid, asyncio, re, json, hashlib, socket, subprocess
from datetime import datetime
from typing import Optional, List, Dict
from contextlib import contextmanager

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool

from app.models import Base, Server, Service, ServerStatus, ServiceStatus, ServiceSource, AuthType
from app.discovery import (
    discover_docker_services, parse_nginx_config, run_full_discovery,
    discover_listening_ports, discover_prometheus_targets, discover_systemd_services,
)
from app.ssh_manager import get_ssh_client, ssh_exec, discover_remote_docker_services, collect_remote_metrics, get_remote_containers, test_ssh_connection

# === Config ===
DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://opscenter:OpsCenter2026@127.0.0.1:5433/opscenter")
LOCAL_HOST = os.getenv("LOCAL_HOST", "39.99.139.131")
GROUPS_JSON_PATH = "/opt/cicd/nginx/html/ops/groups.json"
MANUAL_SERVICES_JSON_PATH = "/opt/cicd/nginx/html/ops/manual-services.json"

# === Database ===
engine = create_engine(DB_URL, poolclass=QueuePool, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(bind=engine)

@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# === Category Metadata ===
CATEGORY_META = {
    "代码与CI/CD": {"icon": "fa-code", "color": "#8b5cf6", "order": 1},
    "应用服务": {"icon": "fa-cube", "color": "#3b82f6", "order": 2},
    "监控与日志": {"icon": "fa-chart-area", "color": "#22c55e", "order": 3},
    "网络与代理": {"icon": "fa-network-wired", "color": "#f59e0b", "order": 4},
    "自动化工作流": {"icon": "fa-robot", "color": "#ec4899", "order": 5},
    "数据存储": {"icon": "fa-database", "color": "#06b6d4", "order": 6},
    "消息与注册": {"icon": "fa-sitemap", "color": "#a78bfa", "order": 7},
    "运维管理": {"icon": "fa-gauge-high", "color": "#f97316", "order": 8},
    "前端应用": {"icon": "fa-desktop", "color": "#60a5fa", "order": 9},
    "安全": {"icon": "fa-shield-halved", "color": "#ef4444", "order": 10},
    "未分类": {"icon": "fa-folder", "color": "#94a3b8", "order": 99},
}

# === Schemas ===

# -- Server --
class ServerCreate(BaseModel):
    name: str
    host: str
    ssh_port: int = 22
    ssh_user: str = "root"
    auth_type: str = "password"  # password | key
    ssh_password: Optional[str] = None
    ssh_key: Optional[str] = None
    tags: List[str] = []
    remark: Optional[str] = None

class ServerUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    auth_type: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_key: Optional[str] = None
    tags: Optional[List[str]] = None
    enabled: Optional[bool] = None
    remark: Optional[str] = None

# -- Service (no more manual create) --
class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    health_path: Optional[str] = None
    pinned: Optional[bool] = None

# -- Groups --
class GroupCreate(BaseModel):
    name: str
    color: str = "#38bdf8"
    icon: str = "box"

class GroupUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    order: Optional[int] = None

class ServiceGroupMove(BaseModel):
    serviceKey: str
    groupId: str

# === App ===
app = FastAPI(title="OpsCenter API", version="4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Helpers ===

def _hash_credential(val: str) -> str:
    """One-way hash for credential storage reference."""
    return "sha256:" + hashlib.sha256(val.encode()).hexdigest()[:32]

def _read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default or {}

def _write_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _slugify(name: str) -> str:
    s = re.sub(r'[^\w]+', '_', (name or '').strip().lower()).strip('_')
    if not s or any(ord(c) > 127 for c in s):
        s = f"g_{uuid.uuid4().hex[:8]}"
    return s

def _server_to_dict(s, db=None) -> dict:
    """Convert Server ORM object to API dict."""
    svc_count = 0
    if db:
        svc_count = db.query(Service).filter(Service.server_id == s.id).count()
    has_creds = bool(s.credential_ref)
    return {
        "id": str(s.id),
        "name": s.name,
        "host": s.host,
        "ssh_port": s.ssh_port,
        "ssh_user": s.ssh_user,
        "auth_type": s.auth_type or "password",
        "tags": s.tags or [],
        "status": s.status,
        "docker_available": s.docker_available,
        "is_local": s.is_local,
        "enabled": s.enabled if s.enabled is not None else True,
        "last_seen": s.last_seen.isoformat() if s.last_seen else None,
        "last_check_at": s.last_check_at.isoformat() if getattr(s, 'last_check_at', None) else None,
        "last_online_at": s.last_online_at.isoformat() if getattr(s, 'last_online_at', None) else None,
        "fail_count": getattr(s, 'fail_count', 0) or 0,
        "last_error": getattr(s, 'last_error', None),
        "remark": getattr(s, 'remark', None) or "",
        "service_count": svc_count,
        "has_credentials": has_creds,
    }

def _service_to_dict(s, server_info: dict = None) -> dict:
    """Convert Service ORM object to API dict."""
    d = {
        "id": str(s.id), "server_id": str(s.server_id),
        "name": s.name, "url": s.url, "category": s.category,
        "icon": s.icon, "description": s.description,
        "source": s.source, "status": s.status, "pinned": s.pinned,
        "health_path": s.health_path, "container_name": s.container_name,
        "image": s.image, "ports": s.ports,
        "discovery_type": getattr(s, 'discovery_type', None),
        "discovered_at": s.discovered_at.isoformat() if getattr(s, 'discovered_at', None) else None,
        "last_seen_at": s.last_seen_at.isoformat() if getattr(s, 'last_seen_at', None) else None,
        "port": getattr(s, 'port', None),
    }
    if server_info:
        d.update({
            "server_name": server_info.get("name", ""),
            "server_host": server_info.get("host", ""),
            "server_status": server_info.get("status", "unknown"),
            "server_is_local": server_info.get("is_local", False),
        })
    return d


# ============================================================
#  Background tasks
# ============================================================

def _run_health_check():
    """Synchronous health check for all services and servers."""
    import requests as req
    try:
        with get_db() as db:
            now = datetime.utcnow()

            # --- Service health ---
            services = db.query(Service).filter(Service.status != ServiceStatus.missing.value).all()
            for svc in services:
                if not svc.url:
                    continue
                try:
                    check_url = svc.url
                    if svc.health_path:
                        base = svc.url if svc.url.startswith("http") else ""
                        if not base:
                            srv = db.query(Server).filter(Server.id == svc.server_id).first()
                            host = srv.host if srv else LOCAL_HOST
                            base = f"http://{host}"
                        check_url = f"{base.rstrip('/')}/{svc.health_path.lstrip('/')}"
                    elif check_url.startswith("/"):
                        srv = db.query(Server).filter(Server.id == svc.server_id).first()
                        host = srv.host if srv else LOCAL_HOST
                        check_url = f"http://{host}{check_url}"
                    resp = req.head(check_url, timeout=5, allow_redirects=True, verify=False)
                    svc.status = ServiceStatus.up.value if resp.status_code < 500 else ServiceStatus.down.value
                except Exception:
                    svc.status = ServiceStatus.down.value
                if hasattr(svc, 'last_seen_at'):
                    svc.last_seen_at = now
            db.commit()

            # --- Server reachability ---
            servers = db.query(Server).all()
            for srv in servers:
                if srv.is_local:
                    srv.status = ServerStatus.online.value
                    srv.last_seen = now
                    if hasattr(srv, 'last_check_at'):
                        srv.last_check_at = now
                    if hasattr(srv, 'last_online_at'):
                        srv.last_online_at = now
                    continue

                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(3)
                    result = sock.connect_ex((srv.host, srv.ssh_port or 22))
                    sock.close()
                    if result == 0:
                        srv.status = ServerStatus.online.value
                        srv.last_seen = now
                        if hasattr(srv, 'last_check_at'):
                            srv.last_check_at = now
                        if hasattr(srv, 'last_online_at'):
                            srv.last_online_at = now
                        if hasattr(srv, 'fail_count'):
                            srv.fail_count = 0
                        if hasattr(srv, 'last_error'):
                            srv.last_error = None
                    else:
                        _mark_server_fail(srv, now, "TCP connect failed")
                except Exception as e:
                    _mark_server_fail(srv, now, str(e)[:200])
            db.commit()
    except Exception as e:
        print(f"Health check error: {e}")

def _mark_server_fail(srv, now, error_msg):
    """Mark a server as offline/warning with failure tracking."""
    prev_fail = getattr(srv, 'fail_count', 0) or 0
    new_fail = prev_fail + 1
    if hasattr(srv, 'fail_count'):
        srv.fail_count = new_fail
    if hasattr(srv, 'last_error'):
        srv.last_error = error_msg
    if hasattr(srv, 'last_check_at'):
        srv.last_check_at = now
    # After 3 consecutive failures -> offline; 1-2 -> warning
    if new_fail >= 3:
        srv.status = ServerStatus.offline.value
    else:
        srv.status = ServerStatus.warning.value

async def background_health_check():
    """Periodically check all services and servers health status."""
    while True:
        await asyncio.to_thread(_run_health_check)
        await asyncio.sleep(60)

async def background_discovery():
    """Periodically run full discovery for all servers."""
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        try:
            await asyncio.to_thread(_run_discovery_all)
        except Exception as e:
            print(f"Background discovery error: {e}")

def _run_discovery_all():
    """Run discovery for all enabled servers."""
    try:
        with get_db() as db:
            servers = db.query(Server).filter(Server.enabled != False).all()
            for srv in servers:
                try:
                    if srv.is_local:
                        run_full_discovery(srv, db, srv.host)
                    else:
                        _discovery_remote(srv, db)
                except Exception as e:
                    print(f"Discovery error for {srv.name}: {e}")
    except Exception as e:
        print(f"Discovery all error: {e}")

def _discovery_remote(srv, db):
    """Discover services on a remote server via SSH."""
    password = None
    if srv.auth_type == "password" and srv.credential_ref and srv.credential_ref.startswith("enc:"):
        # credential_ref stores encrypted reference; for now use password field
        pass
    elif hasattr(srv, 'ssh_key') and srv.ssh_key and srv.ssh_key.startswith("__password__"):
        password = srv.ssh_key[len("__password__"):]

    client = get_ssh_client(srv, password=password)
    if not client:
        return

    try:
        containers = discover_remote_docker_services(client, host=srv.host)
        from app.discovery import classify_image, get_icon, get_desc, get_url
        now = datetime.utcnow()
        active_names = set()
        for c in containers:
            name = c.get('name', '')
            image = c.get('image', '')
            status_str = c.get('status', '')
            ports = c.get('ports', '')
            is_running = 'Up' in status_str
            active_names.add(name)

            short_image = image.split(':')[0].split('/')[-1] if image else ''
            svc_name = name.replace('-', ' ').replace('_', ' ').title()
            svc_url = c.get('auto_url', '') or get_url(name, srv.host) or ''
            svc_category = classify_image(short_image)
            svc_icon = get_icon(short_image)
            svc_desc = get_desc(short_image, name)

            if not svc_url:
                continue

            existing = db.query(Service).filter(
                Service.server_id == srv.id, Service.container_name == name,
            ).first()
            if existing:
                for field, val in [
                    ("name", svc_name), ("url", svc_url), ("category", svc_category),
                    ("icon", svc_icon), ("description", svc_desc), ("image", image),
                    ("ports", ports), ("last_seen_at", now),
                ]:
                    if val is not None and getattr(existing, field) != val:
                        setattr(existing, field, val)
                if hasattr(existing, 'discovery_type'):
                    existing.discovery_type = "docker"
                existing.status = ServiceStatus.up.value if is_running else ServiceStatus.down.value
            else:
                svc = Service(
                    server_id=srv.id, name=svc_name, url=svc_url,
                    category=svc_category, icon=svc_icon, description=svc_desc,
                    source=ServiceSource.docker_auto.value,
                    status=ServiceStatus.up.value if is_running else ServiceStatus.down.value,
                    container_name=name, image=image, ports=ports,
                    discovery_type="docker", discovered_at=now, last_seen_at=now,
                )
                db.add(svc)

        # Mark stale
        stale = db.query(Service).filter(
            Service.server_id == srv.id,
            Service.source == ServiceSource.docker_auto.value,
            Service.status != ServiceStatus.down.value,
        ).all()
        for svc in stale:
            if svc.container_name and svc.container_name not in active_names:
                svc.status = ServiceStatus.down.value

        srv.status = ServerStatus.online.value
        srv.last_seen = now
        srv.docker_available = True
        if hasattr(srv, 'last_online_at'):
            srv.last_online_at = now
        if hasattr(srv, 'fail_count'):
            srv.fail_count = 0
        if hasattr(srv, 'last_error'):
            srv.last_error = None
        db.commit()
    except Exception as e:
        print(f"Remote discovery error for {srv.name}: {e}")
    finally:
        try:
            client.close()
        except:
            pass


# ============================================================
#  Startup
# ============================================================

@app.on_event("startup")
async def startup():
    import time
    # Wait for DB
    for i in range(30):
        try:
            Base.metadata.create_all(bind=engine)
            break
        except Exception:
            time.sleep(2)

    # Auto-register local server & initial discovery
    with get_db() as db:
        local = db.query(Server).filter(Server.is_local == True).first()
        if not local:
            local = Server(
                name="CI/CD 主机", host=LOCAL_HOST, ssh_port=22, ssh_user="ops",
                status=ServerStatus.online.value, docker_available=True, is_local=True,
                auth_type="key", enabled=True,
            )
            db.add(local)
            db.commit()
            db.refresh(local)

        # Run initial discovery
        run_full_discovery(local, db, LOCAL_HOST)

    # Start background tasks
    asyncio.create_task(background_health_check())
    asyncio.create_task(background_discovery())


# ============================================================
#  Home / Overview API
# ============================================================

@app.get("/api/v2/home")
def get_home():
    """Homepage data: only online/warning servers + their services."""
    with get_db() as db:
        servers = db.query(Server).filter(
            Server.status.in_([ServerStatus.online.value, ServerStatus.warning.value]),
            Server.enabled != False,
        ).all()

        total_servers = db.query(Server).count()
        online_servers = db.query(Server).filter(Server.status == ServerStatus.online.value).count()
        warning_servers = db.query(Server).filter(Server.status == ServerStatus.warning.value).count()
        offline_servers = db.query(Server).filter(Server.status == ServerStatus.offline.value).count()

        total_services = db.query(Service).count()
        up_services = db.query(Service).filter(Service.status == ServiceStatus.up.value).count()
        down_services = db.query(Service).filter(Service.status == ServiceStatus.down.value).count()

        server_list = []
        for s in servers:
            services = db.query(Service).filter(Service.server_id == s.id).all()
            server_list.append({
                **_server_to_dict(s, db),
                "services": [_service_to_dict(svc) for svc in services],
            })

        return {
            "stats": {
                "servers": {"total": total_servers, "online": online_servers, "warning": warning_servers, "offline": offline_servers},
                "services": {"total": total_services, "up": up_services, "down": down_services},
            },
            "servers": server_list,
        }


# ============================================================
#  Server APIs
# ============================================================

@app.get("/api/v2/servers")
def list_servers(status: Optional[str] = None, enabled: Optional[bool] = None):
    """List all servers, with optional filters."""
    with get_db() as db:
        q = db.query(Server)
        if status:
            q = q.filter(Server.status == status)
        if enabled is not None:
            q = q.filter(Server.enabled == enabled)
        servers = q.all()
        return [_server_to_dict(s, db) for s in servers]

@app.post("/api/v2/servers", status_code=201)
def create_server(data: ServerCreate):
    """Add a new server (manual, for remote hosts)."""
    with get_db() as db:
        # Store credential reference (never store plaintext)
        cred_ref = None
        if data.auth_type == "password" and data.ssh_password:
            cred_ref = f"enc:{_hash_credential(data.ssh_password)}"
            # Also keep __password__ format in ssh_key for backward compat with ssh_manager
            ssh_key_val = f"__password__{data.ssh_password}"
        elif data.auth_type == "key" and data.ssh_key:
            cred_ref = "ref:ssh_key"
            ssh_key_val = data.ssh_key
        else:
            ssh_key_val = None

        srv = Server(
            name=data.name, host=data.host, ssh_port=data.ssh_port,
            ssh_user=data.ssh_user, ssh_key=ssh_key_val,
            tags=data.tags, is_local=False,
            auth_type=data.auth_type,
            credential_ref=cred_ref,
            enabled=True,
            status=ServerStatus.unknown.value,
        )
        if data.remark:
            srv.remark = data.remark
        db.add(srv)
        db.commit()
        db.refresh(srv)
        return _server_to_dict(srv, db)

@app.get("/api/v2/servers/{server_id}")
def get_server(server_id: str):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        return _server_to_dict(srv, db)

@app.put("/api/v2/servers/{server_id}")
def update_server(server_id: str, data: ServerUpdate):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        updates = data.model_dump(exclude_unset=True)
        # Handle password/key updates
        if "ssh_password" in updates and updates["ssh_password"]:
            srv.ssh_key = f"__password__{updates.pop('ssh_password')}"
            srv.credential_ref = f"enc:{_hash_credential(srv.ssh_key.split('__password__')[1])}"
            if "auth_type" not in updates:
                srv.auth_type = "password"
        if "ssh_key" in updates and updates["ssh_key"]:
            srv.ssh_key = updates.pop("ssh_key")
            srv.credential_ref = "ref:ssh_key"
            if "auth_type" not in updates:
                srv.auth_type = "key"
        if "ssh_password" in updates:
            del updates["ssh_password"]
        if "ssh_key" in updates:
            del updates["ssh_key"]

        for field, val in updates.items():
            if val is not None and hasattr(srv, field):
                setattr(srv, field, val)
        db.commit()
        return _server_to_dict(srv, db)

@app.delete("/api/v2/servers/{server_id}")
def delete_server(server_id: str):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.is_local:
            raise HTTPException(400, "Cannot delete local server")
        # Delete associated services
        db.query(Service).filter(Service.server_id == srv.id).delete()
        db.delete(srv)
        db.commit()
        return {"ok": True}

@app.post("/api/v2/servers/{server_id}/check")
def check_server(server_id: str, password: Optional[str] = None):
    """Probe server connectivity and update status tracking fields."""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        now = datetime.utcnow()

        if srv.is_local:
            srv.status = ServerStatus.online.value
            srv.last_seen = now
            if hasattr(srv, 'last_check_at'):
                srv.last_check_at = now
            if hasattr(srv, 'last_online_at'):
                srv.last_online_at = now
            if hasattr(srv, 'fail_count'):
                srv.fail_count = 0
            db.commit()
            return _server_to_dict(srv, db)

        # Remote: try SSH
        client = get_ssh_client(srv, password=password)
        if client:
            srv.status = ServerStatus.online.value
            srv.last_seen = now
            if hasattr(srv, 'last_check_at'):
                srv.last_check_at = now
            if hasattr(srv, 'last_online_at'):
                srv.last_online_at = now
            if hasattr(srv, 'fail_count'):
                srv.fail_count = 0
            if hasattr(srv, 'last_error'):
                srv.last_error = None
            # Save password if provided
            if password:
                srv.ssh_key = f"__password__{password}"
                srv.credential_ref = f"enc:{_hash_credential(password)}"
                srv.auth_type = "password"
            db.commit()
            client.close()
            return _server_to_dict(srv, db)

        # Failed
        _mark_server_fail(srv, now, "SSH connection failed")
        if password:
            srv.auth_type = "password"
        db.commit()
        return _server_to_dict(srv, db)

@app.post("/api/v2/servers/{server_id}/scan")
def scan_server(server_id: str, password: Optional[str] = None):
    """Scan server for services (auto-discovery)."""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")

        if srv.is_local:
            results = run_full_discovery(srv, db, srv.host)
            return {"discovered": results, "message": "Local discovery complete"}

        # Remote: SSH-based scan
        client = get_ssh_client(srv, password=password)
        if not client:
            raise HTTPException(400, "Cannot connect to server. Check SSH credentials.")
        try:
            if password:
                srv.ssh_key = f"__password__{password}"
                srv.credential_ref = f"enc:{_hash_credential(password)}"
            _discovery_remote(srv, db)
            svc_count = db.query(Service).filter(Service.server_id == srv.id).count()
            return {"discovered": {"docker": svc_count}, "message": f"Found {svc_count} services on {srv.name}"}
        except Exception as e:
            raise HTTPException(500, f"Scan failed: {e}")
        finally:
            try:
                client.close()
            except:
                pass

@app.post("/api/v2/servers/{server_id}/enable")
def enable_server(server_id: str):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        srv.enabled = True
        db.commit()
        return {"ok": True}

@app.post("/api/v2/servers/{server_id}/disable")
def disable_server(server_id: str):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.is_local:
            raise HTTPException(400, "Cannot disable local server")
        srv.enabled = False
        db.commit()
        return {"ok": True}


# ============================================================
#  Service APIs  (NO manual creation in v4.0)
# ============================================================

@app.get("/api/v2/services")
def list_services(
    server_id: Optional[str] = None, category: Optional[str] = None,
    pinned: Optional[bool] = None, search: Optional[str] = None,
    source: Optional[str] = None, status: Optional[str] = None,
):
    with get_db() as db:
        q = db.query(Service)
        if server_id:
            q = q.filter(Service.server_id == uuid.UUID(server_id))
        if category:
            q = q.filter(Service.category == category)
        if pinned is not None:
            q = q.filter(Service.pinned == pinned)
        if search:
            q = q.filter(Service.name.ilike(f"%{search}%"))
        if source:
            q = q.filter(Service.source == source)
        if status:
            q = q.filter(Service.status == status)
        q = q.order_by(Service.sort_order, Service.category, Service.name)
        services = q.all()
        return [_service_to_dict(s) for s in services]

@app.post("/api/v2/services", status_code=403)
def create_service_disabled():
    """DISABLED in v4.0 - Services are auto-discovered only."""
    raise HTTPException(403, "Manual service creation is disabled in v4.0. Services are auto-discovered from Docker, Nginx, systemd, and port scanning.")

@app.put("/api/v2/services/{service_id}")
def update_service(service_id: str, data: ServiceUpdate):
    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
        if not svc:
            raise HTTPException(404, "Service not found")
        for field, val in data.model_dump(exclude_unset=True).items():
            if val is not None:
                setattr(svc, field, val)
        db.commit()
        return _service_to_dict(svc)

@app.delete("/api/v2/services/{service_id}")
def delete_service(service_id: str):
    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
        if not svc:
            raise HTTPException(404, "Service not found")
        # Only allow deleting non-auto services, or mark as missing
        if svc.source in (ServiceSource.docker_auto.value, ServiceSource.docker_label.value):
            svc.status = ServiceStatus.missing.value
            db.commit()
            return {"ok": True, "action": "marked_missing"}
        db.delete(svc)
        db.commit()
        return {"ok": True}

@app.patch("/api/v2/services/{service_id}/pin")
def toggle_pin(service_id: str):
    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
        if not svc:
            raise HTTPException(404, "Service not found")
        svc.pinned = not svc.pinned
        db.commit()
        return {"ok": True, "pinned": svc.pinned}

@app.get("/api/v2/services-with-status")
def list_services_with_status(server_id: Optional[str] = None):
    """List services with their server status included."""
    with get_db() as db:
        q = db.query(Service)
        if server_id:
            q = q.filter(Service.server_id == uuid.UUID(server_id))
        q = q.order_by(Service.sort_order, Service.category, Service.name)
        services = q.all()

        server_ids = set(s.server_id for s in services)
        servers_map = {}
        for sid in server_ids:
            srv = db.query(Server).filter(Server.id == sid).first()
            if srv:
                servers_map[str(sid)] = {"name": srv.name, "host": srv.host, "status": srv.status, "is_local": srv.is_local}

        return [_service_to_dict(s, servers_map.get(str(s.server_id))) for s in services]

# DISABLE manual-services endpoints
@app.post("/api/v2/manual-services", status_code=403)
def add_manual_service_disabled():
    raise HTTPException(403, "Manual service creation is disabled in v4.0.")

@app.get("/api/v2/manual-services")
def list_manual_services(server_id: Optional[str] = None):
    """Read-only: legacy manual services for migration reference."""
    data = _read_json(MANUAL_SERVICES_JSON_PATH, {"services": []})
    services = data.get("services", [])
    if server_id:
        services = [s for s in services if not s.get("serverId") or s.get("serverId") == server_id]
    return services


# ============================================================
#  Discovery APIs
# ============================================================

@app.post("/api/v2/discovery/run")
def run_discovery(server_id: Optional[str] = None):
    """Manually trigger discovery for a specific server or all servers."""
    with get_db() as db:
        if server_id:
            srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
            if not srv:
                raise HTTPException(404, "Server not found")
            if srv.is_local:
                results = run_full_discovery(srv, db, srv.host)
            else:
                _discovery_remote(srv, db)
                results = {"docker": db.query(Service).filter(Service.server_id == srv.id).count()}
            return {"discovered": results, "server": srv.name}
        else:
            all_results = {}
            servers = db.query(Server).filter(Server.enabled != False).all()
            for srv in servers:
                try:
                    if srv.is_local:
                        all_results[srv.name] = run_full_discovery(srv, db, srv.host)
                    else:
                        _discovery_remote(srv, db)
                        all_results[srv.name] = {"docker": db.query(Service).filter(Service.server_id == srv.id).count()}
                except Exception as e:
                    all_results[srv.name] = {"error": str(e)}
            return {"discovered": all_results}

@app.get("/api/v2/discovery/status")
def discovery_status():
    """Get discovery status for all servers."""
    with get_db() as db:
        servers = db.query(Server).all()
        result = []
        for srv in servers:
            svc_count = db.query(Service).filter(Service.server_id == srv.id).count()
            sources = db.query(Service.source).filter(Service.server_id == srv.id).distinct().all()
            result.append({
                "server_id": str(srv.id), "server_name": srv.name,
                "status": srv.status, "is_local": srv.is_local,
                "service_count": svc_count,
                "discovery_sources": [s[0] for s in sources],
                "last_seen": srv.last_seen.isoformat() if srv.last_seen else None,
            })
        return result


# ============================================================
#  Monitor APIs (Enhanced v4.0)
# ============================================================

@app.get("/api/v2/monitor/{server_id}")
def get_monitor(server_id: str):
    """Get real-time monitoring data for a server."""
    import requests as req
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")

    # Remote server: collect via SSH
    if not srv.is_local:
        password = None
        if hasattr(srv, 'ssh_key') and srv.ssh_key and srv.ssh_key.startswith("__password__"):
            password = srv.ssh_key[len("__password__"):]
        client = get_ssh_client(srv, password=password)
        if not client:
            return {
                "server_id": server_id, "timestamp": datetime.utcnow().isoformat(),
                "metrics": {}, "containers": [], "error": "Cannot connect via SSH",
            }
        try:
            m = collect_remote_metrics(client)
            containers = get_remote_containers(client)
            client.close()
            return {"server_id": server_id, "timestamp": datetime.utcnow().isoformat(), "metrics": m, "containers": containers}
        except Exception as e:
            try: client.close()
            except: pass
            return {"server_id": server_id, "timestamp": datetime.utcnow().isoformat(), "metrics": {}, "containers": [], "error": str(e)}

    # Local server: use Prometheus
    prom_url = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090")

    queries = {
        "cpu": '100 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100',
        "cpu_count": 'count(node_cpu_seconds_total{mode="idle"}) without (cpu)',
        "memory": '(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100',
        "memory_total": 'node_memory_MemTotal_bytes',
        "memory_avail": 'node_memory_MemAvailable_bytes',
        "memory_used": 'node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes',
        "disk": '(1 - node_filesystem_avail_bytes{fstype!="tmpfs",mountpoint="/"} / node_filesystem_size_bytes{fstype!="tmpfs",mountpoint="/"}) * 100',
        "disk_total": 'node_filesystem_size_bytes{fstype!="tmpfs",mountpoint="/"}',
        "disk_avail": 'node_filesystem_avail_bytes{fstype!="tmpfs",mountpoint="/"}',
        "disk_used": 'node_filesystem_size_bytes{fstype!="tmpfs",mountpoint="/"} - node_filesystem_avail_bytes{fstype!="tmpfs",mountpoint="/"}',
        "load1": 'node_load1',
        "load5": 'node_load5',
        "load15": 'node_load15',
        "net_rx": 'rate(node_network_receive_bytes_total{device!="lo"}[5m])',
        "net_tx": 'rate(node_network_transmit_bytes_total{device!="lo"}[5m])',
        "containers": 'count(container_last_seen)',
        "uptime": 'time() - node_boot_time_seconds',
    }

    result = {}
    for key, query in queries.items():
        try:
            resp = req.get(f"{prom_url}/api/v1/query", params={"query": query}, timeout=5)
            data = resp.json()
            if data.get("status") == "success" and data.get("data", {}).get("result"):
                val = data["data"]["result"][0]["value"][1]
                result[key] = float(val)
            else:
                result[key] = None
        except Exception:
            result[key] = None

    # Container list from Docker
    container_list = []
    try:
        import docker as docker_sdk
        dc = docker_sdk.from_env()
        for c in dc.containers.list(all=True):
            ports = []
            for p in (c.ports or {}).values():
                if p and isinstance(p, list):
                    for binding in p:
                        ports.append(f"{binding.get('HostIp','0.0.0.0')}:{binding.get('HostPort','?')}")
            try:
                img_name = c.attrs.get('Config', {}).get('Image', 'unknown')
                try:
                    img_name = c.image.tags[0] if c.image.tags else str(c.image.id[:12])
                except Exception:
                    pass
                container_list.append({
                    "name": c.name,
                    "image": img_name,
                    "status": c.status,
                    "ports": ", ".join(ports) if ports else "-",
                })
            except Exception:
                container_list.append({"name": getattr(c, 'name', '?'), "image": "error", "status": getattr(c, 'status', '?'), "ports": "-"})
    except Exception:
        pass

    return {"server_id": server_id, "timestamp": datetime.utcnow().isoformat(), "metrics": result, "containers": container_list}

@app.get("/api/v2/monitor/{server_id}/disks")
def get_monitor_disks(server_id: str):
    """Get all filesystem/disk details."""
    import requests as req
    prom_url = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090")
    try:
        resp = req.get(f"{prom_url}/api/v1/query", params={
            "query": 'node_filesystem_size_bytes{fstype!="tmpfs"}'
        }, timeout=5)
        data = resp.json()
        disks = []
        if data.get("status") == "success":
            for r in data["data"]["result"]:
                mp = r["metric"].get("mountpoint", "/")
                dev = r["metric"].get("device", "unknown")
                size = float(r["value"][1])
                disks.append({"mountpoint": mp, "device": dev, "size": size})
        # Get avail
        resp2 = req.get(f"{prom_url}/api/v1/query", params={
            "query": 'node_filesystem_avail_bytes{fstype!="tmpfs"}'
        }, timeout=5)
        avail_map = {}
        if resp2.json().get("status") == "success":
            for r in resp2.json()["data"]["result"]:
                mp = r["metric"].get("mountpoint", "/")
                avail_map[mp] = float(r["value"][1])
        for d in disks:
            d["avail"] = avail_map.get(d["mountpoint"], 0)
            d["used"] = d["size"] - d["avail"]
            d["percent"] = round(d["used"] / d["size"] * 100, 1) if d["size"] > 0 else 0
        return {"disks": disks}
    except Exception as e:
        return {"disks": [], "error": str(e)}

@app.get("/api/v2/monitor/{server_id}/network")
def get_monitor_network(server_id: str, hours: int = 1):
    """Get network traffic stats."""
    import requests as req
    prom_url = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090")
    try:
        resp = req.get(f"{prom_url}/api/v1/query", params={
            "query": 'rate(node_network_receive_bytes_total{device!="lo"}[5m])'
        }, timeout=5)
        rx = 0.0
        if resp.json().get("status") == "success":
            for r in resp.json()["data"]["result"]:
                rx += float(r["value"][1])

        resp2 = req.get(f"{prom_url}/api/v1/query", params={
            "query": 'rate(node_network_transmit_bytes_total{device!="lo"}[5m])'
        }, timeout=5)
        tx = 0.0
        if resp2.json().get("status") == "success":
            for r in resp2.json()["data"]["result"]:
                tx += float(r["value"][1])

        return {"rx_bytes_per_sec": rx, "tx_bytes_per_sec": tx}
    except Exception as e:
        return {"rx_bytes_per_sec": 0, "tx_bytes_per_sec": 0, "error": str(e)}

@app.get("/api/v2/monitor/{server_id}/containers")
def get_monitor_containers(server_id: str):
    """Get Docker container list with details."""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")

    if not srv.is_local:
        password = None
        if hasattr(srv, 'ssh_key') and srv.ssh_key and srv.ssh_key.startswith("__password__"):
            password = srv.ssh_key[len("__password__"):]
        client = get_ssh_client(srv, password=password)
        if not client:
            return {"containers": [], "error": "Cannot connect via SSH"}
        try:
            containers = get_remote_containers(client)
            client.close()
            return {"containers": containers}
        except Exception as e:
            try: client.close()
            except: pass
            return {"containers": [], "error": str(e)}

    # Local
    container_list = []
    try:
        import docker as docker_sdk
        dc = docker_sdk.from_env()
        for c in dc.containers.list(all=True):
            ports = []
            for p in (c.ports or {}).values():
                if p and isinstance(p, list):
                    for binding in p:
                        ports.append(f"{binding.get('HostIp','0.0.0.0')}:{binding.get('HostPort','?')}")
            container_list.append({
                "name": c.name,
                "image": c.image.tags[0] if c.image.tags else str(c.image.id[:12]),
                "status": c.status,
                "ports": ", ".join(ports) if ports else "-",
                "container_id": c.id[:12],
            })
    except Exception as e:
        return {"containers": [], "error": str(e)}
    return {"containers": container_list}

@app.get("/api/v2/monitor/{server_id}/history")
def get_monitor_history(server_id: str, metric: str = "cpu", hours: int = 24):
    """Get historical monitoring data from Prometheus."""
    import requests as req
    prom_url = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090")

    metric_queries = {
        "cpu": '100 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100',
        "memory": '(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100',
        "disk": '(1 - node_filesystem_avail_bytes{fstype!="tmpfs",mountpoint="/"} / node_filesystem_size_bytes{fstype!="tmpfs",mountpoint="/"}) * 100',
        "net_rx": 'rate(node_network_receive_bytes_total{device!="lo"}[5m])',
        "net_tx": 'rate(node_network_transmit_bytes_total{device!="lo"}[5m])',
        "load1": 'node_load1',
        "load5": 'node_load5',
        "load15": 'node_load15',
    }

    query = metric_queries.get(metric, metric_queries["cpu"])
    try:
        _now = datetime.utcnow().timestamp()
        _start = _now - hours * 3600
        resp = req.get(f"{prom_url}/api/v1/query_range", params={
            "query": query, "start": str(_start), "end": str(_now),
            "step": f"{max(hours * 60 // 200, 1)}m",
        }, timeout=10)
        data = resp.json()
        if data.get("status") == "success" and data.get("data", {}).get("result"):
            values = data["data"]["result"][0].get("values", [])
            return {"metric": metric, "values": [[v[0], float(v[1])] for v in values[-200:]]}
        return {"metric": metric, "values": []}
    except Exception as e:
        return {"metric": metric, "values": [], "error": str(e)}


# ============================================================
#  Health & Stats
# ============================================================

@app.post("/api/v2/health-check")
def trigger_health_check():
    """Manually trigger health check for all services."""
    import requests as req
    checked = 0
    with get_db() as db:
        services = db.query(Service).filter(Service.status != ServiceStatus.missing.value).all()
        for svc in services:
            if not svc.url:
                continue
            try:
                check_url = svc.url
                if svc.health_path:
                    base = svc.url if svc.url.startswith("http") else ""
                    if not base:
                        srv = db.query(Server).filter(Server.id == svc.server_id).first()
                        host = srv.host if srv else LOCAL_HOST
                        base = f"http://{host}"
                    check_url = f"{base.rstrip('/')}/{svc.health_path.lstrip('/')}"
                elif check_url.startswith("/"):
                    srv = db.query(Server).filter(Server.id == svc.server_id).first()
                    host = srv.host if srv else LOCAL_HOST
                    check_url = f"http://{host}{check_url}"
                resp = req.head(check_url, timeout=5, allow_redirects=True, verify=False)
                svc.status = ServiceStatus.up.value if resp.status_code < 500 else ServiceStatus.down.value
            except Exception:
                svc.status = ServiceStatus.down.value
            checked += 1
        db.commit()
    return {"checked": checked, "message": f"Health check completed for {checked} services"}

@app.get("/api/v2/health")
def health_check():
    with get_db() as db:
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        except:
            db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "version": "4.0", "timestamp": datetime.utcnow().isoformat()}

@app.get("/api/v2/stats")
def get_stats():
    with get_db() as db:
        server_count = db.query(Server).count()
        service_count = db.query(Service).count()
        up_count = db.query(Service).filter(Service.status == ServiceStatus.up.value).count()
        down_count = db.query(Service).filter(Service.status == ServiceStatus.down.value).count()
        offline_srv = db.query(Server).filter(Server.status == ServerStatus.offline.value).count()
        return {
            "servers": server_count, "services": service_count,
            "up": up_count, "down": down_count, "offline_servers": offline_srv,
        }

@app.get("/api/v2/health-check-url")
def health_check_url(url: str = Query(..., description="URL to check")):
    """Check if a URL is reachable from server side."""
    import requests as req
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        if url.startswith("/"):
            url = f"http://{LOCAL_HOST}{url}"
        else:
            return {"status": "error", "message": "Invalid URL"}
    try:
        resp = req.head(url, timeout=5, allow_redirects=True, verify=False)
        if resp.status_code < 500:
            return {"status": "online", "code": resp.status_code, "url": url}
        return {"status": "offline", "code": resp.status_code, "url": url}
    except req.exceptions.SSLError:
        http_url = url.replace("https://", "http://", 1)
        try:
            resp2 = req.head(http_url, timeout=5, allow_redirects=True, verify=False)
            if resp2.status_code < 500:
                return {"status": "online", "code": resp2.status_code, "url": http_url, "note": "HTTP fallback"}
            return {"status": "offline", "code": resp2.status_code, "url": http_url}
        except Exception:
            return {"status": "offline", "url": http_url}
    except Exception as e:
        return {"status": "offline", "url": url, "note": str(e)[:100]}


# ============================================================
#  Categories
# ============================================================

@app.get("/api/v2/categories")
def list_categories(server_id: Optional[str] = None):
    with get_db() as db:
        q = db.query(Service.category).distinct()
        if server_id:
            q = q.filter(Service.server_id == uuid.UUID(server_id))
        cats = [r[0] for r in q.all()]
        result = []
        for name in cats:
            meta = CATEGORY_META.get(name, CATEGORY_META["未分类"])
            q2 = db.query(Service).filter(Service.category == name)
            if server_id:
                q2 = q2.filter(Service.server_id == uuid.UUID(server_id))
            svc_count = q2.count()
            result.append({"name": name, "icon": meta["icon"], "color": meta["color"], "order": meta["order"], "count": svc_count})
        result.sort(key=lambda x: x["order"])
        return result


# ============================================================
#  Group APIs (persisted to groups.json)
# ============================================================

@app.get("/api/v2/group-config")
def get_group_config():
    return _read_json(GROUPS_JSON_PATH, {"groups": [], "serviceGroupMap": {}})

@app.put("/api/v2/group-config")
def update_group_config(data: dict):
    _write_json(GROUPS_JSON_PATH, data)
    return {"ok": True}

@app.post("/api/v2/groups", status_code=201)
def create_group(item: GroupCreate):
    current = _read_json(GROUPS_JSON_PATH, {"groups": [], "serviceGroupMap": {}})
    groups = current.get("groups", [])
    base = _slugify(item.name)
    gid = base
    i = 1
    while any(g.get("id") == gid for g in groups):
        gid = f"{base}_{i}"
        i += 1
    order = max([g.get("order", 100) for g in groups], default=0) + 1
    new_group = {"id": gid, "name": item.name, "color": item.color, "icon": item.icon, "order": order}
    groups.append(new_group)
    current["groups"] = groups
    _write_json(GROUPS_JSON_PATH, current)
    return {"ok": True, "group": new_group}

@app.patch("/api/v2/groups/{group_id}")
def update_group(group_id: str, data: GroupUpdate):
    current = _read_json(GROUPS_JSON_PATH, {"groups": [], "serviceGroupMap": {}})
    groups = current.get("groups", [])
    found = False
    for g in groups:
        if g.get("id") == group_id:
            if data.name is not None: g["name"] = data.name
            if data.color is not None: g["color"] = data.color
            if data.icon is not None: g["icon"] = data.icon
            if data.order is not None: g["order"] = data.order
            found = True
            break
    if not found:
        raise HTTPException(404, "Group not found")
    _write_json(GROUPS_JSON_PATH, current)
    return {"ok": True, "group": next(g for g in groups if g["id"] == group_id)}

@app.delete("/api/v2/groups/{group_id}")
def delete_group(group_id: str):
    if group_id == "ungrouped":
        raise HTTPException(400, "Cannot delete the 'ungrouped' group")
    current = _read_json(GROUPS_JSON_PATH, {"groups": [], "serviceGroupMap": {}})
    groups = current.get("groups", [])
    target = next((g for g in groups if g.get("id") == group_id), None)
    if not target:
        raise HTTPException(404, "Group not found")
    current["groups"] = [g for g in groups if g.get("id") != group_id]
    svc_map = current.get("serviceGroupMap", {})
    current["serviceGroupMap"] = {k: v for k, v in svc_map.items() if v != group_id}
    _write_json(GROUPS_JSON_PATH, current)
    return {"ok": True, "removed": group_id}

@app.patch("/api/v2/service-group")
def move_service_group(data: ServiceGroupMove):
    current = _read_json(GROUPS_JSON_PATH, {"groups": [], "serviceGroupMap": {}})
    svc_map = current.get("serviceGroupMap", {})
    if data.groupId == "ungrouped":
        svc_map.pop(data.serviceKey, None)
    else:
        if not any(g.get("id") == data.groupId for g in current.get("groups", [])):
            raise HTTPException(400, "Group not found")
        svc_map[data.serviceKey] = data.groupId
    current["serviceGroupMap"] = svc_map
    _write_json(GROUPS_JSON_PATH, current)
    return {"ok": True}


# ============================================================
#  Scan All (legacy compat)
# ============================================================

@app.post("/api/v2/scan")
def scan_all():
    """Scan all enabled servers."""
    _run_discovery_all()
    with get_db() as db:
        count = db.query(Service).count()
    return {"discovered": count, "message": f"Scan complete, {count} total services"}
