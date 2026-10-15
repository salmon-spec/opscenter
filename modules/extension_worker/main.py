"""Run one existing extension API in its own container."""

import os
import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text

database_url_file = Path(os.environ["EXTENSION_DATABASE_URL_FILE"])
os.environ["DATABASE_URL"] = database_url_file.read_text(encoding="utf-8").strip().replace(
    "postgresql://", "postgresql+psycopg://", 1,
)
if not os.environ["DATABASE_URL"]:
    raise RuntimeError("Extension module database URL is empty")

from app.auth import AUTH_ENABLED, PUBLIC_KEY_FILE
from app.database import engine
from app.extension_modules import command_code_usage_loop, router

KEY = os.environ["EXTENSION_MODULE_KEY"]
if KEY not in {"oss-browser", "command-code-usage"}:
    raise RuntimeError("Unknown extension module")
if not AUTH_ENABLED or not PUBLIC_KEY_FILE:
    raise RuntimeError("Extension module JWT verification is required")

ALLOWED = {
    "oss-browser": {
        "GET": {"status", "config", "objects", "summary", "preview", "download"},
        "POST": {"config", "config/reset", "health"},
    },
    "command-code-usage": {
        "GET": {"status", "config"},
        "POST": {"config", "config/reset", "health"},
    },
}

@asynccontextmanager
async def lifespan(_app):
    sampler = asyncio.create_task(command_code_usage_loop()) if KEY == "command-code-usage" else None
    try:
        yield
    finally:
        if sampler:
            sampler.cancel()
            with suppress(asyncio.CancelledError):
                await sampler


app = FastAPI(title=f"OpsCenter extension: {KEY}", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def restrict_extension(request, call_next):
    path = request.url.path
    if path not in {"/health/live", "/health/ready"}:
        prefix = f"/api/v2/modules/{KEY}/"
        operation = path.removeprefix(prefix) if path.startswith(prefix) else ""
        if operation not in ALLOWED[KEY].get(request.method, set()):
            return JSONResponse({"detail": "Not found"}, status_code=404)
    return await call_next(request)


@app.get("/health/live")
def live():
    return {"status": "ok", "module": KEY}


@app.get("/health/ready")
def ready():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "Module database unavailable") from None
    return {"status": "ok", "module": KEY}
