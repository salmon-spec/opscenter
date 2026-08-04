"""Monitor routes — agent metrics, health check, stats"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
import uuid

from app.database import get_session
from app.models import Server, Service, MetricHistory

router = APIRouter(tags=["monitor"])


@router.get("/monitor/{server_id}")
def get_monitor(server_id: str, db: Session = Depends(get_session)):
    """Get live metrics for a server (via Agent proxy)."""
    srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
    if not srv:
        raise HTTPException(404, "Server not found")

    # For local server, try Agent directly
    try:
        import requests
        host = "127.0.0.1" if srv.agent_type == "local" else srv.host
        port = srv.agent_port or 19100
        token = srv.agent_token or ""
        url = f"http://{host}:{port}/api/metrics"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        if resp.status_code == 200:
            metrics = resp.json()
            return {"server_id": server_id, "metrics": metrics, "connected": True}
    except Exception:
        pass

    return {"server_id": server_id, "metrics": {}, "connected": False}


@router.get("/monitor/{server_id}/history")
def get_history(server_id: str, metric: str = "cpu", hours: int = 24, db: Session = Depends(get_session)):
    """Get metric history for a server."""
    from datetime import datetime, timedelta
    since = datetime.utcnow() - timedelta(hours=hours)
    records = db.query(MetricHistory).filter(
        MetricHistory.server_id == uuid.UUID(server_id),
        MetricHistory.metric == metric,
        MetricHistory.timestamp >= since,
    ).order_by(MetricHistory.timestamp.asc()).all()
    return {
        "server_id": server_id,
        "metric": metric,
        "values": [{"timestamp": r.timestamp.isoformat(), "value": r.value} for r in records],
    }


@router.get("/stats")
def get_stats(db: Session = Depends(get_session)):
    """Get overview statistics."""
    servers = db.query(Server).filter(Server.enabled == True).count()
    services = db.query(Service).filter(Service.hidden != True).count()
    containers = db.query(Service).filter(Service.container_name != None).count()
    return {"servers": servers, "services": services, "containers": containers}
