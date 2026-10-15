from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt


class SessionVerifier:
    """Local workbench session verification; never calls the legacy backend."""

    def __init__(self):
        self.public_key_file = os.getenv("OPS_JWT_PUBLIC_KEY_FILE", "").strip()
        self.global_secret = os.getenv("OPS_JWT_SECRET", "").strip()
        self.legacy_until = os.getenv("OPS_JWT_LEGACY_UNTIL", "").strip()
        self.admin_user = os.getenv("OPS_ADMIN_USER", "admin").strip() or "admin"

    def verify(self, token: str) -> dict:
        if not self.public_key_file and not self.global_secret:
            raise HTTPException(503, "工作台认证未配置")
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
            if algorithm == "EdDSA" and self.public_key_file:
                public_key = Path(self.public_key_file).read_bytes()
                claims = jwt.decode(token, public_key, algorithms=["EdDSA"], issuer="opscenter",
                                    audience="opscenter-modules",
                                    options={"require": ["exp", "sub", "iss", "aud", "role"]})
                role = str(claims["role"])
                if role not in {"viewer", "editor", "admin"}:
                    raise jwt.InvalidTokenError("invalid role")
            elif algorithm == "HS256" and self.global_secret:
                if self.legacy_until and datetime.now(timezone.utc) >= datetime.fromisoformat(self.legacy_until.replace("Z", "+00:00")):
                    raise jwt.InvalidTokenError("legacy token disabled")
                claims = jwt.decode(token, self.global_secret, algorithms=["HS256"])
                role = "admin"
            else:
                raise jwt.InvalidTokenError("unsupported algorithm")
        except (jwt.InvalidTokenError, OSError, ValueError):
            raise HTTPException(401, "工作台登录无效或已过期") from None
        username = str(claims.get("username") or "")
        if str(claims.get("sub")) != "1" or not hmac.compare_digest(username, self.admin_user):
            raise HTTPException(401, "工作台登录无效")
        return {"username": username, "role": role}


class SessionMiddleware:
    """Protect browser-facing module API while keeping container probes public."""

    def __init__(self, app):
        self.app = app
        self.verifier = SessionVerifier()

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope.get("type") == "http" and path.startswith("/api/v1/")
                and path != "/api/v1/agent/telemetry" and not path.startswith("/api/v1/internal/")):
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            authorization = headers.get(b"authorization", b"").decode("latin-1")
            scheme, _, token = authorization.partition(" ")
            try:
                if scheme.lower() != "bearer" or not token:
                    raise HTTPException(401, "请先登录运维工作台")
                self.verifier.verify(token)
            except HTTPException as exc:
                await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class ModuleAuth:
    """Validate the workbench JWT for module-owned write operations."""

    def __init__(self, store_factory):
        self.store_factory = store_factory
        self.verifier = SessionVerifier()
        self.bearer = HTTPBearer(auto_error=False)

    def ensure_schema(self):
        with self.store_factory().connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS module_audit (
                    id BIGSERIAL PRIMARY KEY,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    details JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

    def register(self, app: FastAPI):
        auth = self

        def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(auth.bearer)):
            if not credentials:
                raise HTTPException(401, "请先登录运维工作台")
            return auth.verifier.verify(credentials.credentials)

        def editor(user=Depends(current_user)):
            if user["role"] not in {"editor", "admin"}:
                raise HTTPException(403, "此操作需要编辑权限")
            return user

        def admin(user=Depends(current_user)):
            if user["role"] != "admin":
                raise HTTPException(403, "此操作需要管理员权限")
            return user

        @app.get("/api/v1/auth/me")
        def me(user=Depends(current_user)):
            return user

        app.state.module_auth = auth
        app.state.current_user = current_user
        app.state.require_editor = editor
        app.state.require_admin = admin
