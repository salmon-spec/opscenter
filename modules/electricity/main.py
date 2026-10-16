"""Electricity monitoring module (国家电网电费监测).

Owns its collection loop, its own data volume and its own module contract.
It never touches another module's storage and only appears to the workbench as
`/modules/electricity/*` through the shared gateway.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from collector import collect, describe_error
from page import render_page
from runtime.app import create_module_app
from storage import ElectricityStore

VERSION = "0.1.0"
KEY = "electricity"
LOW_BALANCE_THRESHOLD = float(os.getenv("ELECTRICITY_LOW_BALANCE", "20"))
REFRESH_HOURS = float(os.getenv("ELECTRICITY_REFRESH_HOURS", "12"))
# Two collection rounds without success means the data can no longer be trusted.
STALE_AFTER_SECONDS = 86400

store = ElectricityStore()
_collect_lock = asyncio.Lock()
_status = {"message": "等待首次采集"}


def _parse_updated(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _latest_row(snapshot: dict) -> dict:
    rows = snapshot.get("households") or []
    return rows[0] if rows and isinstance(rows[0], dict) else {}


def _current_data(snapshot: dict) -> dict:
    row = _latest_row(snapshot)
    daily = row.get("daily") or []
    bills = row.get("bills") or []
    recent = daily[:7]
    average = round(sum(float(item.get("kwh") or 0) for item in recent) / len(recent), 2) if recent else None
    return {
        "balance": row.get("balance"),
        "arrears": row.get("arrears"),
        "latest_day": (daily[0] or {}).get("day") if daily else None,
        "latest_day_kwh": (daily[0] or {}).get("kwh") if daily else None,
        "latest_month": (bills[0] or {}).get("month") if bills else None,
        "latest_month_cost": (bills[0] or {}).get("cost") if bills else None,
        "average_7d_kwh": average,
        "updated_at": snapshot.get("updated_at"),
        "errors": len(snapshot.get("errors") or []),
    }


def summary_provider():
    now = datetime.now(timezone.utc)
    if not store.configured():
        return {"status": "unavailable", "collected_at": None, "stale_after_seconds": STALE_AFTER_SECONDS,
                "data": {"configured": False}, "error": "国网账号或采集户号尚未配置"}
    snapshot = store.snapshot()
    updated = _parse_updated(snapshot.get("updated_at"))
    if updated is None:
        return {"status": "unavailable", "collected_at": None, "stale_after_seconds": STALE_AFTER_SECONDS,
                "data": {"configured": True}, "error": _status.get("message") or "尚未完成首次采集"}
    age = (now - updated).total_seconds()
    data = _current_data(snapshot)
    data["configured"] = True
    if age > STALE_AFTER_SECONDS:
        return {"status": "degraded", "collected_at": updated, "stale_after_seconds": STALE_AFTER_SECONDS,
                "data": data, "error": "采集结果已超过 24 小时未更新"}
    return {"status": "ok", "collected_at": updated, "stale_after_seconds": STALE_AFTER_SECONDS,
            "data": data, "error": None}


def findings_provider():
    now = datetime.now(timezone.utc)
    items: list[dict] = []

    if not store.configured():
        items.append({
            "id": "electricity:credentials", "resource_id": "electricity", "severity": "critical",
            "title": "电费采集凭据未配置",
            "summary": "模块缺少国网账号、密码摘要或目标户号，无法采集电费数据。",
            "observed_at": now,
        })

    snapshot = store.snapshot()
    updated = _parse_updated(snapshot.get("updated_at"))
    row = _latest_row(snapshot)
    ident = str(row.get("id") or "")
    balance = row.get("balance")

    if updated is None and store.configured():
        items.append({
            "id": "electricity:no-data", "resource_id": "electricity", "severity": "warning",
            "title": "电费数据尚未采集",
            "summary": _status.get("message") or "模块启动后还没有成功完成一次采集。",
            "observed_at": now,
        })
    elif updated is not None:
        age = (now - updated).total_seconds()
        if age > STALE_AFTER_SECONDS:
            items.append({
                "id": "electricity:stale", "resource_id": ident or "electricity", "severity": "warning",
                "title": "电费数据已超过 24 小时未更新",
                "summary": f"最近一次成功采集时间：{updated.astimezone(timezone.utc).isoformat(timespec='seconds')}。",
                "observed_at": now,
            })
        if balance is not None and float(balance) < LOW_BALANCE_THRESHOLD:
            items.append({
                "id": "electricity:low-balance", "resource_id": ident or "electricity", "severity": "warning",
                "title": f"电费余额低于 {LOW_BALANCE_THRESHOLD:g} 元",
                "summary": f"当前余额 {float(balance):.2f} 元，请及时充值。",
                "observed_at": updated,
            })
        for error in (snapshot.get("errors") or [])[:5]:
            items.append({
                "id": f"electricity:slice:{abs(hash(error)) % 10**8}", "resource_id": ident or "electricity",
                "severity": "info", "title": "部分电费数据待重试", "summary": str(error)[:400],
                "observed_at": updated,
            })

    return {"status": "degraded" if items else "ok", "collected_at": now, "items": items, "error": None}


async def run_collection(trigger: str = "schedule") -> None:
    """Single-flight collection round; failures only update the status message."""
    if _collect_lock.locked():
        _status["message"] = "已有采集任务在进行"
        return
    async with _collect_lock:
        _status["message"] = "正在采集"
        try:
            await collect(store)
            _status["message"] = "采集完成"
            logging.info("electricity collection ok (trigger=%s)", trigger)
        except Exception as exc:  # noqa: BLE001 - surfaced through summary/findings
            message = describe_error(exc)
            _status["message"] = message
            logging.warning("electricity collection failed (trigger=%s): %s", trigger, message)


async def _periodic() -> None:
    while True:
        await run_collection("schedule")
        await asyncio.sleep(max(300.0, REFRESH_HOURS * 3600))


@asynccontextmanager
async def _lifespan(_app):
    task = asyncio.create_task(_periodic())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = create_module_app(
    key=KEY, version=VERSION, summary_provider=summary_provider, findings_provider=findings_provider,
    capabilities=["summary:read", "findings:read", "electricity:read"],
    readiness_provider=store.ping,
)
app.router.lifespan_context = _lifespan


@app.get("/api/v1/overview")
def overview():
    if not store.configured():
        raise HTTPException(503, "国网账号或采集户号尚未配置")
    snapshot = store.snapshot()
    if not snapshot:
        raise HTTPException(503, _status.get("message") or "尚未完成首次采集")
    return {"source": KEY, "status": _status.get("message"), **snapshot}


@app.post("/api/v1/refresh")
async def refresh():
    if not store.configured():
        raise HTTPException(503, "国网账号或采集户号尚未配置")
    if _collect_lock.locked():
        return {"triggered": False, "message": "已有采集任务在进行"}
    asyncio.create_task(run_collection("manual"))
    return {"triggered": True, "message": "已触发采集"}


@app.get("/ui", response_class=HTMLResponse)
def ui():
    return HTMLResponse(render_page())
