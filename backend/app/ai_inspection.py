"""Read-only automatic AI inspection for abnormal test services."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
import logging
import os
import threading
import time
import uuid

from app.ai_context import _service_rows
from app.ai_ops import run_ai_analysis
from app.opencode import OpenCodeError


logger = logging.getLogger("opscenter.ai_inspection")


def _env_bool(name: str, default: str = "false") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in {"1", "true", "yes"}


AI_OPS_AUTOCHECK_ENABLED = _env_bool("AI_OPS_AUTOCHECK_ENABLED")
AI_OPS_AUTOCHECK_INTERVAL_SECONDS = max(
    60, min(int(os.getenv("AI_OPS_AUTOCHECK_INTERVAL_SECONDS", "300")), 3600)
)
AI_OPS_AUTOCHECK_COOLDOWN_SECONDS = max(
    300, min(int(os.getenv("AI_OPS_AUTOCHECK_COOLDOWN_SECONDS", "900")), 86400)
)
AI_OPS_AUTOCHECK_MAX_PER_CYCLE = max(
    1, min(int(os.getenv("AI_OPS_AUTOCHECK_MAX_PER_CYCLE", "3")), 5)
)
_HISTORY_LIMIT = 100
_ABNORMAL_STATUSES = {"down", "degraded"}

_records = deque(maxlen=_HISTORY_LIMIT)
_state_lock = threading.Lock()
_last_dispatch: dict[str, tuple[str, float]] = {}


def auto_inspection_status() -> dict:
    return {
        "enabled": AI_OPS_AUTOCHECK_ENABLED,
        "interval_seconds": AI_OPS_AUTOCHECK_INTERVAL_SECONDS,
        "cooldown_seconds": AI_OPS_AUTOCHECK_COOLDOWN_SECONDS,
        "max_per_cycle": AI_OPS_AUTOCHECK_MAX_PER_CYCLE,
        "history_limit": _HISTORY_LIMIT,
        "mode": "read_only_dry_run",
        "execution_enabled": False,
        "source": "persisted_service_health_and_incidents",
    }


def list_inspections(limit: int = 20) -> list[dict]:
    with _state_lock:
        return list(_records)[: max(1, min(limit, _HISTORY_LIMIT))]


def _fingerprint(row: dict) -> str:
    parts = (
        row.get("status"),
        row.get("last_error_code"),
        row.get("last_error"),
        row.get("consecutive_failures"),
        row.get("active_incident_id"),
    )
    return "|".join(str(part or "") for part in parts)


def _is_abnormal(row: dict) -> bool:
    return (
        row.get("status") in _ABNORMAL_STATUSES
        or bool(row.get("active_incident_id"))
        or int(row.get("consecutive_failures") or 0) > 0
    ) and row.get("status") != "disabled"


def _question(row: dict) -> str:
    return (
        f"自动巡检服务：{row.get('name') or row.get('key')}. "
        f"当前状态：{row.get('status') or 'unknown'}；"
        f"错误码：{row.get('last_error_code') or '无'}；"
        f"连续失败：{row.get('consecutive_failures') or 0} 次。"
        "请根据上下文判断最可能的原因、需要补充确认的证据和安全的解决办法。"
    )


def _append(record: dict) -> None:
    with _state_lock:
        _records.appendleft(record)


def _inspect_one(row: dict) -> dict:
    started_at = datetime.utcnow().isoformat() + "Z"
    service_key = str(row.get("key") or "")
    record = {
        "id": uuid.uuid4().hex,
        "service_key": service_key,
        "service_name": str(row.get("name") or service_key),
        "service_status": str(row.get("status") or "unknown"),
        "trigger": "automatic",
        "started_at": started_at,
        "completed_at": None,
        "status": "running",
        "analysis": None,
        "context_summary": None,
        "model": None,
        "error": None,
    }
    try:
        result = run_ai_analysis(
            _question(row),
            service_key=service_key or None,
            incident_hours=24,
        )
        record.update({
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "analysis": result["analysis"],
            "context_summary": result["context"].get("summary", {}),
            "model": result.get("model"),
        })
    except OpenCodeError as exc:
        record.update({
            "status": "failed",
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "error": {"code": exc.code, "message": exc.public_message},
        })
    except Exception:
        logger.exception("自动 AI 巡检失败: %s", service_key)
        record.update({
            "status": "failed",
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "error": {"code": "inspection_failed", "message": "AI 自动巡检暂时失败"},
        })
    _append(record)
    return record


def run_auto_inspection_cycle() -> int:
    """Inspect a bounded number of abnormal services without any write action."""
    if not AI_OPS_AUTOCHECK_ENABLED:
        return 0
    try:
        rows = _service_rows()
    except Exception:
        logger.exception("读取自动巡检服务上下文失败")
        return 0

    now = time.monotonic()
    selected = []
    with _state_lock:
        for row in rows:
            if not _is_abnormal(row):
                continue
            key = str(row.get("key") or "")
            if not key:
                continue
            fingerprint = _fingerprint(row)
            previous = _last_dispatch.get(key)
            if previous and previous[0] == fingerprint and now - previous[1] < AI_OPS_AUTOCHECK_COOLDOWN_SECONDS:
                continue
            _last_dispatch[key] = (fingerprint, now)
            selected.append(row)
            if len(selected) >= AI_OPS_AUTOCHECK_MAX_PER_CYCLE:
                break

    for row in selected:
        _inspect_one(row)
    if selected:
        logger.info("自动 AI 巡检完成，共检查 %d 个异常服务", len(selected))
    return len(selected)


async def ai_inspection_loop() -> None:
    if not AI_OPS_AUTOCHECK_ENABLED:
        logger.info("AI 自动巡检已禁用（AI_OPS_AUTOCHECK_ENABLED=false）")
        return
    await asyncio.sleep(45)
    while True:
        try:
            await asyncio.to_thread(run_auto_inspection_cycle)
        except Exception:
            logger.exception("AI 自动巡检循环异常")
        await asyncio.sleep(AI_OPS_AUTOCHECK_INTERVAL_SECONDS)

