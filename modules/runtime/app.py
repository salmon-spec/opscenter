from __future__ import annotations

import inspect
import logging
import asyncio
import os
import hmac
from pathlib import Path
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse

from .contract import FindingsResponse, ModuleInfo, ModuleSummary
from .auth import SessionMiddleware

logger = logging.getLogger("opscenter.module")


def require_internal_service(token: str = Header(default="", alias="X-OpsCenter-Module-Token")):
    path = os.getenv("OPS_MODULE_INTERNAL_TOKEN_FILE", "")
    try:
        expected = Path(path).read_text(encoding="utf-8").strip() if path else ""
    except OSError:
        expected = ""
    if not expected or not token or not hmac.compare_digest(expected, token):
        raise HTTPException(401, "未授权的模块间请求")


def create_module_app(
    key: str,
    version: str,
    summary_provider: Callable[[], Any],
    findings_provider: Callable[[], Any],
    capabilities: list[str] | None = None,
    readiness_provider: Callable[[], Any] | None = None,
) -> FastAPI:
    app = FastAPI(title=f"OpsCenter module: {key}", version=version)
    if os.getenv("MODULE_AUTH_REQUIRED", "false").lower() in {"1", "true", "yes"}:
        app.add_middleware(SessionMiddleware)
    info = ModuleInfo(key=key, version=version, capabilities=capabilities or [])

    async def collect(provider: Callable[[], Any], response_model):
        try:
            if inspect.iscoroutinefunction(provider):
                value = await provider()
            else:
                value = await asyncio.to_thread(provider)
            if inspect.isawaitable(value):
                value = await value
            return response_model.model_validate(value)
        except Exception as exc:
            logger.warning("module=%s provider_failed=%s", key, type(exc).__name__)
            now = datetime.now(timezone.utc)
            if response_model is ModuleSummary:
                return ModuleSummary(
                    status="unavailable", collected_at=now, data={}, error="数据源暂不可用",
                )
            return FindingsResponse(
                status="unavailable", collected_at=now, items=[], error="告警源暂不可用",
            )

    @app.get("/health/live")
    def live():
        return {"status": "ok", "module": key}

    @app.get("/health/ready")
    def ready():
        if readiness_provider:
            try:
                readiness_provider()
            except Exception:
                raise HTTPException(status_code=503, detail="模块依赖暂不可用") from None
        return {"status": "ok", "module": key, "version": version}

    @app.get("/api/v1/module", response_model=ModuleInfo)
    def module_info():
        return info

    @app.get("/api/v1/version")
    def module_version():
        return {"key": key, "version": version}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics():
        return f'opscenter_module_info{{module_key="{key}",version="{version}"}} 1\n'

    @app.get("/api/v1/summary", response_model=ModuleSummary)
    async def summary():
        return await collect(summary_provider, ModuleSummary)

    @app.get("/api/v1/findings", response_model=FindingsResponse)
    async def findings():
        return await collect(findings_provider, FindingsResponse)

    @app.get("/api/v1/internal/summary", response_model=ModuleSummary, dependencies=[Depends(require_internal_service)])
    async def internal_summary():
        return await collect(summary_provider, ModuleSummary)

    @app.get("/api/v1/internal/findings", response_model=FindingsResponse, dependencies=[Depends(require_internal_service)])
    async def internal_findings():
        return await collect(findings_provider, FindingsResponse)

    return app
