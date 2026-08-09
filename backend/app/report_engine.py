"""OpsCenter 巡检日报引擎（v3.28, R1/R2）。

设计：复用 v3.27 全部采集器落库的数据（servers/alert_events/cert_checks/
log_matches/backup_checks/image_status/services），零新增采集。
每日按 REPORT_HOUR_UTC 生成当日报告，存 daily_reports 表 + 推送 webhook。
REPORT_ENABLED=false 一键关停（回滚兜底）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from app.config import REPORT_ENABLED, REPORT_HOUR_UTC
from app.database import get_db
from app.models import (
    AlertEvent, AlertRule, BackupCheck, CertCheck, DailyReport, ImageStatus,
    LogMatch, LogRule, MetricHistory, Server, Service,
)

logger = logging.getLogger("opscenter.report")


def _servers_section(db):
    """服务器状态聚合。"""
    servers = db.query(Server).filter(Server.enabled == True).all()  # noqa: E712
    rows = []
    online = 0
    for s in servers:
        is_up = s.agent_status == "running"
        if is_up:
            online += 1
        rows.append(f"- {s.name}：{'✅ 在线' if is_up else '⚠️ ' + (s.agent_status or '未知')}（agent {s.agent_version or '-'}）")
    return {
        "total": len(servers), "online": online,
        "markdown": "\n".join(rows) if rows else "- 无服务器",
    }


def _alerts_section(db):
    """告警聚合：当前活跃 + 昨日发生/恢复。"""
    firing = db.query(AlertEvent).filter(AlertEvent.status == "firing").count()
    yesterday = datetime.utcnow() - timedelta(days=1)
    fired = db.query(AlertEvent).filter(
        AlertEvent.created_at >= yesterday).count()
    recovered = db.query(AlertEvent).filter(
        AlertEvent.status == "recovered", AlertEvent.recovered_at >= yesterday).count()
    # 活跃事件明细
    active = db.query(AlertEvent).filter(AlertEvent.status == "firing").all()
    lines = []
    for ev in active:
        rule = db.query(AlertRule).filter(AlertRule.id == ev.rule_id).first() if ev.rule_id else None
        srv = db.query(Server).filter(Server.id == ev.server_id).first() if ev.server_id else None
        lines.append(f"- {rule.name if rule else ev.rule_id} @ {srv.name if srv else '-'}（{ev.current_value}）")
    return {
        "firing": firing, "fired_yesterday": fired, "recovered_yesterday": recovered,
        "markdown": "\n".join(lines) if lines else "- 无活跃告警",
    }


def _certs_section(db):
    """证书聚合：总数 + 30 天内到期 + 已过期。"""
    checks = db.query(CertCheck).filter(CertCheck.enabled == True).all()  # noqa: E712
    expiring = [c for c in checks if c.days_left is not None and 0 < c.days_left <= 30]
    expired = [c for c in checks if c.days_left is not None and c.days_left <= 0]
    lines = []
    for c in sorted(expiring + expired, key=lambda x: x.days_left or 0)[:5]:
        lines.append(f"- {c.domain}：剩余 {c.days_left} 天（{c.not_after.date() if c.not_after else '-'}）")
    return {
        "total": len(checks), "expiring_30d": len(expiring), "expired": len(expired),
        "markdown": "\n".join(lines) if lines else "- 无到期风险",
    }


def _logs_section(db):
    """日志聚合：昨日命中规则 TOP。"""
    yesterday = datetime.utcnow() - timedelta(days=1)
    rules = db.query(LogRule).filter(LogRule.enabled == True).all()  # noqa: E712
    stats = []
    total = 0
    for r in rules:
        cnt = db.query(LogMatch).filter(
            LogMatch.rule_id == r.id, LogMatch.matched_at >= yesterday).count()
        total += cnt
        if cnt:
            srv = db.query(Server).filter(Server.id == r.server_id).first()
            stats.append((cnt, r.name, srv.name if srv else '-'))
    stats.sort(reverse=True)
    lines = [f"- {name} @ {srv}：命中 {cnt} 条" for cnt, name, srv in stats[:5]]
    return {
        "matched_rules": len(stats), "total_matches": total,
        "top_rule": stats[0][1] if stats else None,
        "markdown": "\n".join(lines) if lines else "- 昨日无命中",
    }


def _backups_section(db):
    """备份聚合：检查项数 + 超期（backup_age > 24h）。"""
    checks = db.query(BackupCheck).filter(BackupCheck.enabled == True).all()  # noqa: E712
    stale = []
    for chk in checks:
        # 读该服务器最新 backup_age 指标
        latest = db.query(MetricHistory).filter(
            MetricHistory.server_id == chk.server_id,
            MetricHistory.metric == "backup_age",
        ).order_by(MetricHistory.timestamp.desc()).first()
        if latest and latest.value > chk.expected_interval_hours:
            stale.append((chk.name, latest.value))
    lines = [f"- {name}：已 {v:.1f}h 未更新" for name, v in stale[:5]]
    return {
        "total": len(checks), "stale": len(stale),
        "markdown": "\n".join(lines) if lines else "- 备份正常",
    }


def _images_section(db):
    """镜像聚合：容器数 + 落后数。"""
    images = db.query(ImageStatus).all()
    outdated = [i for i in images if i.outdated]
    lines = [f"- {i.container_name}（{i.image}）" for i in outdated[:5]]
    return {
        "total": len(images), "outdated": len(outdated),
        "markdown": "\n".join(lines) if lines else "- 无落后镜像",
    }


def _services_section(db):
    """服务聚合：up/down 统计。"""
    svcs = db.query(Service).filter(Service.hidden == False).all()  # noqa: E712
    up = sum(1 for s in svcs if s.status == "up")
    down = [s for s in svcs if s.status == "down"]
    lines = []
    for s in down[:5]:
        srv = db.query(Server).filter(Server.id == s.server_id).first()
        lines.append(f"- {s.name} @ {srv.name if srv else '-'}")
    return {
        "total": len(svcs), "up": up, "down": len(down),
        "markdown": "\n".join(lines) if lines else "- 无宕机服务",
    }


def _build_markdown(sec) -> str:
    d = date.today()
    return f"""# 📊 OpsCenter 巡检日报 {d}

> 生成时间：{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC

## 服务器（{sec['servers']['online']}/{sec['servers']['total']} 在线）

{sec['servers']['markdown']}

## 告警

- 当前活跃：**{sec['alerts']['firing']}** | 昨日发生：{sec['alerts']['fired_yesterday']} | 昨日恢复：{sec['alerts']['recovered_yesterday']}

{sec['alerts']['markdown']}

## 证书（{sec['certs']['expiring_30d']} 即将到期 / {sec['certs']['expired']} 已过期）

{sec['certs']['markdown']}

## 日志异常（昨日命中 {sec['logs']['total_matches']} 条 / {sec['logs']['matched_rules']} 规则）

{sec['logs']['markdown']}

## 备份（{sec['backups']['stale']} 项超期 / {sec['backups']['total']} 项）

{sec['backups']['markdown']}

## 镜像（{sec['images']['outdated']} 项落后 / {sec['images']['total']} 容器）

{sec['images']['markdown']}

## 服务（{sec['services']['up']}/{sec['services']['total']} 在线）

{sec['services']['markdown']}

---
OpsCenter v3.28 · 每日 {REPORT_HOUR_UTC + 8}:00 自动生成
"""


def _notify_report(summary: dict) -> None:
    """推送日报到全局 webhook（复用 DEFAULT_NOTIFY_WEBHOOKS；为空跳过）。"""
    import requests
    from app.alerting import DEFAULT_NOTIFY_WEBHOOKS
    if not DEFAULT_NOTIFY_WEBHOOKS:
        return
    srv, al, cert = summary["servers"], summary["alerts"], summary["certs"]
    logs, bkp, img, svc = summary["logs"], summary["backups"], summary["images"], summary["services"]
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": f"📊 OpsCenter 巡检日报 {date.today()}"}, "template": "blue"},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": (
                    f"**服务器**：{srv['online']}/{srv['total']} 在线\n"
                    f"**告警**：活跃 {al['firing']} | 昨日发生 {al['fired_yesterday']} | 恢复 {al['recovered_yesterday']}\n"
                    f"**证书**：{cert['total']} 项，{cert['expiring_30d']} 项 30 天内到期，{cert['expired']} 项已过期\n"
                    f"**日志**：昨日 {logs['total_matches']} 条命中 / {logs['matched_rules']} 规则\n"
                    f"**备份**：{bkp['total']} 项，{bkp['stale']} 项超期\n"
                    f"**镜像**：{img['total']} 容器，{img['outdated']} 项落后\n"
                    f"**服务**：{svc['up']}/{svc['total']} 在线，{svc['down']} 项宕机\n"
                )}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"*生成时间 {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC · 详情见 OpsCenter 状态页*"}},
            ],
        },
    }
    for url in DEFAULT_NOTIFY_WEBHOOKS:
        try:
            requests.post(url, json=card, timeout=5)
            logger.info("report pushed to webhook")
        except Exception as e:
            logger.warning("report push failed: %s", e)


def generate_report(db=None) -> dict:
    """生成当日日报（幂等：同日期存在则覆盖更新）。返回 report 摘要。"""
    if not REPORT_ENABLED:
        return {"error": "REPORT_ENABLED=false"}

    def _do(sess):
        sec = {
            "servers": _servers_section(sess),
            "alerts": _alerts_section(sess),
            "certs": _certs_section(sess),
            "logs": _logs_section(sess),
            "backups": _backups_section(sess),
            "images": _images_section(sess),
            "services": _services_section(sess),
        }
        summary = {k: {kk: vv for kk, vv in v.items() if kk != "markdown"}
                   for k, v in sec.items()}
        content = _build_markdown(sec)
        today = date.today()
        report = sess.query(DailyReport).filter(DailyReport.report_date == today).first()
        if report:
            report.summary = summary
            report.content = content
            report.title = f"OpsCenter 巡检日报 {today}"
        else:
            report = DailyReport(
                report_date=today,
                title=f"OpsCenter 巡检日报 {today}",
                summary=summary,
                content=content,
            )
            sess.add(report)
        sess.commit()
        sess.refresh(report)
        logger.info("report generated for %s", today)
        _notify_report(summary)
        return {"id": str(report.id), "report_date": today.isoformat(), "summary": summary}

    if db is not None:
        return _do(db)
    with get_db() as sess:
        return _do(sess)


async def report_loop() -> None:
    """后台任务：每日 REPORT_HOUR_UTC 生成日报（启动延迟 150s 等引擎就绪）。"""
    await asyncio.sleep(150)
    while True:
        now = datetime.utcnow()
        nxt = now.replace(hour=REPORT_HOUR_UTC, minute=0, second=0, microsecond=0)
        if now >= nxt:
            nxt = nxt.replace(day=nxt.day + 1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            generate_report()
        except Exception as e:
            logger.exception("report loop error: %s", e)
