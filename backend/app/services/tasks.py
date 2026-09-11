"""统一任务注册表（v5.0.0）。

设计：
- 内存 dict（task_id → task）+ 锁为权威热数据；Redis 可用时同步持久化到
  `opscenter:task:{task_id}`（JSON，TTL 24h）与 `opscenter:agent_task:{server_id}`
  映射（值为 task_id，TTL 24h）；
- get/get_by_server 未命中内存时读 Redis 恢复进内存，后端重启后仍可查询最近任务；
- Redis 不可用 → 纯内存行为，与 v4.9 语义一致（重启视为中断）；
- 所有 message/error 必须经 _sanitize 脱敏（沿用 agent_tasks.py 的标记集）；
- 全部 fail-open：Redis 异常绝不上抛。

环境变量：REDIS_URL（由 redis_store 统一读取）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.services.redis_store import redis_store

TASK_TTL = 86400
_TASK_KEY = "opscenter:task:{}"
_SERVER_KEY = "opscenter:agent_task:{}"
_MAX_TASKS = 1024
_SANITIZE_MARKERS = ("__password__", "Bearer ", "Authorization", "ssh_key", "agent_token")
_STATUS_PHASE = {
    "queued": "排队中",
    "running": "执行中",
    "deploying": "部署中",
    "verifying": "校验中",
    "success": "成功",
    "failed": "失败",
}


def _sanitize(value: Any, limit: int = 500) -> str:
    text = str(value or "")
    for marker in _SANITIZE_MARKERS:
        if marker in text:
            text = text.replace(marker, "***")
    return text[:limit]


class TaskRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, dict] = {}

    # ── 内部辅助 ──

    def _redis_ok(self) -> bool:
        try:
            return bool(redis_store.available())
        except Exception:
            return False

    def _persist(self, task: dict) -> None:
        if not self._redis_ok():
            return
        try:
            redis_store.set_json(_TASK_KEY.format(task["task_id"]), task, ttl=TASK_TTL)
            server_id = task.get("server_id")
            if server_id:
                redis_store.set_json(_SERVER_KEY.format(server_id), task["task_id"], ttl=TASK_TTL)
        except Exception:
            pass

    def _evict_locked(self) -> None:
        while len(self._tasks) > _MAX_TASKS:
            oldest = min(self._tasks, key=lambda tid: self._tasks[tid].get("updated_at") or 0)
            self._tasks.pop(oldest, None)

    # ── 公开 API ──

    def create(self, task_id: str, task_type: str, *, server_id: str | None = None,
               meta: dict | None = None) -> dict:
        now = time.time()
        task = {
            "task_id": str(task_id),
            "task_type": str(task_type),
            "server_id": str(server_id) if server_id is not None else None,
            "status": "queued",
            "phase": "排队中",
            "message": "",
            "error": "",
            "meta": dict(meta or {}),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._tasks[task["task_id"]] = task
            self._evict_locked()
            snapshot = dict(task)
        self._persist(snapshot)
        return dict(snapshot)

    def update(self, task_id: str, **fields: Any) -> dict | None:
        tid = str(task_id)
        with self._lock:
            task = self._tasks.get(tid)
            if task is None:
                return None
            for key, value in fields.items():
                if key == "task_id":
                    continue
                if key in ("message", "error"):
                    value = _sanitize(value)
                task[key] = value
            if "status" in fields and "phase" not in fields:
                phase = _STATUS_PHASE.get(str(fields["status"]).lower())
                if phase:
                    task["phase"] = phase
            task["updated_at"] = time.time()
            snapshot = dict(task)
        self._persist(snapshot)
        return dict(snapshot)

    def finish(self, task_id: str, success: bool, message: str = "", error: str = "") -> dict | None:
        tid = str(task_id)
        with self._lock:
            task = self._tasks.get(tid)
            if task is None:
                return None
            task["status"] = "success" if success else "failed"
            task["phase"] = "成功" if success else "失败"
            task["message"] = _sanitize(message)
            task["error"] = _sanitize(error)
            task["updated_at"] = time.time()
            snapshot = dict(task)
        self._persist(snapshot)
        return dict(snapshot)

    def get(self, task_id: str) -> dict | None:
        tid = str(task_id)
        with self._lock:
            task = self._tasks.get(tid)
            if task is not None:
                return dict(task)
        if not self._redis_ok():
            return None
        try:
            data = redis_store.get_json(_TASK_KEY.format(tid))
        except Exception:
            return None
        if not isinstance(data, dict) or not data.get("task_id"):
            return None
        with self._lock:
            existing = self._tasks.get(tid)
            if existing is not None:
                return dict(existing)
            self._tasks[tid] = data
            self._evict_locked()
            return dict(data)

    def get_by_server(self, server_id: str, task_type: str | None = None) -> dict | None:
        sid = str(server_id)
        with self._lock:
            best = None
            for task in self._tasks.values():
                if str(task.get("server_id") or "") != sid:
                    continue
                if task_type and task.get("task_type") != task_type:
                    continue
                if best is None or (task.get("updated_at") or 0) >= (best.get("updated_at") or 0):
                    best = task
            if best is not None:
                return dict(best)
        if not self._redis_ok():
            return None
        try:
            mapped = redis_store.get_json(_SERVER_KEY.format(sid))
        except Exception:
            return None
        if not mapped:
            return None
        task = self.get(str(mapped))
        if task is None:
            return None
        if task_type and task.get("task_type") != task_type:
            return None
        return task

    def list(self, *, task_type: str | None = None, server_id: str | None = None,
             limit: int = 50) -> list[dict]:
        try:
            size = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            size = 50
        sid = str(server_id) if server_id is not None else None
        with self._lock:
            items = [
                dict(task) for task in self._tasks.values()
                if (not task_type or task.get("task_type") == task_type)
                and (not sid or str(task.get("server_id") or "") == sid)
            ]
        items.sort(key=lambda task: task.get("updated_at") or 0, reverse=True)
        return items[:size]

    def cleanup(self, max_age_seconds: int = 86400) -> int:
        try:
            age = max(0, int(max_age_seconds))
        except (TypeError, ValueError):
            age = 86400
        cutoff = time.time() - age
        removed: list[dict] = []
        with self._lock:
            for tid, task in list(self._tasks.items()):
                if (task.get("updated_at") or 0) < cutoff:
                    removed.append(self._tasks.pop(tid, task))
        if removed and self._redis_ok():
            try:
                redis_store.delete(*[_TASK_KEY.format(task.get("task_id", "")) for task in removed])
            except Exception:
                pass
        return len(removed)


task_registry = TaskRegistry()
