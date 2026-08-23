# -*- coding: utf-8 -*-
"""OpsCenter 免密 SSO 模块（v3.29, T5）。

OpsCenter 兼任认证中心，提供：
- 登录/登出/会话查询（JWT HttpOnly Cookie：ops_session，24h）
- Caddy forward_auth 校验端点（公网/内网统一登录墙）
- 轻量 OIDC Provider（HS256 + 一次性授权码，仅 PyJWT，无新增依赖）

main.py 接线（两行）：
    from app.sso import router as sso_router
    app.include_router(sso_router)

设计取舍与限制：
- 授权码存内存（进程内 dict + 锁），60s 过期、一次性；多副本部署时不共享，当前单进程满足要求
- id_token 用 HS256（与现有 JWT 同一密钥 OPS_JWT_SECRET），满足内部 OIDC 对接
- OIDC_ISSUER 默认 http://127.0.0.1:9091，公网/内网部署时用环境变量覆盖为实际入口
- SSO_COOKIE_SECURE=true 时 Cookie 加 Secure 标记（HTTPS 公网入口建议开启；内网 http 保持 false）
"""
from __future__ import annotations

import os
import ssl
import threading
import time
import uuid
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request as UrlRequest, urlopen

import jwt
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from app.auth import SECRET_KEY, create_access_token, decode_token
from app.config import ADMIN_PASSWORD, ADMIN_USER

router = APIRouter(prefix="/api/v2", tags=["sso"])

COOKIE_NAME = "ops_session"
COOKIE_MAX_AGE = 86400  # 24h
_AUTH_CODE_TTL = 60  # 授权码 60s 有效

SSO_COOKIE_SECURE = os.getenv("SSO_COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes")
OIDC_ISSUER = os.getenv("OIDC_ISSUER", "http://127.0.0.1:9091").rstrip("/")
KEYCLOAK_BASE_URL = os.getenv("KEYCLOAK_BASE_URL", "http://10.66.66.6:8180").rstrip("/")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "ops")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "opscenter")
WORKBENCH_URL = os.getenv("WORKBENCH_URL", "http://10.66.66.5/")

# 账号切换只允许这些预定义的内网注销目标。前端只能读取，不能提交任意 URL。
RESET_TARGETS = (
    {"name": "Gitea", "url": "http://10.66.66.4:3000/user/logout", "mode": "open-and-confirm", "required": True},
    {"name": "GitLab", "url": "http://10.66.66.4:8082/users/sign_out", "mode": "open-and-confirm", "required": True},
    {"name": "Jenkins", "url": "http://10.66.66.4:8080/logout", "mode": "open-and-confirm", "required": True},
    {"name": "Grafana", "url": "http://10.66.66.5:3000/logout", "mode": "open-and-confirm", "required": True},
    {"name": "PVE", "url": "https://10.66.66.3:8006/", "mode": "manual", "required": True},
    {"name": "Nexus", "url": "http://10.66.66.4:8081/", "mode": "manual", "required": True},
    {"name": "SonarQube", "url": "http://10.66.66.4:9000/sessions/logout", "mode": "open-and-confirm", "required": True},
)

# 一次性授权码：code -> {client_id, redirect_uri, nonce, username, expires}
_auth_codes: dict = {}
_auth_codes_lock = threading.Lock()

# OIDC 客户端注册表（内存）：client_id -> {"client_secret", "redirect_uris"}
# 可通过环境变量 OIDC_CLIENTS（JSON 数组）预置，或运行时用 /oidc/clients 注册
_oidc_clients: dict = {}
_oidc_clients_lock = threading.Lock()


def _load_oidc_clients_from_env() -> None:
    """从环境变量 OIDC_CLIENTS 加载预置客户端（格式 [{"client_id","client_secret","redirect_uris":[...]}]）。"""
    raw = os.getenv("OIDC_CLIENTS", "")
    if not raw:
        return
    try:
        for item in json.loads(raw):
            cid = item.get("client_id")
            if cid:
                _oidc_clients[cid] = {
                    "client_secret": item.get("client_secret", ""),
                    "redirect_uris": item.get("redirect_uris", []),
                }
    except (ValueError, TypeError) as e:
        print(f"[sso] OIDC_CLIENTS 环境变量解析失败: {e}", flush=True)


_load_oidc_clients_from_env()


def _client_allowed(client_id: str, redirect_uri: str) -> bool:
    """校验 OIDC 客户端：注册表为空时放行（未配置则向后兼容）；否则校验 client_id 与 redirect_uri 前缀。"""
    if not _oidc_clients:
        return True
    client = _oidc_clients.get(client_id)
    if not client:
        return False
    return any(redirect_uri.startswith(ru) for ru in client.get("redirect_uris", []))


def _client_secret_ok(client_id: str, secret: Optional[str]) -> bool:
    """校验客户端密钥：未配置 secret 时放行；配置了则必须匹配。"""
    client = _oidc_clients.get(client_id)
    if not client or not client.get("client_secret"):
        return True
    return bool(secret) and secret == client["client_secret"]


# === Cookie / 会话 ===
def _set_session_cookie(resp: Response, token: str) -> None:
    """写 HttpOnly 会话 Cookie（Secure 由环境变量控制，内网 http 可正常使用）。"""
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=SSO_COOKIE_SECURE,
    )


def _read_user(request: Request) -> Optional[str]:
    """从 ops_session Cookie 解析当前用户名；无效/缺失返回 None。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = decode_token(token)
    return payload.get("username") if payload else None


def _delete_session_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE_NAME, path="/")


# === 登录 / 登出 / 会话 ===
class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def sso_login(payload: LoginRequest):
    """工作台登录：校验管理员账号，签发 JWT 写入 HttpOnly Cookie。"""
    if payload.username != ADMIN_USER or payload.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(1, payload.username)
    resp = JSONResponse({"ok": True, "username": payload.username})
    _set_session_cookie(resp, token)
    return resp


@router.post("/auth/logout")
def sso_logout():
    """退出登录：清除会话 Cookie。"""
    resp = JSONResponse({"ok": True})
    _delete_session_cookie(resp)
    return resp


@router.get("/auth/me")
def sso_me(request: Request):
    """当前会话信息（前端免密跳转前探测登录态）。"""
    user = _read_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return {"username": user, "role": "admin"}


@router.get("/sso/account-switch")
def switch_sso_account():
    """Clear the local session and end the central Keycloak session."""
    logout_url = (
        f"{KEYCLOAK_BASE_URL}/realms/{quote(KEYCLOAK_REALM, safe='')}"
        "/protocol/openid-connect/logout?"
        + urlencode({
            "client_id": KEYCLOAK_CLIENT_ID,
            "post_logout_redirect_uri": WORKBENCH_URL,
        })
    )
    resp = RedirectResponse(logout_url, status_code=302)
    _delete_session_cookie(resp)
    return resp


@router.get("/sso/reset-targets")
def sso_reset_targets():
    """Return the controlled cross-application logout checklist without credentials."""
    return {"targets": [dict(target) for target in RESET_TARGETS]}


# === 固定目标服务的 SSO 启动器 ===
PVE_WEB_URL = os.getenv("PVE_WEB_URL", "https://10.66.66.3:8006").rstrip("/")
PVE_REALM = os.getenv("PVE_REALM", "ops-sso")
PVE_IDP_HOST = os.getenv("PVE_IDP_HOST", "10.66.66.6")


@router.get("/sso/pve")
def pve_sso_launch():
    """Fetch PVE's one-time OpenID authorization URL and redirect to it."""
    body = urlencode({"realm": PVE_REALM, "redirect-url": PVE_WEB_URL}).encode()
    req = UrlRequest(
        f"{PVE_WEB_URL}/api2/json/access/openid/auth-url",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        context = ssl._create_unverified_context()
        with urlopen(req, timeout=8, context=context) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="PVE OpenID 入口不可用") from exc

    target = payload.get("data")
    parsed = urlparse(target or "")
    if parsed.scheme not in ("http", "https") or parsed.hostname != PVE_IDP_HOST:
        raise HTTPException(status_code=502, detail="PVE 返回了非预期的认证地址")
    return RedirectResponse(target, status_code=302)


# === Caddy forward_auth 校验端点 ===
@router.get("/auth/forward-auth")
def sso_forward_auth(request: Request):
    """Caddy forward_auth 登录墙：Cookie 有效 → 200 + Remote-User/Remote-Email 头；否则 401。"""
    user = _read_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "未登录"})
    resp = JSONResponse({"ok": True, "user": user})
    resp.headers["Remote-User"] = user
    resp.headers["Remote-Email"] = f"{user}@sso.local"
    return resp


# === 轻量 OIDC Provider ===
@router.get("/oidc/.well-known/openid-configuration")
def oidc_discovery():
    """OIDC Discovery 文档（GitLab/Grafana/Gitea/Jenkins 对接用）。"""
    base = f"{OIDC_ISSUER}/api/v2/oidc"
    return {
        "issuer": OIDC_ISSUER,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "userinfo_endpoint": f"{base}/userinfo",
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["HS256"],
        "scopes_supported": ["openid", "profile", "email"],
        "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic", "none"],
    }


@router.get("/oidc/.well-known/jwks.json")
def oidc_jwks():
    """JWKS（HS256 对称密钥，k = OPS_JWT_SECRET 的 base64url）。"""
    key = base64url_encode(SECRET_KEY.encode("utf-8"))
    return {
        "keys": [
            {"kty": "oct", "kid": "opscenter-sso", "use": "sig", "alg": "HS256", "k": key}
        ]
    }


def base64url_encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@router.get("/oidc/authorize")
def oidc_authorize(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    state: Optional[str] = Query(None),
    scope: Optional[str] = Query("openid"),
    nonce: Optional[str] = Query(None),
):
    """OIDC 授权端点：已登录 → 302 带一次性 code+state；未登录 → 302 到工作台登录页。"""
    if response_type != "code":
        raise HTTPException(status_code=400, detail="response_type 仅支持 code")
    if not _client_allowed(client_id, redirect_uri):
        raise HTTPException(status_code=400, detail="client_id 或 redirect_uri 未注册")
    user = _read_user(request)
    if not user:
        # 登录页（前端实现）登录后回跳原 redirect_uri 继续授权流程
        return RedirectResponse(f"/#/login?redirect={quote(redirect_uri)}", status_code=302)
    code = uuid.uuid4().hex
    with _auth_codes_lock:
        _auth_codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "nonce": nonce,
            "username": user,
            "expires": time.time() + _AUTH_CODE_TTL,
        }
    sep = "&" if "?" in redirect_uri else "?"
    url = f"{redirect_uri}{sep}code={code}"
    if state:
        url += f"&state={state}"
    return RedirectResponse(url, status_code=302)


@router.post("/oidc/token")
async def oidc_token(request: Request):
    """OIDC Token 端点：授权码换 id_token + access_token（一次性）。支持表单或 JSON。"""
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        body = await request.json()
    else:
        form = await request.form()
        body = {k: v for k, v in form.items()}
    grant_type = body.get("grant_type")
    code = body.get("code")
    redirect_uri = body.get("redirect_uri")
    client_id = body.get("client_id")
    client_secret = body.get("client_secret")
    if grant_type != "authorization_code":
        raise HTTPException(status_code=400, detail="grant_type 仅支持 authorization_code")
    if not code:
        raise HTTPException(status_code=400, detail="缺少 code")
    with _auth_codes_lock:
        rec = _auth_codes.pop(code, None)
    if not rec:
        raise HTTPException(status_code=400, detail="授权码无效或已使用")
    if rec["expires"] < time.time():
        raise HTTPException(status_code=400, detail="授权码已过期")
    if client_id is not None and rec["client_id"] != client_id:
        raise HTTPException(status_code=400, detail="client_id 不匹配")
    if redirect_uri is not None and rec["redirect_uri"] != redirect_uri:
        raise HTTPException(status_code=400, detail="redirect_uri 不匹配")
    if not _client_secret_ok(rec["client_id"], client_secret):
        raise HTTPException(status_code=400, detail="client_secret 校验失败")

    now = datetime.now(timezone.utc)
    id_token = jwt.encode(
        {
            "iss": OIDC_ISSUER,
            "sub": rec["username"],
            "aud": rec["client_id"],
            "iat": now,
            "exp": now + timedelta(hours=24),
            "nonce": rec.get("nonce"),
            "name": rec["username"],
            "email": f"{rec['username']}@sso.local",
            "preferred_username": rec["username"],
        },
        SECRET_KEY,
        algorithm="HS256",
    )
    access_token = create_access_token(1, rec["username"])
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": COOKIE_MAX_AGE,
        "id_token": id_token,
    }


@router.get("/oidc/userinfo")
def oidc_userinfo(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """OIDC UserInfo：Bearer access_token 或 ops_session Cookie 任一有效即可。"""
    user = None
    if authorization and authorization.lower().startswith("bearer "):
        payload = decode_token(authorization[7:].strip())
        if payload:
            user = payload.get("username")
    if not user:
        user = _read_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return {
        "sub": user,
        "name": user,
        "email": f"{user}@sso.local",
        "preferred_username": user,
    }


class OidcClientCreate(BaseModel):
    client_id: str
    client_secret: str = ""
    redirect_uris: list = []


@router.get("/oidc/clients")
def oidc_client_list():
    """列出已注册 OIDC 客户端（不含 secret）。"""
    with _oidc_clients_lock:
        return [
            {"client_id": cid, "redirect_uris": c.get("redirect_uris", [])}
            for cid, c in sorted(_oidc_clients.items())
        ]


@router.post("/oidc/clients", status_code=201)
def oidc_client_register(payload: OidcClientCreate):
    """注册 OIDC 客户端（内存存储，重启后由 OIDC_CLIENTS 环境变量恢复）。"""
    if not payload.client_id or not payload.redirect_uris:
        raise HTTPException(status_code=400, detail="client_id 与 redirect_uris 必填")
    with _oidc_clients_lock:
        _oidc_clients[payload.client_id] = {
            "client_secret": payload.client_secret,
            "redirect_uris": payload.redirect_uris,
        }
    return {"ok": True, "client_id": payload.client_id}
