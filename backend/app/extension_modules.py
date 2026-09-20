"""Extension module registry and the first built-in OSS browser module.

The registry is intentionally small. A module stores its manifest and
lifecycle state only; optional capability handlers stay inside the backend.
Front-end modules are sandboxed iframes and call these APIs through the host
message bridge, so they never receive the operator token.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import require_operator
from app.database import get_db
from app.models import ExtensionModule
from app.services.aliyun_oss import OssModuleError, aliyun_oss

router = APIRouter(prefix="/api/v2/modules", tags=["extension-modules"])

MODULE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")

BUILTIN_MODULES = {
    "oss-browser": {
        "name": "OSS 文件浏览",
        "version": "1.0.0",
        "description": "只读浏览阿里云 OSS 指定 Bucket 与前缀内的文件",
        "icon": "▤",
        "entry_url": "/v3/extension-modules/oss-browser/index.html",
        "runtime": "iframe",
        "isolation": "sandboxed-iframe",
        "capabilities": ["list", "preview", "download"],
        "permissions": ["oss:read"],
        "manifest": {
            "api_prefixes": ["/modules/oss-browser/"],
            "sandbox": ["allow-scripts", "allow-downloads"],
            "readonly": True,
        },
        "sort_order": 10,
    },
}


class ModuleRegistration(BaseModel):
    key: str = Field(..., min_length=2, max_length=63)
    name: str = Field(..., min_length=1, max_length=80)
    entry_url: str = Field(..., min_length=1, max_length=2000)
    version: str = Field("1.0.0", max_length=40)
    description: str = Field("", max_length=500)
    icon: str = Field("◇", max_length=16)
    capabilities: list[str] = Field(default_factory=list, max_length=50)
    permissions: list[str] = Field(default_factory=lambda: ["readonly"], max_length=50)
    manifest: dict = Field(default_factory=dict)
    sort_order: int = Field(100, ge=0, le=10000)

    @field_validator("key")
    @classmethod
    def _valid_key(cls, value: str) -> str:
        value = value.strip().lower()
        if not MODULE_KEY_RE.fullmatch(value):
            raise ValueError("模块 Key 仅支持小写字母、数字和连字符")
        return value

    @field_validator("entry_url")
    @classmethod
    def _valid_entry_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme in {"javascript", "data", "file", "vbscript"}:
            raise ValueError("模块入口协议不受支持")
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            raise ValueError("模块入口仅支持 HTTP(S) 或站内绝对路径")
        if value.startswith("//"):
            raise ValueError("模块入口不允许协议相对地址")
        if not value.startswith("/") and parsed.scheme not in {"http", "https"}:
            raise ValueError("模块入口必须是站内绝对路径或完整 HTTP(S) 地址")
        if parsed.username or parsed.password:
            raise ValueError("模块入口不能包含账号密码")
        return value


def seed_extension_modules() -> None:
    """Insert or refresh built-in module manifests without touching lifecycle."""
    with get_db() as db:
        changed = False
        for key, item in BUILTIN_MODULES.items():
            row = db.query(ExtensionModule).filter(ExtensionModule.key == key).first()
            if row is None:
                row = ExtensionModule(key=key, running=False, status="stopped")
                db.add(row)
                changed = True
            for field, value in item.items():
                if getattr(row, field) != value:
                    setattr(row, field, value)
                    changed = True
            if row.source != "builtin":
                row.source = "builtin"
                changed = True
        if changed:
            db.commit()


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _module_dict(row: ExtensionModule, include_manifest: bool = True) -> dict:
    result = {
        "key": row.key,
        "name": row.name,
        "version": row.version or "",
        "description": row.description or "",
        "icon": row.icon or "◇",
        "entry_url": row.entry_url,
        "runtime": row.runtime or "iframe",
        "isolation": row.isolation or "sandboxed-iframe",
        "source": row.source or "registered",
        "capabilities": row.capabilities or [],
        "permissions": row.permissions or [],
        "sort_order": row.sort_order or 0,
        "running": bool(row.running),
        "status": row.status or "stopped",
        "last_error": row.last_error or "",
        "started_at": _iso(row.started_at),
        "stopped_at": _iso(row.stopped_at),
        "last_checked_at": _iso(row.last_checked_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if include_manifest:
        result["manifest"] = row.manifest or {}
    return result


def _get_module(db, key: str) -> ExtensionModule:
    row = db.query(ExtensionModule).filter(ExtensionModule.key == key).first()
    if row is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    return row


def _require_running(db, key: str) -> ExtensionModule:
    row = _get_module(db, key)
    if not row.running:
        raise HTTPException(status_code=409, detail="模块未启动")
    return row


def _record_health(row: ExtensionModule, result: dict) -> dict:
    available = bool(result.get("available"))
    row.last_checked_at = datetime.utcnow()
    if available:
        row.status = "running"
        row.last_error = ""
    else:
        row.status = "degraded" if row.running else "stopped"
        row.last_error = str(result.get("error") or "模块健康检查失败")[:500]
    return result


@router.get("")
def list_modules(
    running: Optional[bool] = Query(None),
    user: object = Depends(require_operator),
):
    with get_db() as db:
        query = db.query(ExtensionModule)
        if running is not None:
            query = query.filter(ExtensionModule.running == running)
        rows = query.order_by(ExtensionModule.sort_order, ExtensionModule.name).all()
        return {"items": [_module_dict(row) for row in rows]}


@router.post("/register")
def register_module(
    data: ModuleRegistration,
    user: object = Depends(require_operator),
):
    with get_db() as db:
        row = db.query(ExtensionModule).filter(ExtensionModule.key == data.key).first()
        if row is not None and (row.source or "").lower() == "builtin":
            raise HTTPException(status_code=409, detail="内置模块由系统维护，不能覆盖")
        if row is None:
            row = ExtensionModule(
                key=data.key,
                source="registered",
                runtime="iframe",
                isolation="sandboxed-iframe",
                running=False,
                status="stopped",
            )
            db.add(row)
        row.name = data.name.strip()
        row.version = data.version.strip() or "1.0.0"
        row.description = data.description.strip()
        row.icon = data.icon.strip() or "◇"
        row.entry_url = data.entry_url
        row.capabilities = data.capabilities
        row.permissions = data.permissions
        row.manifest = data.manifest
        row.sort_order = data.sort_order
        db.commit()
        db.refresh(row)
        return _module_dict(row)


@router.get("/{module_key}")
def get_module(module_key: str, user: object = Depends(require_operator)):
    with get_db() as db:
        return _module_dict(_get_module(db, module_key))


@router.post("/{module_key}/start")
def start_module(module_key: str, user: object = Depends(require_operator)):
    with get_db() as db:
        row = _get_module(db, module_key)
        row.running = True
        row.status = "running"
        row.last_error = ""
        row.started_at = datetime.utcnow()
        row.stopped_at = None
        db.commit()
        db.refresh(row)
        return _module_dict(row)


@router.post("/{module_key}/stop")
def stop_module(module_key: str, user: object = Depends(require_operator)):
    with get_db() as db:
        row = _get_module(db, module_key)
        row.running = False
        row.status = "stopped"
        row.stopped_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        return _module_dict(row)


@router.post("/{module_key}/health")
def check_module_health(module_key: str, user: object = Depends(require_operator)):
    with get_db() as db:
        row = _get_module(db, module_key)
        if module_key == "oss-browser":
            result = _record_health(row, aliyun_oss.status(force=True))
        else:
            result = {
                "available": bool(row.running),
                "configured": True,
                "error": "" if row.running else "模块未启动",
                "checked_at": datetime.utcnow().isoformat(),
            }
            _record_health(row, result)
        db.commit()
        db.refresh(row)
        return {
            "module": _module_dict(row),
            "health": result,
        }


def _oss_guard():
    if not aliyun_oss.configured():
        raise HTTPException(status_code=503, detail="OSS 凭据或存储桶未配置")


@router.get("/{module_key}/status")
def oss_status(
    module_key: str,
    force: bool = Query(False),
    user: object = Depends(require_operator),
):
    with get_db() as db:
        row = _require_running(db, module_key)
        if module_key != "oss-browser":
            raise HTTPException(status_code=404, detail="该模块没有状态接口")
        _oss_guard()
        result = _record_health(row, aliyun_oss.status(force=force))
        db.commit()
        return {"module": _module_dict(row, include_manifest=False), "health": result}


@router.get("/{module_key}/objects")
def oss_objects(
    module_key: str,
    prefix: Optional[str] = Query(None),
    marker: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    user: object = Depends(require_operator),
):
    with get_db() as db:
        _require_running(db, module_key)
        if module_key != "oss-browser":
            raise HTTPException(status_code=404, detail="该模块没有对象浏览接口")
        _oss_guard()
        try:
            return aliyun_oss.list_objects(prefix, marker, limit)
        except OssModuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{module_key}/preview")
def oss_preview(
    module_key: str,
    key: str = Query(...),
    user: object = Depends(require_operator),
):
    with get_db() as db:
        _require_running(db, module_key)
        if module_key != "oss-browser":
            raise HTTPException(status_code=404, detail="该模块没有预览接口")
        _oss_guard()
        try:
            return aliyun_oss.preview_object(key)
        except OssModuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{module_key}/download")
def oss_download(
    module_key: str,
    key: str = Query(...),
    user: object = Depends(require_operator),
):
    with get_db() as db:
        _require_running(db, module_key)
        if module_key != "oss-browser":
            raise HTTPException(status_code=404, detail="该模块没有下载接口")
        _oss_guard()
        try:
            chunks, filename, mime = aliyun_oss.open_download(key)
        except OssModuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StreamingResponse(
            chunks,
            media_type=mime,
            headers={"Content-Disposition": aliyun_oss.content_disposition(filename)},
        )
