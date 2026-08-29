"""OpsCenter 操作审计（v3.28, A1）。

写操作（POST/PUT/DELETE）自动记录到 audit_logs 表；读操作不记录。
白名单路径（健康检查/状态页/审计自身）跳过，防递归与噪音。
AUDIT_ENABLED=false 一键关停（回滚兜底）。
"""
from __future__ import annotations

import logging
import json
import re
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import AUDIT_ENABLED
from app.database import get_db
from app.models import AuditLog

logger = logging.getLogger("opscenter.audit")

# 跳过审计的路径前缀（GET 类读操作本来就不记录，这里主要防递归/噪音写操作）
SKIP_PREFIXES = (
    "/api/v2/health",
    "/api/v2/status-page",
    "/api/v2/audit-logs",   # 审计自身的写操作不记录（防递归）
    "/api/v2/reports/",     # 日报生成/查看是汇总行为，不逐一记录（generate 除外可记录）
)

# 路径 → (action, resource) 映射规则；未匹配到则按 method 归类
_RESOURCE_PATTERNS = [
    (r"alert-rules", "alert-rule"),
    (r"alert-events/([^/]+)/ack", "alert-event"),
    (r"alert-silences", "silence"),
    (r"cert-checks", "cert"),
    (r"log-rules", "log-rule"),
    (r"backup-checks", "backup"),
    (r"images/scan", "image"),
    (r"processes", "process"),
    (r"servers", "server"),
    (r"databases/.*/accounts", "database-account"),
    (r"databases", "database"),
    (r"services", "service"),
    (r"reports/generate", "report"),
    (r"auth/login", "login"),
]
_SECRET_KEYS = re.compile(r"password|passwd|secret|token|ssh_key|private_key|credential", re.I)


def _redact(value):
    if isinstance(value, dict):
        return {key: ("••••••••" if _SECRET_KEYS.search(str(key)) else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _classify(path: str, method: str):
    """从请求路径提取 (action, resource)。"""
    action = {"POST": "create", "PUT": "update", "DELETE": "delete"}.get(method, method.lower())
    for pat, res in _RESOURCE_PATTERNS:
        if re.search(pat, path):
            # ack / scan / generate 语义修正
            if "ack" in path:
                action = "update"
            elif "scan" in path or "generate" in path:
                action = "generate"
            return action, res
    return action, "other"


def _record(action: str, resource: str, resource_id=None, detail=None,
            ip=None, status="success") -> None:
    if not AUDIT_ENABLED:
        return
    try:
        with get_db() as db:
            db.add(AuditLog(
                action=action, resource=resource, resource_id=resource_id,
                detail=(detail or "")[:500], ip=ip, status=status,
            ))
            db.commit()
    except Exception as e:
        logger.warning("audit record failed: %s", e)


class AuditMiddleware(BaseHTTPMiddleware):
    """拦截写操作记录审计日志；读操作直接放行。"""

    async def dispatch(self, request: Request, call_next):
        method = request.method
        path = urlparse(str(request.url)).path

        # 只审计写操作
        if method not in ("POST", "PUT", "DELETE"):
            return await call_next(request)

        # 跳过白名单
        if any(path.startswith(p) for p in SKIP_PREFIXES):
            return await call_next(request)

        # 先执行，记录结果状态
        response = await call_next(request)
        try:
            action, resource = _classify(path, method)
            resource_id = None
            m = re.search(r"/([0-9a-f-]{36})$", path)
            if m:
                resource_id = m.group(1)
            # detail 取请求体摘要（仅对 JSON 且较短时）
            detail = None
            try:
                body = await request.body()
                if body and len(body) < 300:
                    raw = body.decode("utf-8", errors="replace")
                    try:
                        detail = json.dumps(_redact(json.loads(raw)), ensure_ascii=False)[:200]
                    except Exception:
                        detail = "[non-json request body omitted]"
            except Exception:
                pass
            client_ip = request.client.host if request.client else None
            _record(action, resource, resource_id=resource_id,
                    detail=detail, ip=client_ip,
                    status="success" if response.status_code < 400 else "failed")
        except Exception as e:
            logger.warning("audit middleware error: %s", e)
        return response
