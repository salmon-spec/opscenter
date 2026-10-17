"""SMS gateway module (短信通知) — a standalone OpsCenter module.

Contract peers (module-electricity, module-alerts, ...) call it server-side to
deliver SMS messages through a configurable upstream provider, so no single
module has to embed SMS credentials.

Endpoints:
* ``POST /api/v1/send``  {phone, message, sign?, template?} + header
  ``X-SMS-Token``  -> forwards to the upstream provider and audits the result
* ``GET  /api/v1/history``  recent send audit (requires the same token)
* ``GET  /api/v1/module`` / ``/health/*`` / ``/metrics``  standard contract

Configuration (env / Secret):
* ``SMS_TOKEN``          shared token required in ``X-SMS-Token`` (empty = open)
* ``SMS_PROVIDER_URL``   upstream gateway accepting ``{"phone","message",...}``
* ``SMS_PROVIDER_TOKEN`` optional bearer token forwarded to the provider
* ``SMS_DATA_DIR``       audit storage (default /data)
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import ClientSession
from fastapi import Body, Header, HTTPException

from runtime.app import create_module_app

VERSION = "0.1.0"
KEY = "sms"

DATA_DIR = Path(os.getenv("SMS_DATA_DIR", "/data"))
AUDIT_PATH = DATA_DIR / "sms_log.jsonl"


def _token_ok(token: str) -> bool:
    expected = (os.getenv("SMS_TOKEN") or "").strip()
    if not expected:
        return True  # open mode: network policy is expected to fence the namespace
    import hmac
    return hmac.compare_digest(expected, (token or "").strip())


def _audit(record: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        os.chmod(AUDIT_PATH, 0o600)
    except OSError:
        pass  # audit is best-effort


def _history(limit: int = 50) -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-max(1, min(limit, 500)):]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    rows.reverse()
    return rows


def summary_provider():
    provider = bool((os.getenv("SMS_PROVIDER_URL") or "").strip())
    rows = _history(200)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    sent_today = sum(1 for row in rows
                     if str(row.get("at", "")).startswith(today) and row.get("ok"))
    failed_today = sum(1 for row in rows
                       if str(row.get("at", "")).startswith(today) and not row.get("ok"))
    status = "ok" if provider else "unavailable"
    return {"status": status, "collected_at": datetime.now(timezone.utc),
            "stale_after_seconds": 86400,
            "data": {"provider_configured": provider, "sent_today": sent_today,
                     "failed_today": failed_today, "audit_rows": len(rows)},
            "error": None if provider else "SMS_PROVIDER_URL 未配置"}


def findings_provider():
    now = datetime.now(timezone.utc)
    provider = bool((os.getenv("SMS_PROVIDER_URL") or "").strip())
    if provider:
        return {"status": "ok", "collected_at": now, "items": [], "error": None}
    return {"status": "degraded", "collected_at": now, "items": [{
        "id": "sms:provider-missing", "resource_id": "sms", "severity": "warning",
        "title": "短信网关未配置",
        "summary": "缺少 SMS_PROVIDER_URL，短信通知无法投递；请在部署 Secret 中配置上游网关地址。",
        "observed_at": now,
    }], "error": None}


app = create_module_app(
    key=KEY, version=VERSION,
    summary_provider=summary_provider, findings_provider=findings_provider,
    capabilities=["sms:read", "sms:send"],
    readiness_provider=lambda: True,
)


@app.post("/api/v1/send")
async def send(payload: dict = Body(default={}), x_sms_token: str = Header(default="")):
    if not _token_ok(x_sms_token):
        raise HTTPException(401, "X-SMS-Token 无效")
    phone = str(payload.get("phone") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not phone or not message:
        raise HTTPException(400, "phone 与 message 不能为空")
    provider_url = (os.getenv("SMS_PROVIDER_URL") or "").strip()
    if not provider_url:
        raise HTTPException(503, "短信网关未配置（SMS_PROVIDER_URL）")

    body = {"phone": phone, "message": message[:480],
            "sign": payload.get("sign") or os.getenv("SMS_PROVIDER_SIGN", ""),
            "template": payload.get("template") or os.getenv("SMS_PROVIDER_TEMPLATE", "")}
    headers = {}
    provider_token = (os.getenv("SMS_PROVIDER_TOKEN") or "").strip()
    if provider_token:
        headers["Authorization"] = f"Bearer {provider_token}"

    now = datetime.now(timezone.utc)
    ok, status, detail = False, 0, ""
    try:
        timeout = float(os.getenv("SMS_PROVIDER_TIMEOUT", "10") or 10)
        async with ClientSession() as session:
            async with session.post(provider_url, json=body, headers=headers,
                                    timeout=timeout) as response:
                detail = (await response.text())[:300]
                status = response.status
                ok = response.status < 400
    except Exception as exc:  # noqa: BLE001 - surfaced through the audit + response
        detail = f"{type(exc).__name__}: {exc}"[:200]

    phone_masked = phone[:3] + "****" + phone[-4:] if len(phone) >= 8 else "***"
    _audit({"at": now.isoformat(timespec="seconds"), "phone": phone_masked,
            "ok": ok, "status": status, "detail": detail,
            "message_chars": len(message)})
    if not ok:
        logging.warning("sms send failed phone=%s status=%s", phone_masked, status)
        raise HTTPException(502, f"短信网关投递失败（status={status}）：{detail[:120]}")
    return {"ok": True, "phone": phone_masked, "provider_status": status}


@app.get("/api/v1/history")
def history(limit: int = 50, x_sms_token: str = Header(default="")):
    if not _token_ok(x_sms_token):
        raise HTTPException(401, "X-SMS-Token 无效")
    return {"source": KEY, "rows": _history(limit)}


@app.get("/api/v1/audit/clear")
def audit_guard():
    """Destructive actions are not exposed over HTTP; audit files are rotated by ops."""
    raise HTTPException(405, "不支持该操作")
