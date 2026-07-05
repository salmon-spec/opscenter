import os, uuid
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
    tags: List[str] = []
    is_local: bool = False

class ServerUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    ssh_key: Optional[str] = None
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Startup ===
@app.on_event("startup")
def startup():
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


# === Server APIs ===
@app.get("/api/v2/servers")
def list_servers():
    with get_db() as db:
        servers = db.query(Server).all()
        result = []
        for s in servers:
            svc_count = db.query(Service).filter(Service.server_id == s.id).count()
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
            })
        return result

@app.post("/api/v2/servers", status_code=201)
def create_server(data: ServerCreate):
    with get_db() as db:
        srv = Server(
            name=data.name, host=data.host, ssh_port=data.ssh_port,
            ssh_user=data.ssh_user, ssh_key=data.ssh_key,
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
def scan_server(server_id: str):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        discovered = discover_docker_services(srv, db, srv.host)
        return {"discovered": len(discovered)}

@app.post("/api/v2/servers/{server_id}/test")
def test_server(server_id: str):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.is_local:
            srv.status = ServerStatus.online.value
            srv.last_seen = datetime.utcnow()
            db.commit()
            return {"status": "online", "message": "Local server is always accessible"}
        # For remote servers, try SSH (Phase 2)
        return {"status": "unknown", "message": "SSH probe not yet implemented"}


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
def toggle_pin(service_id: str, data: PinToggle):
    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
        if not svc:
            raise HTTPException(404, "Service not found")
        svc.pinned = data.pinned
        db.commit()
        return {"ok": True, "pinned": svc.pinned}


# === Scan & Discovery ===
@app.post("/api/v2/scan")
def scan_all():
    with get_db() as db:
        count = 0
        servers = db.query(Server).filter(Server.is_local == True).all()
        for srv in servers:
            if srv.docker_available:
                discovered = discover_docker_services(srv, db, srv.host)
                count += len(discovered)
        return {"discovered": count, "message": f"Scan complete, {count} services updated/added"}


# === Monitor (Prometheus proxy) ===
@app.get("/api/v2/monitor/{server_id}")
def get_monitor(server_id: str):
    """Get real monitoring data from Prometheus for a server."""
    import requests as req
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
    
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
        return cats


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
