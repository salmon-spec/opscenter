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
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.models import User

# === 配置 ===
SECRET_KEY = os.getenv("OPS_JWT_SECRET", "opscenter-default-secret-change-me-1234567890")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
# 免登录开关：OPS_AUTH_ENABLED=true 时恢复 JWT 鉴权；缺省/false 时免登录（v3.24.0）
AUTH_ENABLED = os.getenv("OPS_AUTH_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# Bearer 令牌提取器：auto_error=False 使未带令牌时返回 None 而非直接 403，
# 由 get_current_user 自行返回 401，便于前端识别并弹出登录框。
bearer_scheme = HTTPBearer(auto_error=False)


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
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# === FastAPI 依赖 ===
def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> User:
    """强制鉴权依赖：用于资源管理相关路由。

    免登录模式（OPS_AUTH_ENABLED=false，v3.24.0 默认）：直接返回虚拟管理员，
    所有管理端点放行，前端无需携带令牌。
    鉴权模式（OPS_AUTH_ENABLED=true）：无令牌或令牌无效 → 401（前端弹出登录框）。
    """
    if not AUTH_ENABLED:
        # 免登录模式：返回虚拟管理员，不落库、不校验
        return User(
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

    # 延迟导入 SessionLocal，避免与 main.py 形成循环导入
    from app.main import SessionLocal
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="用户不存在或已禁用")
        return user
    finally:
        db.close()
