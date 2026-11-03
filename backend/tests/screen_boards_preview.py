"""Local UI acceptance server: real dashboard persistence, synthetic monitor sources.

Run from backend with DATABASE_URL pointing to a dedicated test SQLite file,
OPERATOR_TOKEN=screen-preview-token, then uvicorn tests.screen_boards_preview:app.
Never run against an existing OpsCenter database.
"""
from datetime import datetime, timezone

from fastapi import FastAPI
from app import auth
from app.database import engine
from app.screen_boards import router, ScreenBoard, ScreenPublication, ScreenDefault

if engine.url.get_backend_name() != "sqlite" or "screen-preview" not in (engine.url.database or ""):
    raise RuntimeError("Preview requires a dedicated screen-preview SQLite database")
for model in [ScreenBoard, ScreenPublication, ScreenDefault]:
    model.__table__.create(engine, checkfirst=True)
app = FastAPI()
app.include_router(auth.router)
app.include_router(router)


@app.get("/modules/{path:path}")
def module_fixture(path: str):
    now = datetime.now(timezone.utc).isoformat()
    result = {"status": "ok", "collected_at": now, "stale_after_seconds": 120, "items": []}
    if path == "host-inventory/api/v1/hosts":
        result["items"] = [{"id": "00000000-0000-0000-0000-000000000001", "name": "验收主机（模拟数据）",
                            "host": "192.0.2.1", "status": "online", "last_seen": now,
                            "agent_status": "running", "agent_version": "2.6.4"}]
    result["data"] = {"nodes": {"ready": 3, "total": 3}, "pods": {"running": 20, "total": 21, "failed": 0},
                      "pvcs": {"total": 5, "unbound": 0}, "warning_events": 0,
                      "firing": 0, "pending": 0, "acked": 0}
    return result


@app.get("/api/v2/{path:path}")
def core_fixture(path: str):
    if path == "modules":
        return []
    if path == "agents/version":
        return {"current_version": "2.6.4"}
    if path.startswith("metrics/hosts/"):
        return {"hosts": [], "series": {}}
    return {"items": [], "events": [], "series": {}}
