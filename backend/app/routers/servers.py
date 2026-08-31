"""Server management routes"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
import uuid

from app.database import get_session
from app.models import Server
from app.config import LOCAL_HOST, LOCAL_SERVER_NAME, LOCAL_AGENT_TOKEN

router = APIRouter(tags=["servers"])


# ── Schemas ──

class ServerCreate(BaseModel):
    name: str
    host: str
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_key: Optional[str] = None
    tags: Optional[List[str]] = None
    remark: Optional[str] = None
    agent_type: str = "remote"


class SSHTestRequest(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    password: Optional[str] = None


# ── CRUD ──

@router.get("/servers")
def list_servers(db: Session = Depends(get_session)):
    servers = db.query(Server).filter(Server.enabled == True)\
        .order_by(Server.is_local.desc(), Server.name).all()
    return [{
        "id": str(s.id), "name": s.name, "host": s.host,
        "status": s.status or "unknown", "is_local": s.is_local,
        "agent_type": s.agent_type, "agent_status": s.agent_status,
        "ssh_port": s.ssh_port, "ssh_user": s.ssh_user,
        "tags": s.tags or [], "remark": s.remark,
        "last_seen": str(s.last_seen) if s.last_seen else None,
    } for s in servers]


@router.post("/servers", status_code=201)
def create_server(payload: ServerCreate, db: Session = Depends(get_session)):
    srv = Server(**payload.model_dump(exclude_unset=True))
    db.add(srv)
    db.commit()
    db.refresh(srv)
    return {"id": str(srv.id), "name": srv.name, "host": srv.host}


@router.get("/servers/{server_id}")
def get_server(server_id: str, db: Session = Depends(get_session)):
    srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
    if not srv:
        raise HTTPException(404, "Server not found")
    return {"id": str(srv.id), "name": srv.name, "host": srv.host, "status": srv.status or "unknown"}


@router.put("/servers/{server_id}")
def update_server(server_id: str, payload: ServerCreate, db: Session = Depends(get_session)):
    srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
    if not srv:
        raise HTTPException(404, "Server not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(srv, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/servers/{server_id}")
def delete_server(server_id: str, db: Session = Depends(get_session)):
    srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
    if not srv:
        raise HTTPException(404, "Server not found")
    db.delete(srv)
    db.commit()
    return {"ok": True}


@router.post("/server/local/rebuild")
def rebuild_local_server(db: Session = Depends(get_session)):
    existing = db.query(Server).filter(Server.agent_type == "local").first()
    if existing:
        return {"ok": True, "id": str(existing.id)}
    srv = Server(name=LOCAL_SERVER_NAME, host=LOCAL_HOST, agent_type="local",
                 is_local=True, ssh_user="root", ssh_key=None,
                 agent_status="running", agent_port=19100, agent_token=LOCAL_AGENT_TOKEN)
    db.add(srv)
    db.commit()
    db.refresh(srv)
    return {"ok": True, "id": str(srv.id)}


@router.post("/test-ssh")
def test_ssh(payload: SSHTestRequest):
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=payload.host, port=payload.port or 22,
                       username=payload.user or "root", password=payload.password,
                       timeout=5, banner_timeout=5)
        return {"ok": True, "message": "SSH connected"}
    except Exception as e:
        return {"ok": False, "message": str(e)}
    finally:
        try: client.close()
        except: pass


@router.post("/servers/{server_id}/ssh-test")
def test_server_ssh(server_id: str, payload: SSHTestRequest, db: Session = Depends(get_session)):
    srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
    if not srv:
        raise HTTPException(404, "Server not found")
    host = payload.host or srv.host
    port = payload.port or srv.ssh_port or 22
    user = payload.user or srv.ssh_user or "root"
    password = payload.password
    if not password and srv.ssh_key and srv.ssh_key.startswith("__password__"):
        password = srv.ssh_key[12:]
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=host, port=port, username=user, password=password, timeout=5)
        return {"ok": True, "message": f"Connected {user}@{host}:{port}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}
    finally:
        try: client.close()
        except: pass
