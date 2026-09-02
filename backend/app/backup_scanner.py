"""OpsCenter 备份状态验证（v3.27, D3）。

设计：后端每 6h 拉取各服务器 Agent 的 /api/v1/backup/check（纯拉模型），
结果写 metric_history(metric='backup_age') 供告警引擎判定"备份过期"。
BACKUP_CHECK_ENABLED=false 一键关停（回滚兜底）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.config import BACKUP_CHECK_ENABLED, LOCAL_AGENT_HOST
from app.database import get_db
from app.models import BackupCheck, MetricHistory, Server

logger = logging.getLogger("opscenter.backup")


def _agent_backup_check(server: Server, target: str, min_size: int = 0, timeout: float = 8.0):
    """调用远端 Agent 的 backup/check 端点。"""
    import requests
    host = LOCAL_AGENT_HOST if server.agent_type == "local" else server.host
    port = server.agent_port or 19100
    token = server.agent_token or ""
    url = f"http://{host}:{port}/api/v1/backup/check"
    params = {"path": target, "min_size": str(min_size)}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return (r.json().get("check") or {})


def run_backup_check() -> None:
    """检查一轮所有启用的备份项，写 backup_age 指标。"""
    if not BACKUP_CHECK_ENABLED:
        return
    with get_db() as db:
        checks = db.query(BackupCheck).filter(BackupCheck.enabled == True).all()  # noqa: E712
        for chk in checks:
            server = db.query(Server).filter(Server.id == chk.server_id).first()
            if not server:
                continue
            try:
                result = _agent_backup_check(server, chk.target_path, chk.min_size_bytes)
            except Exception as e:
                logger.warning("backup check failed %s on %s: %s", chk.name, server.name, e)
                continue
            if not result.get("exists"):
                # 文件不存在 -> 视为超期（写一个大值触发"备份过期"规则）
                db.add(MetricHistory(server_id=chk.server_id, metric="backup_age",
                                     value=float(chk.expected_interval_hours * 24 * 365)))
                db.commit()
                logger.warning("backup MISSING %s on %s (path=%s)", chk.name, server.name, chk.target_path)
                continue
            age = result.get("age_hours")
            if age is not None:
                db.add(MetricHistory(server_id=chk.server_id, metric="backup_age", value=float(age)))
                db.commit()


async def backup_check_loop() -> None:
    """后台任务：每 6h 一轮备份检查。"""
    await asyncio.sleep(120)
    while True:
        try:
            await asyncio.to_thread(run_backup_check)
        except Exception as e:
            logger.exception("backup check loop error: %s", e)
        await asyncio.sleep(6 * 3600)


def seed_backup_rule(db=None) -> None:
    """幂等 seed 备份过期规则（alert_rules 无 backup_age 规则时插入）。"""
    from app.models import AlertRule

    def _do(sess):
        exists = sess.query(AlertRule).filter(AlertRule.metric == 'backup_age').first()
        if exists:
            return False
        sess.add(AlertRule(
            name='备份过期', metric='backup_age', value_type='numeric',
            operator='>', threshold='24', duration_sec=0, cooldown_sec=3600,
            enabled=True,
        ))
        sess.commit()
        return True

    if db is not None:
        return _do(db)
    from app.database import get_db as _gdb
    with _gdb() as sess:
        return _do(sess)
