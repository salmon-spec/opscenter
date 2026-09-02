"""OpsCenter 日志异常检测（v3.27, D2）。

设计：后端每 60s 拉取各服务器 Agent 的 /api/v1/log/scan（纯拉模型），
命中写 log_matches 明细 + metric_history(metric='log_match') 供告警引擎复用。
LOG_SCAN_ENABLED=false 一键关停（回滚兜底）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.config import LOG_SCAN_ENABLED, LOCAL_AGENT_HOST
from app.database import get_db
from app.models import LogMatch, LogRule, MetricHistory, Server

logger = logging.getLogger("opscenter.logwatch")


def _agent_log_scan(server: Server, rule: LogRule, timeout: float = 8.0):
    """调用远端 Agent 的 log/scan 端点，返回命中行列表。"""
    import requests
    host = LOCAL_AGENT_HOST if server.agent_type == "local" else server.host
    port = server.agent_port or 19100
    token = server.agent_token or ""
    url = f"http://{host}:{port}/api/v1/log/scan"
    params = {"path": rule.log_path, "pattern": rule.pattern, "tail_lines": rule.tail_lines}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data.get("matches") or []


def run_log_scan() -> None:
    """扫描一轮所有启用的日志规则，命中写 log_matches + metric_history。"""
    if not LOG_SCAN_ENABLED:
        return
    with get_db() as db:
        rules = db.query(LogRule).filter(LogRule.enabled == True).all()  # noqa: E712
        for rule in rules:
            server = db.query(Server).filter(Server.id == rule.server_id).first()
            if not server:
                continue
            try:
                matches = _agent_log_scan(server, rule)
            except Exception as e:
                logger.warning("log scan failed rule=%s server=%s: %s", rule.name, server.name, e)
                continue
            if not matches:
                continue
            for line in matches[-20:]:  # 每轮最多落 20 条明细，防膨胀
                db.add(LogMatch(rule_id=rule.id, server_id=rule.server_id, matched_line=line[:1000]))
            db.add(MetricHistory(
                server_id=rule.server_id,
                metric="log_match",
                value=float(len(matches)),
            ))
            db.commit()
            logger.info("log rule=%s server=%s matched=%d", rule.name, server.name, len(matches))


async def log_scan_loop() -> None:
    """后台任务：每 60s 一轮日志扫描。"""
    await asyncio.sleep(90)  # 等引擎与 agent 就绪
    while True:
        try:
            await asyncio.to_thread(run_log_scan)
        except Exception as e:
            logger.exception("log scan loop error: %s", e)
        await asyncio.sleep(60)
