# -*- coding: utf-8 -*-
"""开放 API 密钥管理（v3.29, T2）。

为开放 API（/api/v2/screen/summary、/api/v2/topology、/api/v2/monitor/history 等）
提供 Bearer 密钥鉴权：
- 数据库仅存 SHA-256 哈希，明文只在创建时返回一次；
- scope 区分 read / write，write 可调用全部只读接口；
- 免登录工作台（OPS_AUTH_ENABLED=false）不带令牌时放行，带令牌则必须校验。
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.database import get_db
from app.models import ApiKey

router = APIRouter(prefix="/api/v2", tags=["api-keys"])

_VALID_SCOPES = ("read", "write")
_SCOPE_PREFIX = {"read": "rt", "write": "wt"}

# Bearer 提取器：auto_error=False 使未带令牌时返回 None，由依赖自行判断
_bearer_scheme = HTTPBearer(auto_error=False)


class ApiKeyCreate(BaseModel):
    """创建密钥请求体。"""
    name: str = Field(..., min_length=1, max_length=50, description="密钥名称")
    scope: str = Field("read", description="权限范围：read / write")


def hash_api_key(plain: str) -> str:
    """SHA-256 哈希，数据库不落明文。"""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def generate_api_key(name: str, scope: str = "read"):
    """生成密钥并落库，返回 (明文, ORM 对象)。明文仅此一次对外返回。"""
    scope = scope if scope in _VALID_SCOPES else "read"
    scope_part = _SCOPE_PREFIX.get(scope, "rt")
    plain = f"oc_{scope_part}_{secrets.token_hex(16)}"
    row = ApiKey(
        name=name,
        key_hash=hash_api_key(plain),
        prefix=plain[:10],
        scope=scope,
        enabled=True,
    )
    with get_db() as db:
        db.add(row)
        db.commit()
        db.refresh(row)
    return plain, row


def verify_api_key(plain: str) -> Optional[ApiKey]:
    """校验密钥，并将高频调用产生的 last_used_at 写入限制为每分钟一次。"""
    key_hash = hash_api_key(plain)
    with get_db() as db:
        row = db.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()
        if not row or not row.enabled:
            return None
        now = datetime.utcnow()
        if row.last_used_at is None or row.last_used_at < now - timedelta(minutes=1):
            row.last_used_at = now
            db.commit()
        return row


def require_api_key(scope: str = "read", *, required: bool = False):
    """FastAPI 依赖工厂：开放 API 密钥鉴权。

    - 请求头带 Authorization: Bearer <key> → 必须校验通过（无效/停用 401，scope 不足 403）
    - 不带令牌 → 放行（免登录工作台自身使用；OPS_AUTH_ENABLED=true 时可在此收紧为强制）
    """
    def _dependency(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    ) -> Optional[ApiKey]:
        if credentials is None:
            if required:
                raise HTTPException(
                    status_code=401,
                    detail="该接口必须使用 API 密钥",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            # 兼容既有工作台开放接口；AI context 接口始终传 required=True。
            return None
        row = verify_api_key(credentials.credentials)
        if row is None:
            raise HTTPException(status_code=401, detail="无效或已停用的 API 密钥")
        if scope == "write" and row.scope != "write":
            raise HTTPException(status_code=403, detail="该密钥无 write 权限")
        return row

    return _dependency


@router.get("/keys", dependencies=[Depends(get_current_user)])
def list_api_keys():
    """密钥列表（管理员）。绝不返回 key_hash / 明文。"""
    with get_db() as db:
        rows = db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
        return [
            {
                "id": str(r.id),
                "name": r.name,
                "prefix": r.prefix,
                "scope": r.scope,
                "enabled": r.enabled,
                "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


@router.post("/keys", status_code=201, dependencies=[Depends(get_current_user)])
def create_api_key(payload: ApiKeyCreate):
    """创建密钥（管理员）。明文仅在此响应中返回一次。"""
    if payload.scope not in _VALID_SCOPES:
        raise HTTPException(status_code=400, detail="scope 仅支持 read / write")
    plain, row = generate_api_key(payload.name, payload.scope)
    return {
        "id": str(row.id),
        "name": row.name,
        "prefix": row.prefix,
        "scope": row.scope,
        "enabled": row.enabled,
        "api_key": plain,
    }


@router.delete("/keys/{key_id}", dependencies=[Depends(get_current_user)])
def delete_api_key(key_id: str):
    """删除密钥（管理员）。"""
    try:
        uid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="密钥不存在")
    with get_db() as db:
        row = db.query(ApiKey).filter(ApiKey.id == uid).first()
        if not row:
            raise HTTPException(status_code=404, detail="密钥不存在")
        db.delete(row)
        db.commit()
    return {"ok": True}
