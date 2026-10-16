"""Extension module registry and the first built-in OSS browser module.

The registry is intentionally small. A module stores its manifest and
lifecycle state only; optional capability handlers stay inside the backend.
Front-end modules are sandboxed iframes and call these APIs through the host
message bridge, so they never receive the operator token.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from pydantic import ValidationError

from app.auth import require_operator
from app.credential_crypto import decrypt_secret, encrypt_secret
from app.database import get_db
from app.models import ExtensionModule
from app.services.aliyun_oss import OssModuleError, aliyun_oss
from app.services.command_code import CommandCodeUsageError, command_code

router = APIRouter(prefix="/api/v2/modules", tags=["extension-modules"])

MODULE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")

BUILTIN_MODULES = {
    "oss-browser": {
        "name": "OSS 文件浏览",
        "version": "1.2.2",
        "description": "只读浏览阿里云 OSS 白名单 Bucket 与前缀内的文件",
        "icon": "▤",
        "entry_url": "/v3/extension-modules/oss-browser/index.html?v=1.2.2",
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
    "command-code-usage": {
        "name": "模型用量",
        "version": "1.0.2",
        "description": "只读监控 Command Code / GOAT 的套餐额度、窗口限速和账期用量",
        "icon": "CC",
        "entry_url": "/v3/extension-modules/command-code-usage/index.html?v=1.0.2",
        "runtime": "iframe",
        "isolation": "sandboxed-iframe",
        "capabilities": ["usage:read", "billing:read"],
        "permissions": ["command-code:read"],
        "manifest": {
            "api_prefixes": ["/modules/command-code-usage/"],
            "sandbox": ["allow-scripts"],
            "readonly": True,
        },
        "sort_order": 20,
    },
    "electricity": {
        "name": "电费监测",
        "version": "0.1.0",
        "description": "查看国网电费余额、账单与逐日用电",
        "icon": "⚡",
        "entry_url": "/modules/electricity/ui",
        "runtime": "iframe",
        "isolation": "sandboxed-iframe",
        "capabilities": ["summary:read", "findings:read", "electricity:read", "electricity:refresh"],
        "permissions": ["electricity:read", "electricity:refresh"],
        "manifest": {
            "api_prefixes": ["/modules/electricity/api/v1/"],
            "sandbox": ["allow-scripts"],
            "readonly": False,
        },
        "sort_order": 30,
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


class OssCredentialUpdate(BaseModel):
    access_key_id: str = Field(..., min_length=3, max_length=256)
    access_key_secret: str = Field(..., min_length=8, max_length=512)
    endpoint: str = Field("", max_length=500)

    @field_validator("access_key_id", "access_key_secret", "endpoint")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("endpoint")
    @classmethod
    def _valid_endpoint(cls, value: str) -> str:
        if not value:
            return ""
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Endpoint 必须是 HTTP(S) 地址")
        if parsed.username or parsed.password:
            raise ValueError("Endpoint 不能包含账号密码")
        return value.rstrip("/")


class CommandCodeCredentialUpdate(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=1024)
    api_base: str = Field("https://api.commandcode.ai", max_length=500)
    org_id: str = Field("", max_length=256)
    refresh_interval_seconds: int = Field(60, ge=15, le=3600)
    timeout_seconds: float = Field(12.0, ge=3.0, le=60.0)

    @field_validator("api_key", "org_id")
    @classmethod
    def _strip_secret(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_base")
    @classmethod
    def _valid_api_base(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("API Base 必须是 HTTP(S) 地址")
        if parsed.username or parsed.password:
            raise ValueError("API Base 不能包含账号密码")
        return value


def seed_extension_modules() -> None:
    """Insert or refresh built-in module manifests without touching lifecycle."""
    with get_db() as db:
        changed = False
        for key, item in BUILTIN_MODULES.items():
            row = db.query(ExtensionModule).filter(ExtensionModule.key == key).first()
            if row is None:
                row = ExtensionModule(
                    key=key,
                    running=key == "electricity",
                    status="running" if key == "electricity" else "stopped",
                )
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


def _decrypt_module_config(row: ExtensionModule) -> dict | None:
    if not row.config_ciphertext:
        return None
    try:
        value = json.loads(decrypt_secret(row.config_ciphertext))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="模块配置无法解密，请检查 CREDENTIAL_KEY") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=500, detail="模块配置格式不合法")
    return value


def _apply_oss_config(row: ExtensionModule) -> None:
    aliyun_oss.configure(_decrypt_module_config(row))


def _oss_config_response(row: ExtensionModule) -> dict:
    result = aliyun_oss.config_summary()
    result["updated_at"] = _iso(row.config_updated_at)
    return result


def _apply_command_code_config(row: ExtensionModule) -> None:
    command_code.configure(_decrypt_module_config(row))


def _command_code_config_response(row: ExtensionModule) -> dict:
    result = command_code.config_summary()
    result["updated_at"] = _iso(row.config_updated_at)
    return result


def _parse_module_config(module_key: str, data: dict):
    model = OssCredentialUpdate if module_key == "oss-browser" else CommandCodeCredentialUpdate
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors()
        message = errors[0].get("msg", "模块配置不合法") if errors else "模块配置不合法"
        raise HTTPException(status_code=422, detail=str(message)) from exc


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


def _record_command_code_health(row: ExtensionModule, result: dict) -> dict:
    available = bool(result.get("available"))
    partial_error = str(result.get("error") or "").strip()
    row.last_checked_at = datetime.utcnow()
    if available and not partial_error:
        row.status = "running"
        row.last_error = ""
    else:
        row.status = "degraded" if row.running else "stopped"
        row.last_error = str(partial_error or "模块健康检查失败")[:500]
    return result


def _prepare_command_code_sampling(row: ExtensionModule) -> bool:
    """Apply the effective module/environment config before a background sample."""
    if not row.running:
        return False
    _apply_command_code_config(row)
    return command_code.configured()


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
            _apply_oss_config(row)
            result = _record_health(row, aliyun_oss.status(force=True))
        elif module_key == "command-code-usage":
            _apply_command_code_config(row)
            result = _record_command_code_health(row, command_code.status(force=True))
        elif module_key == "electricity":
            try:
                response = requests.get("http://module-electricity:8080/health/ready", timeout=3)
                available = response.status_code == 200
                error = "" if available else f"健康探针返回 HTTP {response.status_code}"
            except requests.RequestException as exc:
                available = False
                error = str(exc)
            result = {
                "available": available,
                "configured": True,
                "error": error,
                "checked_at": datetime.utcnow().isoformat(),
            }
            _record_health(row, result)
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


@router.get("/{module_key}/config")
def get_oss_config(module_key: str, user: object = Depends(require_operator)):
    with get_db() as db:
        row = _get_module(db, module_key)
        if module_key == "oss-browser":
            _apply_oss_config(row)
            return _oss_config_response(row)
        if module_key == "command-code-usage":
            _apply_command_code_config(row)
            return _command_code_config_response(row)
        raise HTTPException(status_code=404, detail="该模块没有配置接口")


@router.post("/{module_key}/config")
def save_oss_config(
    module_key: str,
    data: dict,
    user: object = Depends(require_operator),
):
    if module_key not in {"oss-browser", "command-code-usage"}:
        raise HTTPException(status_code=404, detail="该模块没有配置接口")
    parsed = _parse_module_config(module_key, data)
    with get_db() as db:
        row = _get_module(db, module_key)
        if module_key == "oss-browser":
            try:
                discovered = aliyun_oss.discover_buckets(
                    parsed.access_key_id, parsed.access_key_secret, parsed.endpoint,
                )
            except OssModuleError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            previous = _decrypt_module_config(row) or {}
            buckets = discovered["buckets"]
            default_bucket = str(previous.get("default_bucket") or "").strip()
            config = {
                "access_key_id": parsed.access_key_id,
                "access_key_secret": parsed.access_key_secret,
                "endpoint": discovered["endpoint"],
                "buckets": buckets,
                "bucket_endpoints": discovered["bucket_endpoints"],
                "default_bucket": default_bucket if default_bucket in buckets else buckets[0],
                "bucket_prefixes": {
                    bucket: prefix
                    for bucket, prefix in (previous.get("bucket_prefixes") or {}).items()
                    if bucket in buckets and isinstance(prefix, str)
                },
            }
        else:
            config = parsed.model_dump()
            try:
                command_code.validate_config(config)
            except CommandCodeUsageError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            row.config_ciphertext = encrypt_secret(json.dumps(config, ensure_ascii=False))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        row.config_updated_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        if module_key == "oss-browser":
            aliyun_oss.configure(config)
            return _oss_config_response(row)
        command_code.configure(config)
        return _command_code_config_response(row)


@router.post("/{module_key}/config/reset")
def reset_oss_config(module_key: str, user: object = Depends(require_operator)):
    with get_db() as db:
        row = _get_module(db, module_key)
        if module_key == "oss-browser":
            service = aliyun_oss
            response = _oss_config_response
        elif module_key == "command-code-usage":
            service = command_code
            response = _command_code_config_response
        else:
            raise HTTPException(status_code=404, detail="该模块没有配置接口")
        row.config_ciphertext = None
        row.config_updated_at = None
        db.commit()
        db.refresh(row)
        service.configure(None)
        return response(row)




def _oss_guard(row: ExtensionModule):
    _apply_oss_config(row)
    if not aliyun_oss.configured():
        raise HTTPException(status_code=503, detail="OSS 凭据或存储桶未配置")


@router.get("/{module_key}/status")
def oss_status(
    module_key: str,
    force: bool = Query(False),
    bucket: Optional[str] = Query(None),
    user: object = Depends(require_operator),
):
    with get_db() as db:
        row = _require_running(db, module_key)
        if module_key == "command-code-usage":
            _apply_command_code_config(row)
            result = _record_command_code_health(row, command_code.status(
                force=force,
                min_captured_at=row.config_updated_at if row.config_ciphertext else None,
            ))
        elif module_key == "oss-browser":
            _oss_guard(row)
            try:
                result = _record_health(row, aliyun_oss.status(force=force, bucket=bucket))
            except OssModuleError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            raise HTTPException(status_code=404, detail="该模块没有状态接口")
        db.commit()
        return {"module": _module_dict(row, include_manifest=False), "health": result}


@router.get("/{module_key}/objects")
def oss_objects(
    module_key: str,
    prefix: Optional[str] = Query(None),
    marker: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    bucket: Optional[str] = Query(None),
    user: object = Depends(require_operator),
):
    with get_db() as db:
        row = _require_running(db, module_key)
        if module_key != "oss-browser":
            raise HTTPException(status_code=404, detail="该模块没有对象浏览接口")
        _oss_guard(row)
        try:
            return aliyun_oss.list_objects(prefix, marker, limit, bucket)
        except OssModuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{module_key}/summary")
def oss_summary(
    module_key: str,
    prefix: Optional[str] = Query(None),
    bucket: Optional[str] = Query(None),
    force: bool = Query(False),
    user: object = Depends(require_operator),
):
    with get_db() as db:
        row = _require_running(db, module_key)
        if module_key != "oss-browser":
            raise HTTPException(status_code=404, detail="该模块没有目录统计接口")
        _oss_guard(row)
        try:
            return aliyun_oss.summary(prefix, bucket, force)
        except OssModuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{module_key}/preview")
def oss_preview(
    module_key: str,
    key: str = Query(...),
    bucket: Optional[str] = Query(None),
    user: object = Depends(require_operator),
):
    with get_db() as db:
        row = _require_running(db, module_key)
        if module_key != "oss-browser":
            raise HTTPException(status_code=404, detail="该模块没有预览接口")
        _oss_guard(row)
        try:
            return aliyun_oss.preview_object(key, bucket)
        except OssModuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{module_key}/download")
def oss_download(
    module_key: str,
    key: str = Query(...),
    bucket: Optional[str] = Query(None),
    user: object = Depends(require_operator),
):
    with get_db() as db:
        row = _require_running(db, module_key)
        if module_key != "oss-browser":
            raise HTTPException(status_code=404, detail="该模块没有下载接口")
        _oss_guard(row)
        try:
            chunks, filename, mime = aliyun_oss.open_download(key, bucket)
        except OssModuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StreamingResponse(
            chunks,
            media_type=mime,
            headers={"Content-Disposition": aliyun_oss.content_disposition(filename)},
        )


async def command_code_usage_loop() -> None:
    """Sample Command Code cumulative usage every minute while the module is running."""
    while True:
        try:
            with get_db() as db:
                row = (
                    db.query(ExtensionModule)
                    .filter(ExtensionModule.key == "command-code-usage")
                    .first()
                )
                should_sample = bool(row and _prepare_command_code_sampling(row))
            if should_sample:
                result = await asyncio.to_thread(command_code.status, force=True)
                with get_db() as db:
                    row = (
                        db.query(ExtensionModule)
                        .filter(ExtensionModule.key == "command-code-usage")
                        .first()
                    )
                    if row:
                        _record_command_code_health(row, result)
                        db.commit()
        except Exception as exc:
            print(f"[modules] Command Code sampling skipped: {exc}", flush=True)
        await asyncio.sleep(60)
