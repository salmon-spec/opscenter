"""OpsCenter 告警引擎（v3.26, F4/F5）。

设计核心：规则表 + 状态机。所有指标监控（CPU/内存/磁盘/主机状态/Agent 状态）
复用同一引擎，之后证书/日志/备份监控只需新增一条规则，不加逻辑。

指标取值约定（已核对 models + 落库点 main.py:2565）：
- 数值型存于 metric_history，metric 名为 cpu / memory / disk / load1 ...（非 cpu_percent）
- 字符串型存于 servers 表：server_status -> Server.status(online/offline/unknown)，
  agent_status -> Server.agent_status(running/stopped/not_deployed/error)
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import requests
from sqlalchemy import desc

from app.config import (
    ALERTING_ENABLED,
    SILENCE_ENABLED,
    DEFAULT_NOTIFY_WEBHOOKS,
    RETENTION_LATENCY_DAYS,
    RETENTION_METRIC_DAYS,
    RETENTION_STATS_DAYS,
)
from app.database import get_db
from app.models import (
    AuditLog, DailyReport,
    AlertEvent,
    AlertRule,
    AlertSilence,
    MetricHistory,
    NetworkLatency,
    NetworkStats,
    Server,
)

logger = logging.getLogger("opscenter.alerting")

# 字符串型指标 -> servers 表字段名
_STRING_METRIC_ATTR = {"server_status": "status", "agent_status": "agent_status"}

_OPERATORS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


# === 取值与判定 ===

def _get_current_value(server: Server, rule: AlertRule, db) -> Tuple[Optional[str], str]:
    """返回 (当前值文本, 值类型)。无数据返回 (None, ...)。

    Args:
        server: 目标主机 ORM 对象
        rule: 告警规则（含 metric / value_type）
        db: 数据库会话
    """
    if rule.value_type == "string":
        attr = _STRING_METRIC_ATTR.get(rule.metric, rule.metric)
        raw = getattr(server, attr, None)
        return (str(raw) if raw is not None else None), "string"
    # 数值型：取 metric_history 最新一行
    row = (
        db.query(MetricHistory)
        .filter(MetricHistory.server_id == server.id, MetricHistory.metric == rule.metric)
        .order_by(MetricHistory.timestamp.desc())
        .first()
    )
    if row is None:
        return None, "numeric"
    return str(row.value), "numeric"


def _evaluate(rule: AlertRule, current_value: Optional[str]) -> bool:
    """按规则判定当前是否越限（breach）。"""
    if current_value is None:
        return False  # 无数据不触发，避免采集空窗误报
    op = _OPERATORS.get(rule.operator)
    if op is None:
        return False
    try:
        if rule.value_type == "numeric":
            return op(float(current_value), float(rule.threshold))
        return op(current_value, rule.threshold)
    except (ValueError, TypeError):
        return False


# === 通知（F5, M1 修正：webhook 来源 = per-rule JSONB + env 全局，无 settings 表依赖） ===

def _resolve_webhooks(rule: AlertRule) -> List[str]:
    """合并 per-rule webhook 与全局默认 webhook，去重。"""
    urls = list(rule.notify_webhooks or [])
    urls.extend(DEFAULT_NOTIFY_WEBHOOKS)
    # 保序去重
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _is_silenced(rule: AlertRule, server: Server, db, now: datetime = None) -> bool:
    """v3.27 S1：检查规则是否处于静默/维护窗口内。

    命中 alert_silences 中 rule_id(或NULL=全局) + server_id(或NULL=全部) + [starts_at, ends_at) 的记录即静默。
    SILENCE_ENABLED=false 时直接返回 False（回滚兜底）。
    """
    if not SILENCE_ENABLED:
        return False
    now = now or datetime.utcnow()
    q = db.query(AlertSilence).filter(AlertSilence.starts_at <= now, AlertSilence.ends_at > now)
    # 规则级匹配：rule_id 相等或全局(NULL)
    from sqlalchemy import or_
    q = q.filter(or_(AlertSilence.rule_id == rule.id, AlertSilence.rule_id.is_(None)))
    # 服务器级匹配：server_id 相等或全局(NULL)
    q = q.filter(or_(AlertSilence.server_id == server.id, AlertSilence.server_id.is_(None)))
    return db.query(q.exists()).scalar() or False


def _notify(rule: AlertRule, server: Server, value: Optional[str], firing: bool) -> None:
    """发送飞书交互卡片；webhook 为空时仅落库不发送。"""
    webhooks = _resolve_webhooks(rule)
    if not webhooks:
        return
    if firing:
        title = f"🔴 [告警] {rule.name} — {server.name}"
        color = "red"
        state_text = "**状态**：触发 firing"
    else:
        title = f"✅ [恢复] {rule.name} — {server.name}"
        color = "green"
        state_text = "**状态**：已恢复 recovered"
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**规则**：{rule.name}\n"
                            f"**主机**：{server.name}\n"
                            f"**当前值**：{value}\n"
                            f"**阈值**：{rule.operator} {rule.threshold}\n"
                            f"**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        ),
                    },
                },
                {"tag": "div", "text": {"tag": "lark_md", "content": state_text}},
            ],
        },
    }
    for url in webhooks:
        try:
            requests.post(url, json=card, timeout=5)
        except Exception as e:  # 通知失败不阻塞引擎
            logger.warning("feishu notify failed: %s", e)


# === 状态机（H3 修正：pending -> firing -> recovered，含 duration 防抖 + cooldown 冷却） ===

def _evaluate_rule_for_server(rule: AlertRule, server: Server, db) -> None:
    now = datetime.utcnow()
    if _is_silenced(rule, server, db, now):
        logger.debug("rule %s silenced for server %s (maintenance window)", rule.name, server.name)
        return
    value, _ = _get_current_value(server, rule, db)
    breach = _evaluate(rule, value)

    open_ev = (
        db.query(AlertEvent)
        .filter(
            AlertEvent.rule_id == rule.id,
            AlertEvent.server_id == server.id,
            AlertEvent.status.in_(["pending", "firing"]),
        )
        .first()
    )

    if breach:
        if open_ev is None:
            # cooldown：近期 recovered 且在冷却期内 -> 抑制新建
            recent = (
                db.query(AlertEvent)
                .filter(
                    AlertEvent.rule_id == rule.id,
                    AlertEvent.server_id == server.id,
                    AlertEvent.status == "recovered",
                )
                .order_by(desc(AlertEvent.recovered_at))
                .first()
            )
            if (
                recent
                and recent.recovered_at
                and (now - recent.recovered_at).total_seconds() < rule.cooldown_sec
            ):
                return
            ev = AlertEvent(
                rule_id=rule.id,
                server_id=server.id,
                status="pending",
                first_breached_at=now,
                current_value=value,
            )
            db.add(ev)
            db.commit()
        else:
            if open_ev.status == "pending":
                if (now - open_ev.first_breached_at).total_seconds() >= rule.duration_sec:
                    open_ev.status = "firing"
                    open_ev.fired_at = now
                    open_ev.current_value = value
                    db.commit()
                    _notify(rule, server, value, firing=True)
                else:
                    open_ev.current_value = value
                    db.commit()
            elif open_ev.status == "firing":
                open_ev.current_value = value  # 持续期间仅更新值，不重复通知
                db.commit()
    else:
        if open_ev is None:
            return
        if open_ev.status == "pending":
            db.delete(open_ev)  # 越限未达 duration 即恢复 -> 丢弃 pending
            db.commit()
        elif open_ev.status == "firing":
            open_ev.status = "recovered"
            open_ev.recovered_at = now
            db.commit()
            _notify(rule, server, value, firing=False)


def run_alerting_cycle() -> None:
    """执行一轮告警评估（同步）。由 alerting_loop 异步包装调用。"""
    with get_db() as db:
        rules = db.query(AlertRule).filter(AlertRule.enabled == True).all()  # noqa: E712
        for rule in rules:
            if rule.server_id:
                servers = db.query(Server).filter(Server.id == rule.server_id).all()
            else:
                servers = db.query(Server).filter(Server.enabled == True).all()  # noqa: E712
            for server in servers:
                try:
                    _evaluate_rule_for_server(rule, server, db)
                except Exception as e:
                    logger.warning("alert eval error rule=%s server=%s: %s", rule.id, server.id, e)


# === 默认规则 seed（F7, 幂等） ===

DEFAULT_RULES = [
    {"name": "CPU 过高", "metric": "cpu", "value_type": "numeric", "operator": ">", "threshold": "90", "duration_sec": 120},
    {"name": "内存过高", "metric": "memory", "value_type": "numeric", "operator": ">", "threshold": "90", "duration_sec": 120},
    {"name": "磁盘占用过高", "metric": "disk", "value_type": "numeric", "operator": ">", "threshold": "85", "duration_sec": 600},
    # H1 修正：server_status 实际取值 online/offline/unknown；agent_status 实际取值 running/stopped/not_deployed
    {"name": "服务离线", "metric": "server_status", "value_type": "string", "operator": "!=", "threshold": "online"},
    {"name": "Agent 失联", "metric": "agent_status", "value_type": "string", "operator": "!=", "threshold": "running", "duration_sec": 300},
]


def _seed(db) -> None:
    if db.query(AlertRule).count() == 0:
        for r in DEFAULT_RULES:
            db.add(
                AlertRule(
                    name=r["name"],
                    metric=r["metric"],
                    value_type=r["value_type"],
                    operator=r["operator"],
                    threshold=r["threshold"],
                    duration_sec=r.get("duration_sec", 60),
                    cooldown_sec=300,
                    enabled=True,
                )
            )
        db.commit()
        logger.info("Seeded %d default alert rules", len(DEFAULT_RULES))


def seed_default_rules(db=None) -> None:
    """幂等 seed：仅当 alert_rules 为空时写入默认规则。"""
    if db is None:
        with get_db() as db:
            _seed(db)
    else:
        _seed(db)


# === 数据保留清理（F2, 分批防锁表） ===

# (name, model, days, time_col, is_date)
_RETENTION = [
    ("metric_history", MetricHistory, RETENTION_METRIC_DAYS, "timestamp", False),
    ("network_latency", NetworkLatency, RETENTION_LATENCY_DAYS, "timestamp", False),
    ("network_stats", NetworkStats, RETENTION_STATS_DAYS, "date", True),
    ("audit_logs", AuditLog, 90, "ts", False),                # v3.28 A2 审计日志保留 90 天
    ("daily_reports", DailyReport, 90, "report_date", True),  # v3.28 R1 日报保留 90 天
]


def _delete_batched(db, model, cutoff_dt: datetime, cutoff_date=None, time_col="timestamp") -> int:
    """分批删除早于 cutoff 的行，每批 5000，避免大表一次性 DELETE 锁表。

    time_col: 模型时间列名（默认 timestamp；network_stats 用 date，audit 用 ts，report 用 report_date）
    is_date: 该列为 Date 类型时用日期比较
    """
    is_date_col = time_col == "date" or time_col == "report_date"
    col = getattr(model, time_col)
    threshold = cutoff_date if is_date_col else cutoff_dt
    total = 0
    while True:
        rows = db.query(model).filter(col < threshold).limit(5000).all()
        if not rows:
            break
        for r in rows:
            db.delete(r)
        db.commit()
        total += len(rows)
        if len(rows) < 5000:
            break
        time.sleep(0.1)
    return total


def retention_cleanup(db=None) -> None:
    """清理过期监控数据（分批）。db 为空时自建会话。"""
    own = db is None
    if own:
        db = next(get_db())
    try:
        now = datetime.utcnow()
        for name, model, days, time_col, is_date in _RETENTION:
            if days <= 0:
                continue
            cutoff_dt = now - timedelta(days=days)
            cutoff_date = (now - timedelta(days=days)).date()
            deleted = _delete_batched(db, model, cutoff_dt, cutoff_date, time_col=time_col)
            logger.info("retention: %s removed %d rows (keep %d days)", name, deleted, days)
    finally:
        if own:
            db.close()


# === 后台异步循环 ===

async def alerting_loop() -> None:
    """后台任务：每 60s 一轮告警评估。ALERTING_ENABLED=false 时跳过（回滚兜底）。"""
    while True:
        await asyncio.sleep(60)
        if not ALERTING_ENABLED:
            continue
        try:
            run_alerting_cycle()
        except Exception as e:
            logger.exception("alerting cycle error: %s", e)


async def retention_loop() -> None:
    """后台任务：每天 01:00 执行一次数据保留清理。"""
    while True:
        now = datetime.utcnow()
        nxt = now.replace(hour=1, minute=0, second=0, microsecond=0)
        if now >= nxt:
            nxt = nxt + timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            retention_cleanup()
        except Exception as e:
            logger.exception("retention error: %s", e)
