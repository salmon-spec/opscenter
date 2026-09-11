# -*- coding: utf-8 -*-
"""OpsCenter 认证模块（v3.23.0 新增，v3.24.0 增加免登录开关）
提供 JWT 令牌签发/校验和 bcrypt 密码哈希。
资源管理相关路由通过 Depends(get_current_user) 强制鉴权，
其他路由（服务导航/监控/终端）保持公开。

v3.24.0：新增环境变量开关 OPS_AUTH_ENABLED（默认 false）。
- false（默认）：免登录模式，get_current_user 返回虚拟管理员，所有管理端点直接放行；
- true：恢复原 JWT 校验（无令牌/令牌无效 → 401）。
用于现阶段去除管理员密码登录，后续需要时改回 true 即可一键恢复。
"""
import os
import secrets as _secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field


# === 配置 ===
SECRET_KEY = os.getenv("OPS_JWT_SECRET", "").strip()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
# 免登录开关：OPS_AUTH_ENABLED=true 时恢复 JWT 鉴权；缺省/false 时免登录（v3.24.0）
AUTH_ENABLED = os.getenv("OPS_AUTH_ENABLED", "false").strip().lower() in ("1", "true", "yes")
ADMIN_USER = os.getenv("OPS_ADMIN_USER", "admin").strip() or "admin"
ADMIN_PASSWORD = os.getenv("OPS_ADMIN_PASSWORD", "")
# v5.0.0 运维共享令牌：非空时 require_operator 保护的端点必须携带 Bearer <OPERATOR_TOKEN>
OPERATOR_TOKEN = os.getenv("OPERATOR_TOKEN", "").strip()

# Bearer 令牌提取器：auto_error=False 使未带令牌时返回 None 而非直接 403，
# 由 get_current_user 自行返回 401，便于前端识别并弹出登录框。
bearer_scheme = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api/v2/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=1000)


# === 密码哈希 ===
def hash_password(password: str) -> str:
    """bcrypt 哈希密码，返回可存储的字符串。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码与哈希是否匹配。"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# === JWT 令牌 ===
def create_access_token(user_id: int, username: str) -> str:
    """生成 JWT 访问令牌，有效期 24 小时。"""
    if not SECRET_KEY:
        raise RuntimeError("OPS_JWT_SECRET 未配置，不能签发登录令牌")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """解码并校验令牌，无效或过期返回 None。"""
    if not SECRET_KEY:
        return None
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


@router.post("/login")
def login(payload: LoginRequest):
    if not AUTH_ENABLED:
        if OPERATOR_TOKEN and _secrets.compare_digest(payload.password, OPERATOR_TOKEN):
            return {
                "access_token": OPERATOR_TOKEN, "token_type": "bearer",
                "expires_in": None,
                "user": {"id": 1, "username": ADMIN_USER, "display_name": "管理员", "role": "admin"},
            }
        raise HTTPException(status_code=409, detail="当前未启用账号登录")
    if not SECRET_KEY or not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="登录密钥或管理员密码未配置")
    valid_user = _secrets.compare_digest(payload.username, ADMIN_USER)
    valid_password = _secrets.compare_digest(payload.password, ADMIN_PASSWORD)
    if not (valid_user and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {
        "access_token": create_access_token(1, ADMIN_USER),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        "user": {"id": 1, "username": ADMIN_USER, "display_name": "管理员", "role": "admin"},
    }


# === FastAPI 依赖 ===
def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> object:
    """强制鉴权依赖：用于资源管理相关路由。

    兼容模式（OPS_AUTH_ENABLED=false）：配置 OPERATOR_TOKEN 后仍要求该令牌；
    两者都未配置时返回虚拟管理员。
    鉴权模式（OPS_AUTH_ENABLED=true）：无令牌或令牌无效 → 401（前端弹出登录框）。
    """
    if not AUTH_ENABLED:
        token = credentials.credentials if credentials else None
        if OPERATOR_TOKEN and not check_operator_token(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="缺少或无效的运维令牌",
                headers={"WWW-Authenticate": "Bearer"},
            )
        from types import SimpleNamespace
        return SimpleNamespace(
            id=1,
            username="admin",
            display_name="管理员",
            role="admin",
            is_active=True,
        )
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或缺少认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="无效的认证令牌")

    username = str(payload.get("username") or "")
    if str(user_id) != "1" or not _secrets.compare_digest(username, ADMIN_USER):
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    return _virtual_admin()


# === v5.0.0 运维共享令牌依赖（数据服务/高危端点） ===
def _virtual_admin():
    from types import SimpleNamespace
    return SimpleNamespace(id=1, username="admin", display_name="管理员", role="admin", is_active=True)


def check_operator_token(token: Optional[str]) -> bool:
    """OPERATOR_TOKEN 未配置时放行；配置后要求常量时间比对。"""
    if not OPERATOR_TOKEN:
        return True
    if not token:
        return False
    return _secrets.compare_digest(str(token), OPERATOR_TOKEN)


def check_access_token(token: Optional[str]) -> bool:
    """Validate the token used by transports that cannot use dependencies."""
    if AUTH_ENABLED:
        payload = decode_token(token or "")
        return bool(
            payload
            and str(payload.get("sub") or "") == "1"
            and _secrets.compare_digest(str(payload.get("username") or ""), ADMIN_USER)
        )
    return check_operator_token(token)


def require_operator(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> object:
    """v5.0.0 新增端点/高危端点的统一鉴权依赖。

    - OPS_AUTH_ENABLED=true：与 get_current_user 一致，要求有效 JWT（401）；
    - 否则 OPERATOR_TOKEN 已配置：要求 Bearer 携带正确令牌（401）；
    - 两者都未启用：沿用免登录虚拟管理员（现状兼容）。
    """
    if AUTH_ENABLED:
        return get_current_user(credentials)
    token = credentials.credentials if credentials else None
    if not check_operator_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少或无效的运维令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _virtual_admin()


class MutationAuthMiddleware:
    """Apply the configured operator gate to every mutating v2 API request.

    Route-level dependencies remain useful documentation, while this shared
    boundary prevents newly-added POST/PUT/PATCH/DELETE handlers from being
    accidentally left unguarded. Compatibility is unchanged when both auth
    mechanisms are explicitly disabled.
    """

    _METHODS = {"POST", "PUT", "PATCH", "DELETE"}
    _PUBLIC = {"/api/v2/auth/login"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") == "http"
            and scope.get("method", "").upper() in self._METHODS
            and scope.get("path", "").startswith("/api/v2/")
            and scope.get("path") not in self._PUBLIC
        ):
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            authorization = headers.get(b"authorization", b"").decode("latin-1")
            credentials = None
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() == "bearer" and token:
                credentials = HTTPAuthorizationCredentials(scheme=scheme, credentials=token)
            try:
                require_operator(credentials)
            except HTTPException as exc:
                response = JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                    headers=exc.headers,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
