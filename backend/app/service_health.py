"""Unified service health checks and alert state management."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

from app.config import DEFAULT_NOTIFY_WEBHOOKS
from app.database import get_db
from app.models import Server, Service, ServiceProbeResult, ServiceStatus

logger = logging.getLogger("opscenter.service_health")

SERVICE_HEALTH_INTERVAL_SEC = int(os.getenv("SERVICE_HEALTH_INTERVAL_SEC", "60"))
SERVICE_HEALTH_FAIL_THRESHOLD = int(os.getenv("SERVICE_HEALTH_FAIL_THRESHOLD", "3"))
SERVICE_HEALTH_TIMEOUT_SEC = float(os.getenv("SERVICE_HEALTH_TIMEOUT_SEC", "5"))
SERVICE_HEALTH_MAX_WORKERS = int(os.getenv("SERVICE_HEALTH_MAX_WORKERS", "8"))
SERVICE_HEALTH_PROBE_UNCONFIGURED = os.getenv(
    "SERVICE_HEALTH_PROBE_UNCONFIGURED", "false"
).strip().lower() in ("1", "true", "yes")
SERVICE_HEALTH_ENABLED = os.getenv("SERVICE_HEALTH_ENABLED", "true").strip().lower() in (
    "1", "true", "yes",
)

_health_state: Dict[str, dict] = {}
_state_lock = threading.Lock()
_cycle_lock = threading.Lock()


def _build_check_url(svc) -> Optional[str]:
    """Build an HTTP health URL. Placeholder and non-HTTP URLs are skipped."""
    url = (getattr(svc, "url", None) or "").strip()
    health_path = (getattr(svc, "health_path", None) or "").strip()
    if health_path.lower().startswith(("http://", "https://")):
        return health_path
    if not url or url.startswith("#") or not url.lower().startswith(("http://", "https://")):
        return None
    if health_path:
        return url.rstrip("/") + "/" + health_path.lstrip("/")
    # Auto-discovery frequently assigns HTTP-looking URLs to SSH, Redis,
    # PostgreSQL and gRPC ports. They are not authoritative probe targets.
    return url if SERVICE_HEALTH_PROBE_UNCONFIGURED else None


def _check_service(svc) -> Tuple[Optional[bool], str, Optional[int], Optional[float]]:
    """Probe one service; None means it has no supported HTTP endpoint."""
    url = _build_check_url(svc)
    if not url:
        return None, "无 HTTP 探测地址", None, None
    started = time.monotonic()
    verify_tls = True
    try:
        address = ipaddress.ip_address(urlsplit(url).hostname or "")
        verify_tls = not (address.is_private or address.is_loopback or address.is_link_local)
    except ValueError:
        pass
    try:
        response = requests.get(
            url,
            timeout=SERVICE_HEALTH_TIMEOUT_SEC,
            allow_redirects=True,
            verify=verify_tls,
            headers={"User-Agent": "OpsCenter-HealthCheck/3.30"},
        )
        if response.status_code < 400 or response.status_code in (401, 403):
            return True, "", response.status_code, round((time.monotonic() - started) * 1000, 1)
        return False, f"HTTP {response.status_code}", response.status_code, round((time.monotonic() - started) * 1000, 1)
    except requests.RequestException as exc:
        return False, str(exc)[:120], None, round((time.monotonic() - started) * 1000, 1)


def _notify_webhook(title: str, color: str, elements: list) -> None:
    if not DEFAULT_NOTIFY_WEBHOOKS:
        return
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
            "elements": elements,
        },
    }
    for url in DEFAULT_NOTIFY_WEBHOOKS:
        try:
            requests.post(url, json=card, timeout=5)
        except requests.RequestException as exc:
            logger.warning("webhook 发送失败: %s", exc)


def _fire_alert(svc, server, err: str, fail_count: int) -> None:
    loc = server.name if server else getattr(svc, "server_id", "")
    _notify_webhook(
        f"🔴 [服务告警] {svc.name} — {loc}",
        "red",
        [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**服务**：{svc.name}\n**地址**：{svc.url}\n**连续失败**：{fail_count} 次\n**原因**：{err}"}},
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": f"OpsCenter 服务健康检查 · {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"}]},
        ],
    )
    logger.warning("服务告警触发: %s (%s)", svc.name, err)


def _fire_recovery(svc, server) -> None:
    loc = server.name if server else getattr(svc, "server_id", "")
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


def _snapshot_targets():
    """Read scalar target data and close the DB session before network I/O."""
    with get_db() as db:
        # Manual services shown in the plaza are owned by plaza_health_loop.
        # Excluding them here prevents duplicate probes, history and alerts.
        from app.plaza import plaza_owned_service_ids
        owned_ids = plaza_owned_service_ids(db)
        services = [
            SimpleNamespace(
                id=svc.id, name=svc.name, url=svc.url, health_path=svc.health_path,
                server_id=svc.server_id, status=svc.status,
            )
            for svc in db.query(Service).filter(Service.hidden == False).all()  # noqa: E712
            if svc.id not in owned_ids
        ]
        servers = {
            srv.id: SimpleNamespace(id=srv.id, name=srv.name, host=srv.host)
            for srv in db.query(Server).all()
        }
    return services, servers


def run_service_health_cycle() -> int:
    """Run one concurrent cycle without holding a DB connection during I/O."""
    if not _cycle_lock.acquire(blocking=False):
        logger.info("服务健康检查仍在运行，跳过重叠周期")
        return 0
    try:
        services, server_map = _snapshot_targets()
        probe_results = {}
        workers = max(1, min(SERVICE_HEALTH_MAX_WORKERS, len(services) or 1))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="service-health") as executor:
            future_map = {executor.submit(_check_service, svc): svc for svc in services}
            for future in as_completed(future_map):
                svc = future_map[future]
                try:
                    raw_result = future.result()
                    if len(raw_result) == 2:  # compatibility with injected/custom probes
                        probe_results[str(svc.id)] = (*raw_result, None, None)
                    else:
                        probe_results[str(svc.id)] = raw_result
                except Exception as exc:
                    probe_results[str(svc.id)] = (False, str(exc)[:120], None, None)

        status_updates = {}
        notifications = []
        now = time.time()
        with _state_lock:
            for svc in services:
                ok, err, http_status, latency_ms = probe_results[str(svc.id)]
                if ok is None:
                    if svc.status != ServiceStatus.unknown.value:
                        status_updates[svc.id] = ServiceStatus.unknown.value
                    continue
                state = _health_state.setdefault(str(svc.id), {"fail_count": 0, "notified": False})
                if ok:
                    was_notified = state.get("notified", False)
                    state.update(fail_count=0, notified=False, last_ok=now, last_error="")
                    status_updates[svc.id] = ServiceStatus.up.value
                    if was_notified:
                        notifications.append(("recovery", svc, server_map.get(svc.server_id), "", 0))
                else:
                    state["fail_count"] = state.get("fail_count", 0) + 1
                    state.update(last_fail=now, last_error=err)
                    if state["fail_count"] >= SERVICE_HEALTH_FAIL_THRESHOLD:
                        status_updates[svc.id] = ServiceStatus.down.value
                        if not state.get("notified", False):
                            state["notified"] = True
                            notifications.append(("alert", svc, server_map.get(svc.server_id), err, state["fail_count"]))
                    elif not svc.status:
                        status_updates[svc.id] = ServiceStatus.unknown.value

        if status_updates or any(result[0] is not None for result in probe_results.values()):
            with get_db() as db:
                for service_id, status in status_updates.items():
                    db.query(Service).filter(Service.id == service_id).update({Service.status: status})
                for svc in services:
                    ok, err, http_status, latency_ms = probe_results[str(svc.id)]
                    if ok is None:
                        continue
                    db.add(ServiceProbeResult(
                        service_id=svc.id,
                        status="up" if ok else "down",
                        http_status=http_status,
                        latency_ms=latency_ms,
                        error=err or None,
                        probe_url=_build_check_url(svc),
                    ))
                db.commit()

        for kind, svc, server, err, fail_count in notifications:
            if kind == "alert":
                _fire_alert(svc, server, err, fail_count)
            else:
                _fire_recovery(svc, server)
        return sum(1 for result in probe_results.values() if result[0] is not None)
    finally:
        _cycle_lock.release()


async def service_health_loop() -> None:
    if not SERVICE_HEALTH_ENABLED:
        logger.info("服务健康检查已禁用（SERVICE_HEALTH_ENABLED=false）")
        return
    await asyncio.sleep(30)
    while True:
        try:
            checked = await asyncio.to_thread(run_service_health_cycle)
            logger.info("服务健康检查完成，共检查 %d 个服务", checked)
        except Exception as exc:
            logger.exception("服务健康检查异常: %s", exc)
        await asyncio.sleep(SERVICE_HEALTH_INTERVAL_SEC)


def get_health_snapshot() -> List[dict]:
    services, server_map = _snapshot_targets()
    output = []
    with _state_lock:
        states = {key: value.copy() for key, value in _health_state.items()}
    for svc in services:
        state = states.get(str(svc.id), {})
        if not state.get("last_ok") and not state.get("last_fail"):
            status = "unknown"
        elif state.get("notified"):
            status = "down"
        elif state.get("fail_count", 0) > 0:
            status = "degraded"
        else:
            status = "up"
        server = server_map.get(svc.server_id)
        output.append({
            "service_id": str(svc.id), "name": svc.name, "url": svc.url,
            "status": status, "fail_count": state.get("fail_count", 0),
            "last_ok": state.get("last_ok"), "last_fail": state.get("last_fail"),
            "last_error": state.get("last_error", ""),
            "server_name": server.name if server else None,
            "server_host": server.host if server else None,
        })
    return output
