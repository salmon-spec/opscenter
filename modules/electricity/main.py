"""Electricity monitoring module (国家电网电费监测) — v0.5.0.

Owns its collection loop, its own data volume, its own alert engine (rules +
state machine + multi-channel notification), long-term histories, usage
forecasting and its own module contract. It never touches another module's
storage and only appears to the workbench as `/modules/electricity/*` through
the shared gateway.
"""
from __future__ import annotations

import asyncio
import csv
import io
import uuid
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, time, timedelta, timezone
from typing import Any

from aiohttp import ClientSession
from fastapi import Body, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

import alerts as alerting
import notify
from collector import collect, describe_error, probe, relogin_count
from page import render_page  # merged page: tabs [电费监测 | 通知设置]
from runtime.app import create_module_app
from storage import ElectricityStore

VERSION = "0.7.0"
KEY = "electricity"
REFRESH_HOURS = float(os.getenv("ELECTRICITY_REFRESH_HOURS", "12"))
ALERT_INTERVAL_MINUTES = float(os.getenv("ELECTRICITY_ALERT_INTERVAL_MINUTES", "30"))

store = ElectricityStore()
_collect_lock = asyncio.Lock()
_alert_lock = asyncio.Lock()
_status = {"message": "等待首次采集"}
_alert_status: dict[str, Any] = {"message": "尚未评估", "evaluated_at": None, "last_delivery": []}


def _cfg() -> dict:
    return alerting.config()


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _job_start_time() -> time | None:
    raw = _env("ELECTRICITY_JOB_START_TIME")
    if not raw:
        return None
    try:
        return time.fromisoformat(raw)
    except ValueError:
        logging.warning("electricity: ELECTRICITY_JOB_START_TIME 非法（应为 HH:MM），已忽略")
        return None


def _collect_window() -> tuple[time, time] | None:
    return alerting.parse_quiet_hours(_env("ELECTRICITY_COLLECT_WINDOW"))


def _in_collect_window(now: datetime) -> bool:
    window = _collect_window()
    if not window:
        return True
    start, end = window
    local = now.astimezone(alerting.TZ).time()
    if start <= end:
        return start <= local <= end
    return local >= start or local <= end


def _load_context_snapshot() -> dict:
    """Snapshot decorated with the histories the rule engine needs."""
    snapshot = store.snapshot()
    state = store.alert_state()
    snapshot["_balance_history"] = state.get("balance_history") or []
    snapshot["_daily_history"] = store.daily_history(120)
    snapshot["_monthly_history"] = store.monthly_history(24)
    return snapshot


def _current_context(now: datetime) -> dict:
    return alerting.build_context(_load_context_snapshot(), store.configured(), now, _cfg())


# --------------------------------------------------------------------------- #
# module contract providers
# --------------------------------------------------------------------------- #
def summary_provider():
    now = datetime.now(timezone.utc)
    cfg = _cfg()
    stale_after = cfg["stale_after_seconds"]
    if not store.configured():
        return {"status": "unavailable", "collected_at": None, "stale_after_seconds": stale_after,
                "data": {"configured": False}, "error": "国网账号或采集户号尚未配置"}
    ctx = _current_context(now)
    if ctx["updated_at"] is None:
        return {"status": "unavailable", "collected_at": None, "stale_after_seconds": stale_after,
                "data": {"configured": True}, "error": _status.get("message") or "尚未完成首次采集"}
    active = alerting.evaluate_rules(ctx, now, cfg)
    state = store.alert_state()
    data = {
        "configured": True,
        "balance": ctx["balance"],
        "arrears": ctx["arrears"],
        "latest_day": (ctx["daily"][0] or {}).get("day") if ctx["daily"] else None,
        "latest_day_kwh": (ctx["daily"][0] or {}).get("kwh") if ctx["daily"] else None,
        "latest_month": (ctx["bills"][0] or {}).get("month") if ctx["bills"] else None,
        "latest_month_cost": (ctx["bills"][0] or {}).get("cost") if ctx["bills"] else None,
        "average_7d_kwh": ctx["average_7d_kwh"],
        "estimated_daily_cost": ctx["estimated_daily_cost"],
        "remaining_days": ctx["remaining_days"],
        "forecast_7d_kwh": ctx["forecast"]["total_kwh"],
        "forecast_7d_cost": (round(ctx["forecast"]["total_kwh"] * ctx["price_per_kwh"], 2)
                             if ctx["forecast"]["total_kwh"] else None),
        # Real tariff-inclusive price derived from year-to-date bill totals.
        "price_per_kwh": ctx["price_per_kwh"],
        "price_source": ctx["price_source"],
        "year_kwh": (ctx["year"] or {}).get("kwh"),
        "year_cost": (ctx["year"] or {}).get("cost"),
        "penalty": ctx["penalty"],
        "prepay": ctx["prepay"],
        "month_cost": ctx["month_cost"],
        "month_cost_estimated": ctx["month_cost_estimated"],
        "month_cost_projection": ctx["month_cost_projection"],
        "monthly_budget": cfg["monthly_budget"] or None,
        "step_stage": (alerting.current_step(ctx["steps"]) or {}).get("stage"),
        "steps_available": bool(ctx["steps"]),
        "tou_available": bool(ctx["tou"]),
        "alerts": len(active),
        "alerts_critical": sum(1 for item in active if item["severity"] == "critical"),
        "notify_channels": len(notify.channels()),
        "silenced": len(state.get("silences") or {}),
        "updated_at": ctx["updated_at"],
        "errors": len(ctx["daily_slice_errors"]),
    }
    if ctx["age_seconds"] is not None and ctx["age_seconds"] > stale_after:
        return {"status": "degraded", "collected_at": ctx["updated_at"], "stale_after_seconds": stale_after,
                "data": data, "error": f"采集结果已超过 {stale_after // 3600} 小时未更新"}
    return {"status": "ok", "collected_at": ctx["updated_at"], "stale_after_seconds": stale_after,
            "data": data, "error": None}


def findings_provider():
    """Same rule engine as the notifier, so the workbench panel never disagrees."""
    now = datetime.now(timezone.utc)
    ctx = _current_context(now)
    url = notify.alert_url() or None
    items = []
    for alert in alerting.evaluate_rules(ctx, now, _cfg()):
        items.append({
            "id": alert["id"],
            "resource_id": alert["resource_id"],
            "severity": alert["severity"],
            "title": alert["title"],
            "summary": alert["summary"],
            "observed_at": alert["observed_at"],
            "url": url,
        })
    return {"status": "degraded" if items else "ok", "collected_at": now, "items": items, "error": None}


# --------------------------------------------------------------------------- #
# event export (v0.4.0)
# --------------------------------------------------------------------------- #
async def _publish_event(payload: dict) -> None:
    url = _env("ELECTRICITY_EVENT_WEBHOOK")
    if not url:
        return
    timeout = float(_env("ELECTRICITY_NOTIFY_TIMEOUT", "10") or 10)
    try:
        async with ClientSession() as session:
            async with session.post(url, json=payload, timeout=timeout) as response:
                if response.status >= 400:
                    logging.warning("electricity event webhook status=%s", response.status)
    except Exception as exc:  # noqa: BLE001 - export must never break collection
        logging.warning("electricity event webhook failed: %s", type(exc).__name__)


# --------------------------------------------------------------------------- #
# alert engine loop
# --------------------------------------------------------------------------- #
async def evaluate_alerts(trigger: str = "schedule") -> dict:
    """One alert evaluation round: state machine + notification + history."""
    now = datetime.now(timezone.utc)
    async with _alert_lock:
        state = store.alert_state()
        snapshot = store.snapshot()
        state = alerting.record_balance(snapshot, state, now)
        snapshot["_balance_history"] = state.get("balance_history") or []
        snapshot["_daily_history"] = store.daily_history(120)
        snapshot["_monthly_history"] = store.monthly_history(24)
        ctx = alerting.build_context(snapshot, store.configured(), now, _cfg())
        result = alerting.evaluate(ctx, state, now, _cfg())

        events = result["events"] + [{"action": "resolved", "alert": alert} for alert in result["resolved"]]
        delivery: list[dict] = []
        if events:
            notify.import_breaker(state.get("channel_health"))
            notify.import_targets(store.notify_targets(enabled_only=True),
                                  store.notify_config().get("settings") or {})
            notify.import_targets(store.notify_targets(enabled_only=True),
                                  store.notify_config().get("settings") or {})
            try:
                delivery = await notify.deliver(events,
                                                extra_lines=[f"当前余额：{ctx['balance']}",
                                                             f"预计可用：{ctx['remaining_days']} 天"]
                                                if ctx["balance"] is not None else None)
            except Exception as exc:  # noqa: BLE001 - notification must never break collection
                logging.warning("electricity notify dispatch failed: %s", type(exc).__name__)
            state["channel_health"] = notify.export_breaker()
            store.append_alert_history([
                {
                    "at": now.isoformat(timespec="seconds"),
                    "trigger": trigger,
                    "action": event.get("action"),
                    "alert_id": (event.get("alert") or {}).get("id"),
                    "severity": (event.get("alert") or {}).get("severity"),
                    "title": (event.get("alert") or {}).get("title"),
                    "channels": [f"{item.get('channel')}:{item.get('name')}" for item in delivery],
                    "delivered": all(item.get("ok") for item in delivery) if delivery else None,
                }
                for event in events
            ])

        state["active"] = result["state"]["active"]
        state["updated_at"] = now.isoformat(timespec="seconds")
        state["silences"] = result["state"]["silences"]
        store.save_alert_state(state)

        _alert_status.update({
            "message": f"活跃告警 {len(result['alerts'])} 条",
            "evaluated_at": now.isoformat(timespec="seconds"),
            "last_trigger": trigger,
            "last_delivery": delivery,
        })
        if events:
            logging.warning("electricity alert events=%s trigger=%s", len(events), trigger)
        return {"alerts": result["alerts"], "events": events, "delivery": delivery}


async def run_collection(trigger: str = "schedule", force: bool = False) -> None:
    """Single-flight collection round; failures only update the status message."""
    if _collect_lock.locked():
        _status["message"] = "已有采集任务在进行"
        return
    if not force and not _in_collect_window(datetime.now(timezone.utc)):
        _status["message"] = f"当前不在采集窗口（{_env('ELECTRICITY_COLLECT_WINDOW')}）"
        logging.warning("electricity collection skipped: outside window")
        return
    snapshot: dict | None = None
    async with _collect_lock:
        _status["message"] = "正在采集"
        try:
            snapshot = await collect(store)
            _status["message"] = "采集完成"
            logging.warning("electricity collection ok (trigger=%s)", trigger)
        except Exception as exc:  # noqa: BLE001 - surfaced through summary/findings
            message = describe_error(exc)
            _status["message"] = message
            logging.warning("electricity collection failed (trigger=%s): %s", trigger, message)

    if snapshot is not None:
        row = (snapshot.get("households") or [{}])[0]
        await _publish_event({
            "source": "electricity", "event": "collection", "trigger": trigger,
            "at": snapshot.get("updated_at"),
            "balance": row.get("balance"), "arrears": row.get("arrears"),
            "latest_day": (row.get("daily") or [{}])[0].get("day") if row.get("daily") else None,
            "latest_day_kwh": (row.get("daily") or [{}])[0].get("kwh") if row.get("daily") else None,
            "errors": snapshot.get("errors") or [],
        })

    # Always evaluate afterwards, so "no data"/"stale" states also get alerted.
    with suppress(Exception):
        await evaluate_alerts(f"collect:{trigger}")


def next_run_delay(now: datetime, hours: float = REFRESH_HOURS) -> float:
    """Seconds until the next scheduled collection.

    With ``ELECTRICITY_JOB_START_TIME`` set the run happens at that wall-clock
    time and 12 hours later (Beijing time), mirroring the community projects;
    otherwise the original fixed-interval behaviour is kept.
    """
    start = _job_start_time()
    if start is None:
        return max(300.0, hours * 3600)
    local = now.astimezone(alerting.TZ)
    candidates = []
    for day_offset in (0, 1, 2):
        base = (local + timedelta(days=day_offset)).replace(
            hour=start.hour, minute=start.minute, second=0, microsecond=0)
        candidates.append(base)
        candidates.append(base + timedelta(hours=12))
    future = [item for item in candidates if item > local]
    if not future:
        return max(300.0, hours * 3600)
    return max(60.0, (min(future) - local).total_seconds())


async def _periodic() -> None:
    if _env("ELECTRICITY_RUN_ON_STARTUP", "true").lower() not in {"0", "false", "no"}:
        # Avoid an immediate extra round when a scheduled one is nearly due.
        if next_run_delay(datetime.now(timezone.utc)) > 1800:
            await run_collection("startup")
    while True:
        await asyncio.sleep(next_run_delay(datetime.now(timezone.utc)))
        await run_collection("schedule")


async def _alert_periodic() -> None:
    """Re-check thresholds between collections (balance burn, stale, arrears)."""
    interval = max(300.0, ALERT_INTERVAL_MINUTES * 60)
    while True:
        await asyncio.sleep(interval)
        with suppress(Exception):
            await evaluate_alerts("interval")


@asynccontextmanager
async def _lifespan(_app):
    tasks = [asyncio.create_task(_periodic()), asyncio.create_task(_alert_periodic())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


app = create_module_app(
    key=KEY, version=VERSION, summary_provider=summary_provider, findings_provider=findings_provider,
    capabilities=["summary:read", "findings:read", "electricity:read", "electricity:alerts",
                  "electricity:diagnostics"],
    readiness_provider=store.ping,
)
app.router.lifespan_context = _lifespan


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.get("/api/v1/overview")
def overview():
    if not store.configured():
        raise HTTPException(503, "国网账号或采集户号尚未配置")
    snapshot = store.snapshot()
    if not snapshot:
        raise HTTPException(503, _status.get("message") or "尚未完成首次采集")
    now = datetime.now(timezone.utc)
    ctx = _current_context(now)
    return {
        "source": KEY,
        "status": _status.get("message"),
        "remaining_days": ctx["remaining_days"],
        "estimated_daily_cost": ctx["estimated_daily_cost"],
        "price_per_kwh": ctx["price_per_kwh"],
        "price_source": ctx["price_source"],
        "forecast": ctx["forecast"],
        "month_cost": ctx["month_cost"],
        "month_cost_projection": ctx["month_cost_projection"],
        "month_label": ctx["month_label"],
        **snapshot,
    }


@app.post("/api/v1/refresh")
async def refresh():
    if not store.configured():
        raise HTTPException(503, "国网账号或采集户号尚未配置")
    if _collect_lock.locked():
        return {"triggered": False, "message": "已有采集任务在进行"}
    asyncio.create_task(run_collection("manual", force=True))
    return {"triggered": True, "message": "已触发采集"}


@app.get("/api/v1/usage")
def usage(history: int = Query(default=120, ge=1, le=730)):
    """Tiered tariff, time-of-use, long-term histories and the 7-day forecast."""
    now = datetime.now(timezone.utc)
    ctx = _current_context(now)
    return {
        "source": KEY,
        "collected_at": ctx["updated_at"].isoformat(timespec="seconds") if ctx["updated_at"] else None,
        "balance": ctx["balance"],
        "steps": ctx["steps"],
        "current_step": alerting.current_step(ctx["steps"]),
        "tou": ctx["tou"],
        "forecast": ctx["forecast"],
        "remaining_days": ctx["remaining_days"],
        "estimated_daily_cost": ctx["estimated_daily_cost"],
        "estimate_source": ctx["estimate_source"],
        "price_per_kwh": ctx["price_per_kwh"],
        "price_source": ctx["price_source"],
        "year": ctx["year"],
        "penalty": ctx["penalty"],
        "prepay": ctx["prepay"],
        "daily_history": store.daily_history(history),
        "monthly_history": store.monthly_history(24),
        "month_cost": ctx["month_cost"],
        "month_cost_estimated": ctx["month_cost_estimated"],
        "month_cost_projection": ctx["month_cost_projection"],
        "month_label": ctx["month_label"],
        "monthly_budget": _cfg()["monthly_budget"] or None,
    }


@app.get("/api/v1/alerts")
def list_alerts(history: int = Query(default=20, ge=0, le=200)):
    """Current alert state machine + recent notification history."""
    now = datetime.now(timezone.utc)
    ctx = _current_context(now)
    active = alerting.evaluate_rules(ctx, now, _cfg())
    state = store.alert_state()
    entries = state.get("active") or {}
    items = []
    for alert in active:
        entry = entries.get(alert["id"]) or {}
        items.append({
            "id": alert["id"],
            "rule": alert["rule"],
            "severity": alert["severity"],
            "title": alert["title"],
            "summary": alert["summary"],
            "observed_at": alert["observed_at"].isoformat(timespec="seconds"),
            "value": alert.get("value"),
            "details": alert.get("details") or {},
            "status": entry.get("status", "firing"),
            "first_seen": entry.get("first_seen"),
            "last_seen": entry.get("last_seen"),
            "notify_count": entry.get("notify_count", 0),
            "last_notified_at": entry.get("last_notified_at"),
            "silenced": alerting.is_silenced(state, alert["id"], now),
        })
    return {
        "source": KEY,
        "evaluated_at": now.isoformat(timespec="seconds"),
        "engine": _alert_status,
        "counts": {
            "total": len(items),
            "critical": sum(1 for item in items if item["severity"] == "critical"),
            "warning": sum(1 for item in items if item["severity"] == "warning"),
            "info": sum(1 for item in items if item["severity"] == "info"),
        },
        "context": {
            "balance": ctx["balance"],
            "arrears": ctx["arrears"],
            "remaining_days": ctx["remaining_days"],
            "estimated_daily_cost": ctx["estimated_daily_cost"],
            "estimate_source": ctx["estimate_source"],
            "average_7d_kwh": ctx["average_7d_kwh"],
            "month_cost": ctx["month_cost"],
            "month_cost_projection": ctx["month_cost_projection"],
            "collected_at": ctx["updated_at"].isoformat(timespec="seconds") if ctx["updated_at"] else None,
            "balance_history_points": ctx["balance_history_points"],
            "relogin_count": ctx["relogin_count"],
        },
        "silences": state.get("silences") or {},
        "channel_health": state.get("channel_health") or {},
        "channels": [{"type": item.get("type"), "name": item.get("name"), "min_severity": item.get("min_severity", "warning")}
                     for item in notify.destinations()],
        "items": items,
        "history": store.alert_history(history),
    }


@app.get("/api/v1/alerts/rules")
def list_rules():
    document = alerting.rules_documentation(_cfg())
    document["channel_count"] = len(notify.destinations())
    document["collect_window"] = _env("ELECTRICITY_COLLECT_WINDOW") or "未限制"
    document["job_start_time"] = _env("ELECTRICITY_JOB_START_TIME") or "未启用（按固定间隔）"
    document["alert_url"] = notify.alert_url() or None
    return document


@app.post("/api/v1/alerts/test")
async def test_notification(payload: dict = Body(default={})):
    target_id = str(payload.get("target_id") or "") or None
    # targets must be imported BEFORE the destinations check
    notify.import_targets(store.notify_targets(enabled_only=True),
                          store.notify_config().get("settings") or {})
    if target_id and not any(str(item.get("id")) == target_id for item in store.notify_targets()):
        raise HTTPException(404, "通知人不存在")
    if not notify.destinations():
        raise HTTPException(503, "尚未配置任何通知方式（环境变量为空且通知设置页无启用的通知人）")
    state = store.alert_state()
    notify.import_breaker(state.get("channel_health"))
    delivery = await notify.send_test(payload.get("target_id") if isinstance(payload, dict) else None)
    state["channel_health"] = notify.export_breaker()
    store.save_alert_state(state)
    store.append_alert_history([{
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trigger": "manual-test", "action": "test", "alert_id": "electricity:test",
        "severity": "critical", "title": "测试告警（手动触发）",
        "channels": [f"{item.get('channel')}:{item.get('name')}" for item in delivery],
        "delivered": all(item.get("ok") for item in delivery) if delivery else None,
    }])
    return {"sent": len(delivery), "results": delivery}


@app.post("/api/v1/alerts/silence")
def silence(payload: dict = Body(default={})):
    """Silence one alert (``id``) or every alert (``id`` empty / ``"*"``)."""
    try:
        hours = float(payload.get("hours", 12))
    except (TypeError, ValueError):
        raise HTTPException(400, "hours 必须是数字") from None
    if hours <= 0 or hours > 24 * 30:
        raise HTTPException(400, "hours 必须介于 0 与 720 之间")
    alert_id = str(payload.get("id") or "*")
    if alert_id != "*" and not alert_id.startswith("electricity:"):
        raise HTTPException(400, "仅支持静默本模块的告警")
    now = datetime.now(timezone.utc)
    state = store.alert_state()
    state = alerting.apply_silence(state, alert_id, hours, now)
    store.save_alert_state(state)
    return {"silenced": alert_id, "until": (now + timedelta(hours=hours)).isoformat(timespec="seconds")}


@app.post("/api/v1/alerts/unsilence")
def unsilence(payload: dict = Body(default={})):
    """Release a silence. POST (not DELETE) because the workbench iframe bridge
    only proxies GET/POST into module APIs."""
    alert_id = str(payload.get("id") or "*")
    state = store.alert_state()
    silences = dict(state.get("silences") or {})
    silences.pop(alert_id, None)
    state["silences"] = silences
    store.save_alert_state(state)
    return {"released": alert_id, "remaining": silences}


# --------------------------------------------------------------------------- #
# diagnostics: raw payload capture + extra-API probe (v0.3.0)
# --------------------------------------------------------------------------- #
@app.get("/api/v1/diagnostics/raw")
def list_raw():
    return {
        "capture_enabled": store.raw_capture_enabled(),
        "hint": "设置 ELECTRICITY_RAW_CAPTURE=true 后，采集会留档脱敏后的原始报文",
        "files": store.list_raw(40),
    }


@app.get("/api/v1/diagnostics/raw/shape")
def raw_shape(name: str = Query(min_length=5, max_length=120)):
    """Structural summary (keys/types/list lengths) of one captured payload."""
    shape = store.raw_shape(name)
    if shape is None:
        raise HTTPException(404, "未找到该报文（名称需形如 balance-20261017T090000.json）")
    return shape


@app.get("/api/v1/diagnostics/raw/content")
def raw_content(name: str = Query(min_length=5, max_length=120)):
    payload = store.read_raw(name)
    if payload is None:
        raise HTTPException(404, "未找到该报文")
    return payload


@app.post("/api/v1/diagnostics/probe")
async def run_probe(payload: dict = Body(default={})):
    """Probe the AUTH_APIS endpoints this module never calls.

    Answers whether tiered (阶梯) / time-of-use (分时) data is reachable, and
    stores the redacted response so the parsers can be built from evidence.
    """
    if not store.configured():
        raise HTTPException(503, "国网账号或采集户号尚未配置")
    apis = payload.get("apis") if isinstance(payload.get("apis"), list) else None
    try:
        return await probe(store, apis)
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        raise HTTPException(502, describe_error(exc)) from None


# --------------------------------------------------------------------------- #
# report export (v0.5.0)
# --------------------------------------------------------------------------- #
@app.get("/api/v1/report.csv", response_class=PlainTextResponse)
def report_csv(month: str = Query(default="", max_length=6)):
    """CSV of daily usage / monthly cost / alert counts for one month."""
    now = datetime.now(timezone.utc)
    target = month or now.astimezone(alerting.TZ).strftime("%Y%m")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["类型", "日期/月份", "用电量(度)", "电费(元)", "数据来源"])
    for row in store.daily_history(365):
        if str(row.get("day", "")).startswith(f"{target[:4]}-{target[4:6]}"):
            writer.writerow(["日用电", row.get("day"), row.get("kwh"), "", "国网公布数据"])
    for row in store.monthly_history(24):
        if str(row.get("month")) == target:
            writer.writerow(["月度账单", target, row.get("kwh"), row.get("cost"), "国网账单"])
    alerts_count = sum(1 for item in store.alert_history(500)
                       if str(item.get("at", "")).startswith(f"{target[:4]}-{target[4:6]}"))
    writer.writerow(["告警次数", target, "", "", f"{alerts_count} 条推送记录"])
    headers = {"Content-Disposition": f'attachment; filename="electricity-{target}.csv"'}
    return PlainTextResponse(content="\ufeff" + buffer.getvalue(),
                             media_type="text/csv; charset=utf-8", headers=headers)



# --------------------------------------------------------------------------- #
# notification targets (v0.6.0): people / groups managed on the notify page
# --------------------------------------------------------------------------- #
ALLOWED_TARGET_TYPES = {
    "sms": "短信（经自建短信网关转发）",
    "email": "邮箱",
    "feishu": "飞书群机器人",
    "wecom": "企业微信群机器人",
    "wechat_serverchan": "微信推送（Server 酱）",
    "wechat_pushplus": "微信推送（PushPlus）",
    "dingtalk": "钉钉机器人",
    "bark": "Bark（iOS）",
    "ntfy": "ntfy",
    "webhook": "自定义 Webhook",
}


def _mask_target(kind: str, value: str) -> str:
    value = value or ""
    if len(value) <= 6:
        return value
    if "@" in value:
        head, _, domain = value.partition("@")
        return f"{head[:2]}***@{domain}" if domain else value[:2] + "***"
    return value[:4] + "***" + value[-4:]


@app.get("/api/v1/notify/targets")
def list_notify_targets():
    config = store.notify_config()
    masked = []
    for item in config["targets"]:
        entry = dict(item)
        entry["target_masked"] = _mask_target(str(item.get("type")), str(item.get("target") or ""))
        masked.append(entry)
    settings = dict(config.get("settings") or {})
    for secret_key in ("password", "sms_sign"):
        if settings.get(secret_key):
            settings[secret_key] = "***"
    return {
        "source": KEY,
        "types": ALLOWED_TARGET_TYPES,
        "targets": masked,
        "settings": settings,
        "smtp_ready": bool((config.get("settings") or {}).get("smtp_host")),
        "sms_gateway_ready": bool((config.get("settings") or {}).get("sms_webhook")),
    }


@app.post("/api/v1/notify/targets")
def upsert_notify_target(payload: dict = Body(default={})):
    """Create or update one recipient: {id?, name, type, target, min_severity, enabled, extra?}."""
    kind = str(payload.get("type") or "").strip()
    if kind not in ALLOWED_TARGET_TYPES:
        raise HTTPException(400, f"不支持的通知方式：{kind or '空'}")
    target_value = str(payload.get("target") or "").strip()
    if not target_value:
        raise HTTPException(400, "通知地址不能为空")
    name = str(payload.get("name") or "").strip() or target_value[:24]
    severity = str(payload.get("min_severity") or "warning")
    if severity not in alerting.SEVERITY_ORDER:
        raise HTTPException(400, "min_severity 仅支持 info / warning / critical")

    config = store.notify_config()
    item_id = str(payload.get("id") or "")
    updated = False
    for item in config["targets"]:
        if item_id and str(item.get("id")) == item_id:
            item.update({
                "name": name, "type": kind, "target": target_value, "min_severity": severity,
                "enabled": bool(payload.get("enabled", item.get("enabled", True))),
                "extra": payload.get("extra") if isinstance(payload.get("extra"), dict)
                         else item.get("extra") or {},
            })
            updated = True
            break
    if not updated:
        config["targets"].append({
            "id": uuid.uuid4().hex[:12], "name": name, "type": kind, "target": target_value,
            "min_severity": severity, "enabled": bool(payload.get("enabled", True)),
            "extra": payload.get("extra") if isinstance(payload.get("extra"), dict) else {},
        })
    store.save_notify_config(config)
    return {"ok": True, "updated": updated, "count": len(config["targets"])}


@app.post("/api/v1/notify/targets/delete")
def delete_notify_target(payload: dict = Body(default={})):
    item_id = str(payload.get("id") or "")
    if not item_id:
        raise HTTPException(400, "缺少 id")
    config = store.notify_config()
    before = len(config["targets"])
    config["targets"] = [item for item in config["targets"] if str(item.get("id")) != item_id]
    if len(config["targets"]) == before:
        raise HTTPException(404, "通知人不存在")
    store.save_notify_config(config)
    return {"ok": True, "removed": item_id, "count": len(config["targets"])}


@app.post("/api/v1/notify/targets/toggle")
def toggle_notify_target(payload: dict = Body(default={})):
    item_id = str(payload.get("id") or "")
    config = store.notify_config()
    for item in config["targets"]:
        if str(item.get("id")) == item_id:
            item["enabled"] = not bool(item.get("enabled", True))
            store.save_notify_config(config)
            return {"ok": True, "id": item_id, "enabled": item["enabled"]}
    raise HTTPException(404, "通知人不存在")


@app.post("/api/v1/notify/settings")
def save_notify_settings(payload: dict = Body(default={})):
    """SMTP credentials for email targets + the generic SMS gateway address."""
    config = store.notify_config()
    settings = dict(config.get("settings") or {})
    for key in ("smtp_host", "smtp_port", "username", "password", "mail_from",
                "sms_webhook", "sms_sign", "sms_template"):
        if key in payload:
            value = payload.get(key)
            if key == "password" and value in (None, "", "***"):
                continue  # keep the stored secret when the form sends the mask back
            settings[key] = str(value).strip() if value is not None else ""
    store.save_notify_config({**config, "settings": settings})
    masked = {key: ("***" if key == "password" and value else value)
              for key, value in settings.items()}
    return {"ok": True, "settings": masked}


@app.post("/api/v1/notify/test")
async def test_notify_target(payload: dict = Body(default={})):
    """Send a test message to one recipient (or to every enabled destination)."""
    target_id = str(payload.get("target_id") or "") or None
    notify.import_targets(store.notify_targets(enabled_only=True),
                          store.notify_config().get("settings") or {})
    if target_id and not any(str(item.get("id")) == target_id for item in store.notify_targets()):
        raise HTTPException(404, "通知人不存在")
    if not notify.destinations():
        raise HTTPException(503, "尚未配置任何可用的通知方式")
    delivery = await notify.send_test(target_id)
    store.append_alert_history([{
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trigger": "notify-test", "action": "test", "alert_id": "electricity:test",
        "severity": "critical", "title": "测试通知（通知设置页）",
        "channels": [f"{item.get('channel')}:{item.get('name')}" for item in delivery],
        "delivered": all(item.get("ok") for item in delivery) if delivery else None,
    }])
    return {"sent": len(delivery), "results": delivery}


# --------------------------------------------------------------------------- #
# usage statistics (v0.6.0): day / month / year spending
# --------------------------------------------------------------------------- #
def _build_stats(granularity: str, limit: int, now: datetime) -> dict:
    cfg = _cfg()
    ctx = _current_context(now)
    price = ctx["price_per_kwh"]
    granularity = granularity if granularity in {"day", "month", "year"} else "day"
    rows: list[dict] = []

    if granularity == "day":
        for item in store.daily_history(limit):
            kwh = float(item.get("kwh") or 0)
            rows.append({"label": str(item.get("day")), "kwh": round(kwh, 2),
                         "cost": round(kwh * price, 2), "estimated": True,
                         "source": "公布电量 × 均价"})
    elif granularity == "month":
        for item in store.monthly_history(limit):
            label = str(item.get("month"))
            kwh = item.get("kwh")
            cost = item.get("cost")
            estimated = cost is None
            if estimated:
                cost = round(float(kwh or 0) * price, 2)
            rows.append({"label": f"{label[:4]}-{label[4:6]}", "kwh": float(kwh or 0),
                         "cost": float(cost or 0), "estimated": estimated,
                         "source": "国网账单" if not estimated else "公布电量 × 均价"})
        month_prefix = f"{ctx['month_label'][:4]}-{ctx['month_label'][4:6]}"
        month_kwh = round(sum(float(item.get("kwh") or 0) for item in (ctx["daily"] or [])
                              if str(item.get("day", "")).startswith(month_prefix)), 2)
        current = {"label": month_prefix,
                   "kwh": month_kwh or None, "cost": ctx["month_cost"],
                   "estimated": bool(ctx["month_cost_estimated"]), "source": "本月（进行中）"}
        if rows and rows[0]["label"] == current["label"]:
            rows[0] = current
        else:
            rows.insert(0, current)
    else:  # year
        by_year: dict[str, dict] = {}
        for item in store.monthly_history(240):
            year_label = str(item.get("month"))[:4]
            bucket = by_year.setdefault(year_label, {"kwh": 0.0, "cost": 0.0, "estimated": False})
            bucket["kwh"] += float(item.get("kwh") or 0)
            bucket["cost"] += float(item.get("cost") or 0)
        year_info = ctx["year"] or {}
        if year_info.get("kwh") is not None:
            by_year[str(year_info.get("year") or now.astimezone(alerting.TZ).year)] = {
                "kwh": float(year_info.get("kwh") or 0),
                "cost": float(year_info.get("cost") or 0),
                "estimated": False,
            }
        rows = [{"label": label, "kwh": round(value["kwh"], 2),
                 "cost": round(value["cost"], 2), "estimated": value["estimated"],
                 "source": "年度账单合计" if not value["estimated"] else "估算"}
                for label, value in sorted(by_year.items(), reverse=True)][:limit]
        rows = list(reversed(rows))

    # month-over-month / year-over-year delta of spending
    for index, row in enumerate(rows):
        previous = rows[index + 1] if index + 1 < len(rows) else None
        row["delta_pct"] = (round((row["cost"] - previous["cost"]) / previous["cost"] * 100, 1)
                            if previous and previous["cost"] else None)
    totals = {
        "kwh": round(sum(row["kwh"] or 0 for row in rows), 2),
        "cost": round(sum(row["cost"] or 0 for row in rows), 2),
        "count": len(rows),
        "avg": round(sum(row["cost"] or 0 for row in rows) / len(rows), 2) if rows else None,
    }
    return {"source": KEY, "granularity": granularity, "limit": limit,
            "price_per_kwh": price, "price_source": ctx["price_source"],
            "rows": rows, "totals": totals,
            "current_balance": ctx["balance"], "remaining_days": ctx["remaining_days"]}


@app.get("/api/v1/stats")
def usage_stats(granularity: str = Query(default="day"), limit: int = Query(default=30, ge=1, le=730)):
    return _build_stats(granularity, limit, datetime.now(timezone.utc))


@app.get("/ui/notify", response_class=HTMLResponse)
def notify_ui():
    # no-store: the dashboard is actively developed; a cached page would keep
    # calling removed APIs and confuse the operator.
    return HTMLResponse(render_page(), headers={"Cache-Control": "no-store"})


@app.get("/ui", response_class=HTMLResponse)
def ui():
    return HTMLResponse(render_page(), headers={"Cache-Control": "no-store"})
