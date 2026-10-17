"""Multi-channel notification delivery for the electricity module.

Only ``aiohttp`` (already a module dependency) plus the standard library are
used, so no extra pip package is required. Channels are described by JSON in
``ELECTRICITY_NOTIFY_CHANNELS``::

    [{"type": "feishu", "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
      "secret": "", "min_severity": "warning"}]

Supported types: ``webhook`` / ``feishu`` / ``wecom`` / ``dingtalk`` /
``serverchan`` / ``bark`` / ``ntfy`` / ``email``.

The unified-URL idea (feishu://, wecombot://, bark://, ntfy://…) is borrowed
from Apprise; we only implement the channels that actually make sense for a
single household here, keyed by an explicit ``type`` instead of a URL scheme.

v0.2.1 additions: bounded retries with exponential backoff for transient
failures, a per-channel circuit breaker (persisted with the alert state so a pod
restart does not immediately hammer a known-bad webhook), and Feishu interactive
cards carrying the severity colour plus a deep link back to the workbench.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import smtplib
import ssl
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from aiohttp import ClientSession

TZ = timezone(timedelta(hours=8))

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
ICONS = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}
COLORS = {"info": "blue", "warning": "orange", "critical": "red"}
ACTION_TITLES = {"firing": "电费告警", "re-fire": "电费告警（仍未恢复）", "resolved": "电费告警已恢复"}

# channel key -> {failures, open_until, last_error, last_ok_at}
_breaker: dict[str, dict] = {}


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def _severity_ok(min_severity: str, severity: str) -> bool:
    return SEVERITY_ORDER.get(severity, 1) >= SEVERITY_ORDER.get(min_severity or "warning", 1)


def channels() -> list[dict]:
    """Channel list from the JSON env var, plus a few convenience single-URL vars."""
    raw = (os.getenv("ELECTRICITY_NOTIFY_CHANNELS") or "").strip()
    items: list[dict] = []
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            logging.warning("electricity notify: ELECTRICITY_NOTIFY_CHANNELS 不是合法 JSON，已忽略")
            parsed = []
        if isinstance(parsed, dict):
            parsed = [parsed]
        for entry in parsed or []:
            if isinstance(entry, dict) and entry.get("type") and entry.get("enabled", True):
                items.append(entry)

    shorthand = {
        "feishu": "ELECTRICITY_NOTIFY_FEISHU",
        "wecom": "ELECTRICITY_NOTIFY_WECOM",
        "dingtalk": "ELECTRICITY_NOTIFY_DINGTALK",
        "serverchan": "ELECTRICITY_NOTIFY_SERVERCHAN",
        "bark": "ELECTRICITY_NOTIFY_BARK",
        "ntfy": "ELECTRICITY_NOTIFY_NTFY",
        "webhook": "ELECTRICITY_NOTIFY_WEBHOOK",
    }
    for kind, env_name in shorthand.items():
        value = (os.getenv(env_name) or "").strip()
        if value:
            items.append({"type": kind, "webhook": value})
    return items


def enabled() -> bool:
    return bool(channels())


def alert_url() -> str:
    return (os.getenv("ELECTRICITY_ALERT_URL") or "").strip()


def _retries() -> int:
    try:
        return max(0, int(float(os.getenv("ELECTRICITY_NOTIFY_RETRIES", "2") or 2)))
    except ValueError:
        return 2


def _breaker_threshold() -> int:
    try:
        return max(1, int(float(os.getenv("ELECTRICITY_NOTIFY_BREAKER_FAILURES", "5") or 5)))
    except ValueError:
        return 5


def _breaker_seconds() -> int:
    try:
        return max(60, int(float(os.getenv("ELECTRICITY_NOTIFY_BREAKER_SECONDS", "1800") or 1800)))
    except ValueError:
        return 1800


# --------------------------------------------------------------------------- #
# circuit breaker (persisted by the caller alongside the alert state)
# --------------------------------------------------------------------------- #
def import_breaker(value: dict | None) -> None:
    global _breaker
    _breaker = {str(key): dict(item) for key, item in (value or {}).items() if isinstance(item, dict)}


def export_breaker() -> dict:
    return {key: dict(value) for key, value in _breaker.items()}


def _channel_key(channel: dict, index: int) -> str:
    return f"{channel.get('type')}#{index}"


def _breaker_allows(key: str, now: datetime) -> bool:
    entry = _breaker.get(key) or {}
    until = entry.get("open_until")
    if not until:
        return True
    try:
        deadline = datetime.fromisoformat(str(until))
    except ValueError:
        return True
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return now >= deadline


def _record_result(key: str, ok: bool, error: str | None, now: datetime) -> None:
    entry = dict(_breaker.get(key) or {})
    if ok:
        entry = {"failures": 0, "last_ok_at": now.isoformat(timespec="seconds")}
    else:
        failures = int(entry.get("failures") or 0) + 1
        entry["failures"] = failures
        entry["last_error"] = (error or "")[:200]
        if failures >= _breaker_threshold():
            entry["open_until"] = (now + timedelta(seconds=_breaker_seconds())).isoformat(timespec="seconds")
            logging.warning("electricity notify breaker open channel=%s failures=%s", key, failures)
    _breaker[key] = entry


# --------------------------------------------------------------------------- #
# message rendering
# --------------------------------------------------------------------------- #
def render_text(event: dict, extra_lines: list[str] | None = None) -> str:
    action = event.get("action", "firing")
    alert = event.get("alert") or event
    severity = alert.get("severity", "warning")
    icon = ICONS.get(severity, "⚠️")
    lines = [f"{icon} {ACTION_TITLES.get(action, '电费告警')}｜{alert.get('title', '')}", ""]
    if alert.get("summary"):
        lines.append(str(alert["summary"]))
    observed = alert.get("observed_at")
    if observed:
        try:
            stamp = observed if isinstance(observed, str) else observed.isoformat(timespec="seconds")
            when = datetime.fromisoformat(stamp)
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            lines.append(f"触发时间：{when.astimezone(TZ).strftime('%Y-%m-%d %H:%M:%S')}（北京时间）")
        except (TypeError, ValueError):
            pass
    lines.append(f"告警 ID：{alert.get('id', '-')}")
    for line in extra_lines or []:
        lines.append(line)
    url = alert_url()
    if url:
        lines.append(f"查看详情：{url}")
    lines.append("来源：OpsCenter 电费监测模块")
    return "\n".join(lines)


def _feishu_card(text: str, alert: dict, action: str) -> dict:
    severity = alert.get("severity", "warning")
    title = f"{ICONS.get(severity, '⚠️')} {ACTION_TITLES.get(action, '电费告警')}｜{alert.get('title', '')}"
    fields = [
        {"is_short": False, "text": {"tag": "lark_md", "content": str(alert.get("summary") or "")}},
    ]
    details = alert.get("details") or {}
    if isinstance(details, dict) and details:
        rendered = "\n".join(f"**{key}**：{value}" for key, value in list(details.items())[:4]
                             if not isinstance(value, (dict, list)))
        if rendered:
            fields.append({"is_short": False, "text": {"tag": "lark_md", "content": rendered}})
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"template": COLORS.get(severity, "orange"),
                   "title": {"tag": "plain_text", "content": title}},
        "elements": fields,
    }
    url = alert_url()
    if url:
        card["elements"].append({
            "tag": "action",
            "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "查看详情"},
                         "type": "primary", "url": url}],
        })
    card["elements"].append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": f"来源：OpsCenter 电费监测 · {alert.get('id', '-')}"}]})
    return card


def build_channel_payload(channel: dict, text: str, alert: dict, action: str) -> dict:
    """Request description for one channel: ``{method, url, json|data|headers}``."""
    kind = channel.get("type")
    icon = ICONS.get(alert.get("severity", "warning"), "⚠️")
    title = f"{icon} {ACTION_TITLES.get(action, '电费告警')}"

    if kind == "feishu":
        url = channel.get("webhook", "")
        use_card = str(channel.get("card", os.getenv("ELECTRICITY_NOTIFY_FEISHU_CARD", "true"))
                       ).lower() not in {"0", "false", "no"}
        if use_card:
            payload: dict = {"msg_type": "interactive", "card": _feishu_card(text, alert, action)}
        else:
            body = "\n".join(text.splitlines()[2:]) if len(text.splitlines()) > 2 else text
            payload = {"msg_type": "text", "content": {"text": f"{title}｜{alert.get('title', '')}\n\n{body}"}}
        secret = channel.get("secret") or ""
        if secret:
            stamp = str(int(time.time()))
            string_to_sign = f"{stamp}\n{secret}"
            payload["timestamp"] = stamp
            payload["sign"] = base64.b64encode(
                hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
            ).decode("utf-8")
        return {"method": "POST", "url": url, "json": payload}

    if kind == "wecom":
        return {"method": "POST", "url": channel.get("webhook", ""),
                "json": {"msgtype": "markdown",
                         "markdown": {"content": f"**{title}｜{alert.get('title', '')}**\n{text}"}}}

    if kind == "dingtalk":
        url = channel.get("webhook", "")
        secret = channel.get("secret") or ""
        if secret:
            stamp = str(round(time.time() * 1000))
            string_to_sign = f"{stamp}\n{secret}"
            sign = urllib.parse.quote_plus(base64.b64encode(
                hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
            ))
            url = f"{url}&timestamp={stamp}&sign={sign}" if "?" in url else f"{url}?timestamp={stamp}&sign={sign}"
        return {"method": "POST", "url": url,
                "json": {"msgtype": "markdown",
                         "markdown": {"title": title,
                                      "text": f"### {title}｜{alert.get('title', '')}\n\n{text}"}}}

    if kind == "serverchan":
        key = channel.get("sendkey") or channel.get("webhook", "")
        url = key if key.startswith("http") else f"https://sctapi.ftqq.com/{key}.send"
        return {"method": "POST", "url": url,
                "data": {"title": f"{title}｜{alert.get('title', '')}", "desp": text}}

    if kind == "bark":
        base = channel.get("webhook") or f"https://api.day.app/{channel.get('device_key', '')}"
        return {"method": "POST", "url": base,
                "json": {"title": title,
                         "body": f"{alert.get('title', '')}\n{alert.get('summary', '')}",
                         "group": "电费监测",
                         "level": "timeSensitive" if alert.get("severity") == "critical" else "active"}}

    if kind == "ntfy":
        base = (channel.get("webhook") or "https://ntfy.sh").rstrip("/")
        topic = channel.get("topic", "electricity")
        return {"method": "POST", "url": f"{base}/{topic}", "data": text,
                "headers": {"Title": urllib.parse.quote(title), "Tags": "electricity,zap",
                            "Priority": "5" if alert.get("severity") == "critical" else "3",
                            "Content-Type": "text/plain; charset=utf-8"}}

    return {"method": "POST", "url": channel.get("webhook", ""),
            "json": {"source": "electricity", "action": action, "text": text,
                     "alert": _jsonable(alert)}}


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


# --------------------------------------------------------------------------- #
# delivery
# --------------------------------------------------------------------------- #
async def _post(session: ClientSession, plan: dict, timeout: float) -> dict:
    async with session.request(plan["method"], plan["url"], json=plan.get("json"),
                               data=plan.get("data"), headers=plan.get("headers"),
                               timeout=timeout) as response:
        body = (await response.text())[:300]
        # Feishu/WeCom/DingTalk answer 200 with an error code in the body.
        ok = response.status < 400
        if ok and body:
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                code = parsed.get("code", parsed.get("errcode", parsed.get("StatusCode")))
                if code not in (None, 0, "0", 200, "200"):
                    ok = False
        return {"ok": ok, "status": response.status, "body": body}


def _retryable(outcome: dict) -> bool:
    status = outcome.get("status") or 0
    return status >= 500 or status == 0 or status == 429


async def _post_with_retry(session: ClientSession, plan: dict, timeout: float, retries: int) -> dict:
    attempt = 0
    outcome: dict = {"ok": False, "status": 0, "body": "not attempted"}
    while True:
        try:
            outcome = await _post(session, plan, timeout)
        except Exception as exc:  # noqa: BLE001 - network level failure
            outcome = {"ok": False, "status": 0, "body": f"{type(exc).__name__}: {exc}"[:200]}
        if outcome.get("ok") or not _retryable(outcome) or attempt >= retries:
            outcome["attempts"] = attempt + 1
            return outcome
        attempt += 1
        await asyncio.sleep(min(3 ** attempt, 10))


async def _send_email(channel: dict, subject: str, text: str, timeout: float) -> dict:
    def _blocking() -> dict:
        host = channel.get("smtp_host", "")
        port = int(channel.get("smtp_port", 465))
        message = MIMEText(text, "plain", "utf-8")
        message["Subject"] = subject
        message["From"] = channel.get("mail_from") or channel.get("username", "")
        recipients = channel.get("mail_to", [])
        message["To"] = ", ".join(recipients if isinstance(recipients, list) else [recipients])
        user, password = channel.get("username", ""), channel.get("password", "")
        try:
            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=timeout,
                                     context=ssl.create_default_context()) as server:
                    if user:
                        server.login(user, password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(host, port, timeout=timeout) as server:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    if user:
                        server.login(user, password)
                    server.send_message(message)
        except Exception as exc:  # noqa: BLE001 - reported per channel
            return {"ok": False, "status": 0, "body": type(exc).__name__}
        return {"ok": True, "status": 0, "body": "smtp ok"}

    try:
        return await asyncio.wait_for(asyncio.to_thread(_blocking), timeout=timeout + 5)
    except asyncio.TimeoutError:
        return {"ok": False, "status": 0, "body": "smtp timeout"}


async def deliver(events: list[dict], extra_lines: list[str] | None = None,
                  session: ClientSession | None = None) -> list[dict]:
    """Send every event through every eligible channel. Never raises."""
    plans = channels()
    if not plans or not events:
        return []
    timeout = float(os.getenv("ELECTRICITY_NOTIFY_TIMEOUT", "10") or 10)
    retries = _retries()
    now = datetime.now(timezone.utc)
    results: list[dict] = []
    own_session = session is None
    if own_session:
        session = ClientSession()
    try:
        for event in events:
            alert = event.get("alert") or event
            severity = alert.get("severity", "warning")
            text = render_text(event, extra_lines)
            for index, channel in enumerate(plans):
                if not _severity_ok(channel.get("min_severity", "warning"), severity):
                    continue
                kind = channel.get("type")
                key = _channel_key(channel, index)
                if not _breaker_allows(key, now):
                    results.append({"channel": kind, "alert": alert.get("id"),
                                    "action": event.get("action"), "ok": False, "status": 0,
                                    "body": "circuit open", "skipped": True})
                    continue
                try:
                    if kind == "email":
                        outcome = await _send_email(channel, f"电费告警｜{alert.get('title', '')}",
                                                   text, timeout)
                    else:
                        plan = build_channel_payload(channel, text, alert, event.get("action", "firing"))
                        if not plan.get("url"):
                            outcome = {"ok": False, "status": 0, "body": "缺少 webhook 地址"}
                        else:
                            outcome = await _post_with_retry(session, plan, timeout, retries)
                except Exception as exc:  # noqa: BLE001 - one channel must not break the others
                    outcome = {"ok": False, "status": 0, "body": type(exc).__name__}
                _record_result(key, bool(outcome.get("ok")), outcome.get("body"), now)
                results.append({"channel": kind, "alert": alert.get("id"), "action": event.get("action"),
                                **outcome})
                if not outcome.get("ok"):
                    logging.warning("electricity notify failed channel=%s alert=%s status=%s body=%s",
                                    kind, alert.get("id"), outcome.get("status"),
                                    str(outcome.get("body"))[:120])
    finally:
        if own_session:
            await session.close()
    return results


async def send_test(session: ClientSession | None = None) -> list[dict]:
    """Send one synthetic critical message to validate the setup."""
    sample = {
        "id": "electricity:test", "rule": "test", "resource_id": "electricity",
        "severity": "critical", "title": "测试告警（手动触发）",
        "summary": "这是一条来自运维工作台电费监测模块的测试通知，收到即表示通道配置可用。",
        "observed_at": datetime.now(timezone.utc),
    }
    return await deliver([{"action": "firing", "alert": sample}],
                         extra_lines=["触发方式：POST /api/v1/alerts/test"], session=session)
