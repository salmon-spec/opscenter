"""服务健康检查与告警（v3.29, T4）。

在现有告警引擎（服务器/资源维度）之外补充服务粒度健康检查：
- 轮询 services 表中启用的服务（url + health_path），HTTP 探测
- 连续失败 N 次（防抖动）触发飞书告警，恢复后自动发送恢复通知
- 健康状态内存保存（进程内），提供 get_health_snapshot 供 /api/v2/services/health 使用

设计取舍：不新增表（T1 模型已定稿），间隔/阈值走环境变量，状态进程内存；
如需持久化历史可后续叠加 service_uptime 表。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

from app.config import DEFAULT_NOTIFY_WEBHOOKS
from app.database import get_db
from app.models import Server, Service

logger = logging.getLogger("opscenter.service_health")

# 环境变量配置（默认：60s 探测一次，连续 3 次失败告警）
SERVICE_HEALTH_INTERVAL_SEC = int(os.getenv("SERVICE_HEALTH_INTERVAL_SEC", "60"))
SERVICE_HEALTH_FAIL_THRESHOLD = int(os.getenv("SERVICE_HEALTH_FAIL_THRESHOLD", "3"))
SERVICE_HEALTH_TIMEOUT_SEC = float(os.getenv("SERVICE_HEALTH_TIMEOUT_SEC", "5"))
SERVICE_HEALTH_ENABLED = os.getenv("SERVICE_HEALTH_ENABLED", "true").strip().lower() in ("1", "true", "yes")

# 健康状态：service_id(str) -> {fail_count, notified, last_ok, last_fail, last_error}
_health_state: Dict[str, dict] = {}


def _build_check_url(svc: Service) -> Optional[str]:
    """构造探测 URL：health_path 优先（含 http 则直接用），否则用服务 url。"""
    if svc.health_path:
        hp = svc.health_path.strip()
        if hp.lower().startswith(("http://", "https://")):
            return hp
        return svc.url.rstrip("/") + "/" + hp.lstrip("/")
    return svc.url or None


def _check_service(svc: Service) -> Tuple[bool, str]:
    """HTTP 探测单个服务，返回 (是否健康, 错误信息)。"""
    url = _build_check_url(svc)
    if not url:
        return True, "无探测地址"
    try:
        resp = requests.get(url, timeout=SERVICE_HEALTH_TIMEOUT_SEC, allow_redirects=True)
        # 2xx/3xx 视为健康；健康路径显式返回 200 的按 200 处理，其余 3xx 也放行
        if resp.status_code < 400:
            return True, ""
        return False, f"HTTP {resp.status_code}"
    except requests.RequestException as e:
        return False, str(e)[:120]


def _notify_webhook(title: str, color: str, elements: list) -> None:
    """发送飞书交互卡片到全局 webhook（复用现有通知格式）。"""
    webhooks = DEFAULT_NOTIFY_WEBHOOKS
    if not webhooks:
        return
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
            "elements": elements,
        },
    }
    for url in webhooks:
        try:
            requests.post(url, json=card, timeout=5)
        except requests.RequestException as e:
            logger.warning("webhook 发送失败: %s", e)


def _fire_alert(svc: Service, server: Optional[Server], err: str) -> None:
    """触发告警（飞书 + 状态标记），连续失败达到阈值时仅通知一次。"""
    st = _health_state.setdefault(str(svc.id), {"fail_count": 0, "notified": False})
    st["fail_count"] += 1
    st["last_fail"] = time.time()
    st["last_error"] = err
    if st["fail_count"] >= SERVICE_HEALTH_FAIL_THRESHOLD and not st["notified"]:
        st["notified"] = True
        loc = server.name if server else svc.server_id
        _notify_webhook(
            f"🔴 [服务告警] {svc.name} — {loc}",
            "red",
            [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**服务**：{svc.name}\n**地址**：{svc.url}\n**连续失败**：{st['fail_count']} 次\n**原因**：{err}"}},
                {"tag": "hr"},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": f"OpsCenter 服务健康检查 · {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"}]},
            ],
        )
        logger.warning("服务告警触发: %s (%s)", svc.name, err)


def _fire_recovery(svc: Service, server: Optional[Server]) -> None:
    """服务恢复通知。"""
    st = _health_state.setdefault(str(svc.id), {"fail_count": 0, "notified": False})
    st["notified"] = False
    st["last_ok"] = time.time()
    loc = server.name if server else svc.server_id
    _notify_webhook(
        f"✅ [服务恢复] {svc.name} — {loc}",
        "green",
        [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**服务**：{svc.name}\n**地址**：{svc.url}\n状态已恢复正常"}},
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": f"OpsCenter 服务健康检查 · {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"}]},
        ],
    )
    logger.info("服务恢复: %s", svc.name)


def run_service_health_cycle() -> int:
    """单轮健康检查：遍历启用服务探测，返回检查数量。"""
    checked = 0
    with get_db() as db:
        services = db.query(Service).filter(Service.hidden == False).all()  # noqa: E712
        server_map = {s.id: s for s in db.query(Server).all()}
        for svc in services:
            ok, err = _check_service(svc)
            st = _health_state.setdefault(str(svc.id), {"fail_count": 0, "notified": False})
            server = server_map.get(svc.server_id)
            if ok:
                st["fail_count"] = 0
                st["last_ok"] = time.time()
                st["last_error"] = ""
                if st["notified"]:
                    _fire_recovery(svc, server)
            else:
                _fire_alert(svc, server, err)
            checked += 1
    return checked


async def service_health_loop() -> None:
    """后台循环：启动后每 SERVICE_HEALTH_INTERVAL_SEC 执行一轮健康检查。"""
    if not SERVICE_HEALTH_ENABLED:
        logger.info("服务健康检查已禁用（SERVICE_HEALTH_ENABLED=false）")
        return
    # 等待应用启动完成后再开始，避免与 startup 抢占数据库
    await asyncio.sleep(30)
    while True:
        try:
            n = await asyncio.to_thread(run_service_health_cycle)
            logger.info("服务健康检查完成，共检查 %d 个服务", n)
        except Exception as e:
            logger.error("服务健康检查异常: %s", e)
        await asyncio.sleep(SERVICE_HEALTH_INTERVAL_SEC)


def get_health_snapshot() -> List[dict]:
    """返回全量服务健康快照（供 /api/v2/services/health 与前端大屏使用）。"""
    with get_db() as db:
        services = db.query(Service).filter(Service.hidden == False).all()  # noqa: E712
        server_map = {s.id: s for s in db.query(Server).all()}
        out = []
        for svc in services:
            st = _health_state.get(str(svc.id), {"fail_count": 0, "notified": False})
            server = server_map.get(svc.server_id)
            status = "unknown"
            if st.get("notified"):
                status = "down"
            elif st.get("fail_count", 0) > 0:
                status = "degraded"
            else:
                status = "up"
            out.append({
                "service_id": str(svc.id),
                "name": svc.name,
                "url": svc.url,
                "status": status,
                "fail_count": st.get("fail_count", 0),
                "last_ok": st.get("last_ok"),
                "last_fail": st.get("last_fail"),
                "last_error": st.get("last_error", ""),
                "server_name": server.name if server else None,
                "server_host": server.host if server else None,
            })
        return out
