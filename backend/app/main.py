import os, uuid, asyncio
from datetime import datetime
from typing import Optional, List
from contextlib import contextmanager

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool

from app.models import Base, Server, Service, ServerStatus, ServiceStatus, ServiceSource
from app.discovery import discover_docker_services, parse_nginx_config
from app.ssh_manager import get_ssh_client, ssh_exec, discover_remote_docker_services, collect_remote_metrics, get_remote_containers, test_ssh_connection

# === Config ===
DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://opscenter:OpsCenter2026@127.0.0.1:5433/opscenter")
LOCAL_HOST = os.getenv("LOCAL_HOST", "39.99.157.36")

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

# === Schemas ===
class ServerCreate(BaseModel):
    name: str
    host: str
    ssh_port: int = 22
    ssh_user: str = "ops"
    ssh_key: Optional[str] = None
    ssh_password: Optional[str] = None
    tags: List[str] = []
    is_local: bool = False

class ServerUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    ssh_key: Optional[str] = None
    ssh_password: Optional[str] = None
    tags: Optional[List[str]] = None

class ServiceCreate(BaseModel):
    name: str
    url: str
    category: str = "未分类"
    icon: str = "fa-cube"
    description: str = ""
    health_path: Optional[str] = None
    pinned: bool = False

class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    health_path: Optional[str] = None
    pinned: Optional[bool] = None

class PinToggle(BaseModel):
    pinned: bool

# === App ===
app = FastAPI(title="OpsCenter API", version="2.0")



# Category metadata for enhanced UI
CATEGORY_META = {
    "代码与CI/CD": {"icon": "fa-code", "color": "#8b5cf6", "order": 1},
    "应用服务": {"icon": "fa-cube", "color": "#3b82f6", "order": 2},
    "监控与日志": {"icon": "fa-chart-area", "color": "#22c55e", "order": 3},
    "网络与代理": {"icon": "fa-network-wired", "color": "#f59e0b", "order": 4},
    "自动化工作流": {"icon": "fa-robot", "color": "#ec4899", "order": 5},
    "数据存储": {"icon": "fa-database", "color": "#06b6d4", "order": 6},
    "运维管理": {"icon": "fa-gauge-high", "color": "#f97316", "order": 7},
    "安全": {"icon": "fa-shield-halved", "color": "#ef4444", "order": 8},
    "未分类": {"icon": "fa-folder", "color": "#94a3b8", "order": 99},
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Startup ===

# === Background Health Check ===
async def background_health_check():
    """Periodically check all services health status."""
    import requests as req
    while True:
        try:
            with get_db() as db:
                services = db.query(Service).all()
                for svc in services:
                    if not svc.url or svc.url == "/":
                        continue
                    try:
                        url = svc.url
                        if url.startswith("/"):
                            srv = db.query(Server).filter(Server.id == svc.server_id).first()
                            host = srv.host if srv else LOCAL_HOST
                            url = f"http://{host}{url}"
                        resp = req.head(url, timeout=5, allow_redirects=True, verify=False)
                        svc.status = ServiceStatus.up.value if resp.status_code < 500 else ServiceStatus.down.value
                    except Exception:
                        svc.status = ServiceStatus.down.value
                db.commit()
        except Exception as e:
            print(f"Health check error: {e}")
        await asyncio.sleep(60)

@app.on_event("startup")
async def startup():
    # Wait for DB and create tables
    import time
    for i in range(30):
        try:
            Base.metadata.create_all(bind=engine)
            break
        except Exception:
            time.sleep(2)
    
    # Auto-register local server and discover services
    with get_db() as db:
        local = db.query(Server).filter(Server.is_local == True).first()
        if not local:
            local = Server(
                name="CI/CD 主机",
                host=LOCAL_HOST,
                ssh_port=22,
                ssh_user="ops",
                status=ServerStatus.online.value,
                docker_available=True,
                is_local=True,
            )
            db.add(local)
            db.commit()
            db.refresh(local)
        
        # Run initial discovery
        discover_docker_services(local, db, LOCAL_HOST)
        
        # Parse nginx config
        nginx_routes = parse_nginx_config(host=LOCAL_HOST)
        for route in nginx_routes:
            existing = db.query(Service).filter(
                Service.server_id == local.id,
                Service.url == route["url"],
                Service.source != ServiceSource.docker_label.value,
                Service.source != ServiceSource.docker_auto.value,
            ).first()
            if not existing:
                svc = Service(
                    server_id=local.id,
                    name=route["name"],
                    url=route["url"],
                    source=ServiceSource.nginx.value,
                    category="未分类",
                    icon="fa-globe",
                    status=ServiceStatus.unknown.value,
                )
                db.add(svc)
        db.commit()

    # Start background health check
    asyncio.create_task(background_health_check())


# === Server APIs ===
@app.get("/api/v2/servers")
def list_servers():
    with get_db() as db:
        servers = db.query(Server).all()
        result = []
        for s in servers:
            svc_count = db.query(Service).filter(Service.server_id == s.id).count()
            has_creds = bool(s.ssh_key and (s.ssh_key.startswith("__password__") or "BEGIN" in s.ssh_key))
            result.append({
                "id": str(s.id),
                "name": s.name,
                "host": s.host,
                "ssh_port": s.ssh_port,
                "ssh_user": s.ssh_user,
                "tags": s.tags or [],
                "status": s.status,
                "docker_available": s.docker_available,
                "is_local": s.is_local,
                "last_seen": s.last_seen.isoformat() if s.last_seen else None,
                "service_count": svc_count,
                "has_credentials": has_creds,
            })
        return result

@app.post("/api/v2/servers", status_code=201)
def create_server(data: ServerCreate):
    with get_db() as db:
        ssh_key_val = data.ssh_key
        if data.ssh_password and not data.ssh_key:
            ssh_key_val = f"__password__{data.ssh_password}"
        srv = Server(
            name=data.name, host=data.host, ssh_port=data.ssh_port,
            ssh_user=data.ssh_user, ssh_key=ssh_key_val,
            tags=data.tags, is_local=data.is_local,
        )
        db.add(srv)
        db.commit()
        db.refresh(srv)
        return {"id": str(srv.id), "name": srv.name, "host": srv.host}

@app.get("/api/v2/servers/{server_id}")
def get_server(server_id: str):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        return {
            "id": str(srv.id), "name": srv.name, "host": srv.host,
            "ssh_port": srv.ssh_port, "ssh_user": srv.ssh_user,
            "tags": srv.tags or [], "status": srv.status,
            "docker_available": srv.docker_available, "is_local": srv.is_local,
            "last_seen": srv.last_seen.isoformat() if srv.last_seen else None,
        }

@app.put("/api/v2/servers/{server_id}")
def update_server(server_id: str, data: ServerUpdate):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        for field, val in data.model_dump(exclude_unset=True).items():
            if val is not None:
                setattr(srv, field, val)
        db.commit()
        return {"ok": True}

@app.delete("/api/v2/servers/{server_id}")
def delete_server(server_id: str):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.is_local:
            raise HTTPException(400, "Cannot delete local server")
        db.delete(srv)
        db.commit()
        return {"ok": True}

@app.post("/api/v2/servers/{server_id}/scan")
def scan_server(server_id: str, password: Optional[str] = None):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        
        if srv.is_local:
            discovered = discover_docker_services(srv, db, srv.host)
            return {"discovered": len(discovered)}
        
        # Remote server: need SSH credentials
        client = get_ssh_client(srv, password=password)
        if not client:
            raise HTTPException(400, "Cannot connect to server. Check SSH credentials.")
        try:
            containers = discover_remote_docker_services(client, host=srv.host)
            count = 0
            from app.discovery import classify_image, get_icon, get_desc, get_url
            for c in containers:
                name = c.get('name', '')
                image = c.get('image', '')
                status_str = c.get('status', '')
                ports = c.get('ports', '')
                is_running = c.get('is_running', 'Up' in status_str)
                
                short_image = image.split(':')[0].split('/')[-1] if image else ''
                svc_name = name.replace('-', ' ').replace('_', ' ').title()
                svc_url = c.get('auto_url', '') or get_url(name, srv.host) or ''
                svc_category = classify_image(short_image)
                svc_icon = get_icon(short_image)
                svc_desc = get_desc(short_image, name)
                
                if not svc_url:
                    continue
                
                existing = db.query(Service).filter(
                    Service.server_id == srv.id,
                    Service.container_name == name,
                ).first()
                if existing:
                    for field, val in [("name", svc_name), ("url", svc_url), ("category", svc_category), ("icon", svc_icon), ("description", svc_desc), ("image", image), ("ports", ports)]:
                        if val and getattr(existing, field) != val:
                            setattr(existing, field, val)
                    existing.status = ServiceStatus.up.value if is_running else ServiceStatus.down.value
                else:
                    svc = Service(
                        server_id=srv.id, name=svc_name, url=svc_url,
                        category=svc_category, icon=svc_icon, description=svc_desc,
                        source=ServiceSource.docker_auto.value,
                        status=ServiceStatus.up.value if is_running else ServiceStatus.down.value,
                        container_name=name, image=image, ports=ports,
                    )
                    db.add(svc)
                count += 1
            
            srv.status = ServerStatus.online.value
            srv.last_seen = datetime.utcnow()
            srv.docker_available = True
            db.commit()
            return {"discovered": count}
        except Exception as e:
            raise HTTPException(500, f"Scan failed: {e}")
        finally:
            try:
                client.close()
            except:
                pass

@app.post("/api/v2/servers/{server_id}/test")
def test_server(server_id: str, password: Optional[str] = None):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.is_local:
            srv.status = ServerStatus.online.value
            srv.last_seen = datetime.utcnow()
            db.commit()
            return {"status": "online", "message": "Local server is always accessible"}
        
        # Remote server: test SSH connection
        client = get_ssh_client(srv, password=password)
        if client:
            srv.status = ServerStatus.online.value
            srv.last_seen = datetime.utcnow()
            # Store password if provided (for future auto-scans)
            if password:
                srv.ssh_key = f"__password__{password}"
            db.commit()
            client.close()
            return {"status": "online", "message": "SSH connection successful"}
        return {"status": "offline", "message": "Cannot connect via SSH. Check credentials."}


# === Service APIs ===
@app.get("/api/v2/services")
def list_services(server_id: Optional[str] = None, category: Optional[str] = None, pinned: Optional[bool] = None, search: Optional[str] = None):
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
        q = q.order_by(Service.sort_order, Service.category, Service.name)
        services = q.all()
        result = []
        for s in services:
            result.append({
                "id": str(s.id), "server_id": str(s.server_id),
                "name": s.name, "url": s.url, "category": s.category,
                "icon": s.icon, "description": s.description,
                "source": s.source, "status": s.status, "pinned": s.pinned,
                "health_path": s.health_path, "container_name": s.container_name,
                "image": s.image, "ports": s.ports,
            })
        return result

@app.post("/api/v2/services", status_code=201)
def create_service(data: ServiceCreate, server_id: str = Query(...)):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        svc = Service(
            server_id=srv.id, name=data.name, url=data.url,
            category=data.category, icon=data.icon, description=data.description,
            health_path=data.health_path, pinned=data.pinned,
            source=ServiceSource.manual.value, status=ServiceStatus.unknown.value,
        )
        db.add(svc)
        db.commit()
        db.refresh(svc)
        return {"id": str(svc.id), "name": svc.name}

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
        return {"ok": True}

@app.delete("/api/v2/services/{service_id}")
def delete_service(service_id: str):
    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
        if not svc:
            raise HTTPException(404, "Service not found")
        if svc.source == ServiceSource.docker_label.value:
            raise HTTPException(400, "Cannot delete auto-discovered service (disable label instead)")
        db.delete(svc)
        db.commit()
        return {"ok": True}

@app.patch("/api/v2/services/{service_id}/pin")
def toggle_pin(service_id: str):
    """Toggle service pin status."""
    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
        if not svc:
            raise HTTPException(404, "Service not found")
        svc.pinned = not svc.pinned
        db.commit()
        return {"ok": True, "pinned": svc.pinned}


# === Health Check Trigger ===
@app.post("/api/v2/health-check")
def trigger_health_check():
    """Manually trigger health check for all services."""
    import requests as req
    checked = 0
    with get_db() as db:
        services = db.query(Service).all()
        for svc in services:
            if not svc.url or svc.url == "/":
                continue
            try:
                url = svc.url
                if url.startswith("/"):
                    srv = db.query(Server).filter(Server.id == svc.server_id).first()
                    host = srv.host if srv else LOCAL_HOST
                    url = f"http://{host}{url}"
                resp = req.head(url, timeout=5, allow_redirects=True, verify=False)
                svc.status = ServiceStatus.up.value if resp.status_code < 500 else ServiceStatus.down.value
            except Exception:
                svc.status = ServiceStatus.down.value
            checked += 1
        db.commit()
    return {"checked": checked, "message": f"Health check completed for {checked} services"}


@app.post("/api/v2/servers/{server_id}/ssh-test")
def ssh_test(server_id: str, password: Optional[str] = None):
    """Test SSH connection and auto-scan if successful."""
    from app.discovery import classify_image, get_icon, get_desc, get_url
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.is_local:
            return {"success": True, "message": "Local server, no SSH needed"}
        
        client = get_ssh_client(srv, password=password)
        if not client:
            return {"success": False, "message": "SSH连接失败，请检查账号密码"}
        
        # Save credentials
        if password:
            srv.ssh_key = f"__password__{password}"
        srv.status = ServerStatus.online.value
        srv.last_seen = datetime.utcnow()
        
        # Auto-discover
        containers = discover_remote_docker_services(client, host=srv.host)
        count = 0
        for c in containers:
            name = c.get('name', '')
            image = c.get('image', '')
            status_str = c.get('status', '')
            ports = c.get('ports', '')
            is_running = c.get('is_running', 'Up' in status_str)
            
            short_image = image.split(':')[0].split('/')[-1] if image else ''
            svc_name = name.replace('-', ' ').replace('_', ' ').title()
            svc_url = c.get('auto_url', '') or get_url(name, srv.host) or ''
            svc_category = classify_image(short_image)
            svc_icon = get_icon(short_image)
            svc_desc = get_desc(short_image, name)
            
            if not svc_url:
                continue
            
            existing = db.query(Service).filter(
                Service.server_id == srv.id,
                Service.container_name == name,
            ).first()
            if existing:
                for field, val in [("name", svc_name), ("url", svc_url), ("category", svc_category), ("icon", svc_icon), ("description", svc_desc), ("image", image), ("ports", ports)]:
                    if val and getattr(existing, field) != val:
                        setattr(existing, field, val)
                existing.status = ServiceStatus.up.value if is_running else ServiceStatus.down.value
            else:
                svc = Service(
                    server_id=srv.id, name=svc_name, url=svc_url,
                    category=svc_category, icon=svc_icon, description=svc_desc,
                    source=ServiceSource.docker_auto.value,
                    status=ServiceStatus.up.value if is_running else ServiceStatus.down.value,
                    container_name=name, image=image, ports=ports,
                )
                db.add(svc)
            count += 1
        
        srv.docker_available = True
        db.commit()
        client.close()
        return {"success": True, "message": f"SSH连接成功，发现 {count} 个服务", "discovered": count}

# === Scan & Discovery ===
@app.post("/api/v2/scan")
def scan_all():
    with get_db() as db:
        count = 0
        # Local servers
        servers = db.query(Server).filter(Server.is_local == True).all()
        for srv in servers:
            if srv.docker_available:
                discovered = discover_docker_services(srv, db, srv.host)
                count += len(discovered)
        # Remote servers with credentials
        remote_servers = db.query(Server).filter(Server.is_local == False).all()
        for srv in remote_servers:
            if not srv.ssh_key and not hasattr(srv, '_password'):
                continue
            client = get_ssh_client(srv)
            if not client:
                continue
            try:
                containers = discover_remote_docker_services(client, host=srv.host)
                for c in containers:
                    name = c.get('name', '')
                    image = c.get('image', '')
                    status_str = c.get('status', '')
                    ports = c.get('ports', '')
                    is_running = c.get('is_running', 'Up' in status_str)
                    
                    # Auto-classify
                    short_image = image.split(':')[0].split('/')[-1] if image else ''
                    from app.discovery import classify_image, get_icon, get_desc, get_url
                    svc_name = name.replace('-', ' ').replace('_', ' ').title()
                    svc_url = c.get('auto_url', '') or get_url(name, srv.host) or ''
                    svc_category = classify_image(short_image)
                    svc_icon = get_icon(short_image)
                    svc_desc = get_desc(short_image, name)
                    
                    if not svc_url:
                        continue
                    
                    # Upsert
                    existing = db.query(Service).filter(
                        Service.server_id == srv.id,
                        Service.container_name == name,
                    ).first()
                    if existing:
                        updated = False
                        for field, val in [("name", svc_name), ("url", svc_url), ("category", svc_category), ("icon", svc_icon), ("description", svc_desc), ("image", image), ("ports", ports)]:
                            if val and getattr(existing, field) != val:
                                setattr(existing, field, val)
                                updated = True
                        if updated:
                            count += 1
                    else:
                        svc = Service(
                            server_id=srv.id,
                            name=svc_name,
                            url=svc_url,
                            category=svc_category,
                            icon=svc_icon,
                            description=svc_desc,
                            source=ServiceSource.docker_auto.value,
                            status=ServiceStatus.up.value if is_running else ServiceStatus.down.value,
                            container_name=name,
                            image=image,
                            ports=ports,
                        )
                        db.add(svc)
                        count += 1
                # Update server status
                srv.status = ServerStatus.online.value
                srv.last_seen = datetime.utcnow()
                db.commit()
            except Exception as e:
                print(f"Remote scan error for {srv.host}: {e}")
            finally:
                try:
                    client.close()
                except:
                    pass
        return {"discovered": count, "message": f"Scan complete, {count} services updated/added"}


# === Monitor (Prometheus proxy) ===
@app.get("/api/v2/monitor/{server_id}")
def get_monitor(server_id: str):
    """Get real monitoring data for a server (local via Prometheus, remote via SSH)."""
    import requests as req
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
    
    # Remote server: collect via SSH
    if not srv.is_local:
        password = None
        if srv.ssh_key and srv.ssh_key.startswith("__password__"):
            password = srv.ssh_key[len("__password__"):]
        client = get_ssh_client(srv, password=password)
        if not client:
            return {
                "server_id": server_id,
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": {"cpu_percent": 0, "memory_percent": 0, "disk_percent": 0, "cpu_count": 0,
                           "memory_total": 0, "memory_used": 0, "disk_total": 0, "disk_used": 0, "disk_avail": 0,
                           "load1": 0, "load5": 0, "load15": 0, "net_rx_bytes": 0, "net_tx_bytes": 0, "containers": 0},
                "containers": [],
                "error": "Cannot connect via SSH",
            }
        try:
            m = collect_remote_metrics(client)
            containers = get_remote_containers(client)
            client.close()
            return {
                "server_id": server_id,
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": m,
                "containers": containers,
            }
        except Exception as e:
            try: client.close()
            except: pass
            return {
                "server_id": server_id,
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": {},
                "containers": [],
                "error": str(e),
            }
    
    # Local server: use Prometheus
    prom_url = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090")
    
    queries = {
        "cpu": '100 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100',
        "memory": '(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100',
        "memory_total": 'node_memory_MemTotal_bytes',
        "memory_avail": 'node_memory_MemAvailable_bytes',
        "disk": '(1 - node_filesystem_avail_bytes{fstype!="tmpfs",mountpoint="/"} / node_filesystem_size_bytes{fstype!="tmpfs",mountpoint="/"}) * 100',
        "disk_total": 'node_filesystem_size_bytes{fstype!="tmpfs",mountpoint="/"}',
        "disk_avail": 'node_filesystem_avail_bytes{fstype!="tmpfs",mountpoint="/"}',
        "load1": 'node_load1',
        "load5": 'node_load5',
        "load15": 'node_load15',
        "net_rx": 'rate(node_network_receive_bytes_total{device!="lo"}[5m])',
        "net_tx": 'rate(node_network_transmit_bytes_total{device!="lo"}[5m])',
        "containers": 'count(container_last_seen)',
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
    
    # Get container list from Docker if available
    container_list = []
    try:
        import docker as docker_sdk
        dc = docker_sdk.from_env()
        for c in dc.containers.list(all=True):
            ports = []
            for p in c.ports.values():
                if p and isinstance(p, list):
                    for binding in p:
                        ports.append(f"{binding.get('HostIp','0.0.0.0')}:{binding.get('HostPort','?')}")
            container_list.append({
                "name": c.name,
                "image": c.image.tags[0] if c.image.tags else str(c.image.id[:12]),
                "status": c.status,
                "ports": ", ".join(ports) if ports else "-",
            })
    except Exception:
        pass

    return {
        "server_id": server_id,
        "timestamp": datetime.utcnow().isoformat(),
        "metrics": result,
        "containers": container_list,
    }


# === Monitor History ===
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
    }
    
    query = metric_queries.get(metric, metric_queries["cpu"])
    
    try:
        from datetime import datetime as _dt
        _now = _dt.utcnow().timestamp()
        _start = _now - hours * 3600
        resp = req.get(f"{prom_url}/api/v1/query_range", params={
            "query": query,
            "start": str(_start),
            "end": str(_now),
            "step": f"{max(hours * 60 // 200, 1)}m",
        }, timeout=10)
        data = resp.json()
        if data.get("status") == "success" and data.get("data", {}).get("result"):
            values = data["data"]["result"][0].get("values", [])
            return {"metric": metric, "values": [[v[0], float(v[1])] for v in values[-200:]]}
        return {"metric": metric, "values": []}
    except Exception as e:
        return {"metric": metric, "values": [], "error": str(e)}


# === Categories ===
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
            result.append({
                "name": name,
                "icon": meta["icon"],
                "color": meta["color"],
                "order": meta["order"],
                "count": svc_count,
            })
        result.sort(key=lambda x: x["order"])
        return result


# === Health ===
@app.get("/api/v2/health")
def health_check():
    with get_db() as db:
        try:
            db.execute("SELECT 1")
            db_ok = True
        except:
            db_ok = False
    
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "timestamp": datetime.utcnow().isoformat(),
    }


# === Stats ===
@app.get("/api/v2/stats")
def get_stats():
    with get_db() as db:
        server_count = db.query(Server).count()
        service_count = db.query(Service).count()
        up_count = db.query(Service).filter(Service.status == ServiceStatus.up.value).count()
        down_count = db.query(Service).filter(Service.status == ServiceStatus.down.value).count()
        return {
            "servers": server_count,
            "services": service_count,
            "up": up_count,
            "down": down_count,
        }
