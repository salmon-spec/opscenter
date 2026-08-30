"""OpsCenter SSL 证书监控（v3.27, D1）。

设计：探测从后端直接发起（目标为公网域名），不依赖 Agent 版本。
- cert_checks 表存域名配置 + 最近探测结果
- 探测结果同时写 metric_history(metric='cert_days_left') 供告警引擎复用
- CERT_SCAN_ENABLED=false 一键关停（回滚兜底）
"""
from __future__ import annotations

import asyncio
import logging
import socket
import ssl
from datetime import datetime, timedelta

from app.config import CERT_SCAN_ENABLED, CERT_SCAN_INTERVAL_HOURS
from app.database import get_db
from app.models import CertCheck, MetricHistory

logger = logging.getLogger("opscenter.cert")

DEFAULT_CERT_RULE = {
    "name": "证书即将过期",
    "metric": "cert_days_left",
    "value_type": "numeric",
    "operator": "<",
    "threshold": "30",
    "duration_sec": 0,
    "cooldown_sec": 86400,  # 证书状态稳定，冷却 24h
    "enabled": True,
}


def check_certificate(domain: str, port: int = 443, timeout: float = 6.0):
    """探测单个域名的证书剩余天数。返回 (days_left, not_after, issuer) 或抛异常。"""
    ctx = ssl.create_default_context()
    # 强制 TLSv1.2：VM2 出站公网 443 的中间设备拦截 TLS1.3 ClientHello（TLSv1.2 正常，系统 curl 亦如此）
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    # 强制 IPv4（VM2 有 IPv6 地址但出站不通，避免 IPv6 优先导致握手超时）
    with socket.create_connection((socket.gethostbyname(domain), port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=domain) as tls:
            cert = tls.getpeercert()
    not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=None)
    days_left = int((not_after - datetime.utcnow()).total_seconds() / 86400)
    issuer = ""
    try:
        issuer = cert.get("issuer", ())
        cn = [v for k, v in issuer if k == "commonName"]
        issuer = cn[0] if cn else ""
    except Exception:
        issuer = ""
    return days_left, not_after, issuer


def run_cert_scan() -> None:
    """扫描一轮所有启用的证书检查项，更新 cert_checks + metric_history。"""
    if not CERT_SCAN_ENABLED:
        return
    with get_db() as db:
        checks = db.query(CertCheck).filter(CertCheck.enabled == True).all()  # noqa: E712
        for chk in checks:
            try:
                days_left, not_after, issuer = check_certificate(chk.domain, chk.port)
                chk.days_left = days_left
                chk.not_after = not_after
                chk.issuer = issuer
                chk.last_error = None
                logger.info("cert %s:%s days_left=%d", chk.domain, chk.port, days_left)
            except Exception as e:
                chk.last_error = str(e)[:250]
                chk.days_left = None
                logger.warning("cert check failed %s:%s: %s", chk.domain, chk.port, e)
            chk.updated_at = datetime.utcnow()
            db.commit()
            # 写指标供告警引擎（成功才写；失败置 None 由 last_error 表达）
            if chk.days_left is not None and chk.server_id:
                db.add(MetricHistory(
                    server_id=chk.server_id,
                    metric="cert_days_left",
                    value=float(chk.days_left),
                ))
                db.commit()


def seed_cert_rule(db=None) -> None:
    """幂等 seed 证书过期规则（alert_rules 无同名时插入）。"""
    from app.models import AlertRule

    def _do(sess):
        exists = sess.query(AlertRule).filter(AlertRule.metric == "cert_days_left").first()
        if exists:
            return False
        sess.add(AlertRule(**DEFAULT_CERT_RULE))
        sess.commit()
        return True

    if db is not None:
        return _do(db)
    with get_db() as sess:
        return _do(sess)


async def cert_scan_loop() -> None:
    """后台任务：每 CERT_SCAN_INTERVAL_HOURS 小时扫一轮（首轮延迟 60s 等引擎就绪）。"""
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(run_cert_scan)
        except Exception as e:
            logger.exception("cert scan error: %s", e)
        await asyncio.sleep(CERT_SCAN_INTERVAL_HOURS * 3600)
