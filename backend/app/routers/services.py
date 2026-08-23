"""Service management routes"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import uuid

from app.database import get_session
from app.models import Service, Server

router = APIRouter(tags=["services"])


class ServiceCreate(BaseModel):
    name: str
    url: str = ""
    category: str = "应用服务"
    icon: str = "server"
    description: str = ""
    container_name: Optional[str] = None
    image: Optional[str] = None
    ports: Optional[str] = None
    hidden: bool = False
    account: Optional[str] = None
    password: Optional[str] = None
    pinned: bool = False


class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    hidden: Optional[bool] = None
    account: Optional[str] = None
    password: Optional[str] = None


@router.get("/services")
def list_services(server_id: Optional[str] = None, db: Session = Depends(get_session)):
    q = db.query(Service)
    if server_id:
        q = q.filter(Service.server_id == uuid.UUID(server_id))
    q = q.filter(Service.url != None, Service.url != "", Service.hidden != True)
    services = q.order_by(Service.category, Service.name).all()
    return [{
        "id": str(s.id), "server_id": str(s.server_id),
        "name": s.name, "url": s.url, "category": s.category,
        "icon": s.icon, "description": s.description,
        "source": s.source, "status": s.status, "pinned": s.pinned,
        "container_name": s.container_name, "image": s.image, "ports": s.ports,
    } for s in services]


@router.get("/services/all")
def list_all_services(server_id: Optional[str] = None, db: Session = Depends(get_session)):
    q = db.query(Service)
    if server_id:
        q = q.filter(Service.server_id == uuid.UUID(server_id))
    services = q.order_by(Service.category, Service.name).all()
    return [{
        "id": str(s.id), "server_id": str(s.server_id),
        "name": s.name, "url": s.url, "category": s.category,
        "icon": s.icon, "description": s.description,
        "source": s.source, "status": s.status, "hidden": s.hidden or False,
    } for s in services]


@router.post("/services", status_code=201)
def create_service(server_id: str = Query(...), payload: ServiceCreate = None, db: Session = Depends(get_session)):
    srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
    if not srv:
        raise HTTPException(404, "Server not found")
    svc = Service(
        server_id=srv.id,
        name=payload.name, url=payload.url,
        category=payload.category, icon=payload.icon,
        description=payload.description,
        container_name=payload.container_name,
        image=payload.image, ports=payload.ports,
        hidden=payload.hidden,
        account=payload.account, password=payload.password,
        pinned=payload.pinned,
        source="manual",
    )
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return {"id": str(svc.id), "name": svc.name}


@router.put("/services/{service_id}")
def update_service(service_id: str, payload: ServiceUpdate, db: Session = Depends(get_session)):
    svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
    if not svc:
        raise HTTPException(404, "Service not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(svc, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/services/{service_id}")
def delete_service(service_id: str, db: Session = Depends(get_session)):
    svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
    if not svc:
        raise HTTPException(404, "Service not found")
    db.delete(svc)
    db.commit()
    return {"ok": True}


@router.patch("/services/{service_id}/pin")
def toggle_pin(service_id: str, db: Session = Depends(get_session)):
    svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
    if not svc:
        raise HTTPException(404, "Service not found")
    svc.pinned = not svc.pinned
    db.commit()
    return {"id": str(svc.id), "pinned": svc.pinned}
