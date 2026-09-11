"""Agent 部署/升级任务注册表（v5.0.0，需求基线 2026-09-10 §9.3）。

- 状态机：deploying → verifying(可选) → success / failed；
- 内部委托统一任务注册表 `app.services.tasks.task_registry`：Redis 可用时持久化
  （`opscenter:task:{task_id}` 与 `opscenter:agent_task:{server_id}`，TTL 24h），
  后端重启后内存为空但可从 Redis 恢复最近任务；Redis 不可用时退化为纯内存语义
  （重启视为任务中断，servers.agent_status 仍为权威）；
- main.py 的 `_deploy_agent_background` 与 server_details.py 的状态端点继续使用
  `AGENT_TASKS` 的 start/update/finish/get，接口与返回字段保持不变。
"""

from __future__ import annotations

import time
import uuid

from app.services.tasks import _sanitize, task_registry

_AGENT_TASK_TYPE = "agent_deploy"


def _latest(server_id: str) -> dict | None:
    """server_id 维度取当前任务：优先带 kind 的 Agent 自建任务（内存），
    其次 Redis/内存中最近一条 agent_deploy 记录。"""
    sid = str(server_id)
    task = task_registry.get_by_server(sid, task_type=_AGENT_TASK_TYPE)
    if task is not None and task.get("kind"):
        return task
    for candidate in task_registry.list(server_id=sid, limit=200):
        if candidate.get("kind"):
            return candidate
    return task


class AgentTaskRegistry:
    def start(self, server_id: str, kind: str = "agent_deploy") -> dict:
        sid = str(server_id)
        now = time.time()
        task = task_registry.create(str(uuid.uuid4()), _AGENT_TASK_TYPE, server_id=sid)
        updated = task_registry.update(
            task["task_id"],
            kind=kind,
            status="deploying",
            phase="部署中",
            started_at=now,
        )
        return dict(updated or task)

    def update(self, server_id: str, phase: str = "", message: str = ""):
        task = _latest(server_id)
        if not task:
            return
        fields: dict = {}
        if phase:
            fields["phase"] = phase
        if message:
            fields["message"] = _sanitize(message)
        task_registry.update(task["task_id"], **fields)

    def finish(self, server_id: str, success: bool, message: str = "", error: str = ""):
        task = _latest(server_id)
        if not task:
            return
        task_registry.finish(task["task_id"], success, message=message, error=error)

    def get(self, server_id: str) -> dict | None:
        task = _latest(server_id)
        if not task:
            return None
        snapshot = dict(task)
        if not snapshot.get("kind"):
            snapshot["kind"] = snapshot.get("task_type") or _AGENT_TASK_TYPE
        return snapshot


AGENT_TASKS = AgentTaskRegistry()
