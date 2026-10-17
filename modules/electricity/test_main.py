"""Isolated tests for the electricity module. No network and no upstream access."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from fastapi.testclient import TestClient  # noqa: E402

import collector  # noqa: E402
import main  # noqa: E402
import pytest  # noqa: E402


client = TestClient(main.app)


def test_money_and_balance_sign_rule():
    assert collector.money("12.345") == 12.35
    assert collector.money("bad") is None
    # Postpaid debt keeps the source integration's negative sign.
    assert collector.balance_of({"sumMoney": "30", "consType": "0"}) == -30.0
    assert collector.balance_of({"sumMoney": "30", "consType": "1"}) == 30.0
    assert collector.balance_of({"sumMoney": "30", "accountBalance": "12.5"}) == 12.5


def test_bills_and_daily_usage_are_sorted_and_bounded():
    bills = collector.latest_bills([
        {"month": "202608", "monthEleCost": "10", "monthEleNum": "5"},
        {"month": "202609", "monthEleCost": "20", "monthEleNum": "6"},
        {"month": "oops", "monthEleCost": "1", "monthEleNum": "1"},
        {"month": "202607", "monthEleCost": "30", "monthEleNum": "7"},
        {"month": "202606", "monthEleCost": "40", "monthEleNum": "8"},
    ])
    assert [row["month"] for row in bills] == ["202609", "202608", "202607"]

    daily = collector.daily_usage([
        {"day": "2026-10-14", "dayElePq": "1.5"},
        {"day": "2026-10-15", "dayElePq": "2"},
        {"day": "not-a-day", "dayElePq": "9"},
    ])
    assert daily == [{"day": "2026-10-15", "kwh": 2.0}, {"day": "2026-10-14", "kwh": 1.5}]


def test_business_rejects_non_ok_codes():
    assert collector.business({"code": "0000", "data": {"bizrt": {"x": 1}}}) == {"x": 1}
    try:
        collector.business({"code": "-200"})
    except RuntimeError as exc:
        assert "-200" in str(exc)
    else:
        raise AssertionError("non-ok code must raise")


def test_summary_is_unavailable_without_credentials(monkeypatch):
    monkeypatch.setattr(main.store, "credentials", lambda: {"account": "", "password_digest": "", "target_id": "", "target_meter": {}})
    summary = client.get("/api/v1/summary").json()
    assert summary["status"] == "unavailable"
    assert "尚未配置" in summary["error"]


def _snapshot(updated_at: datetime, balance: float = 54.47, errors=None):
    return {
        "updated_at": updated_at.isoformat(timespec="seconds"),
        "households": [{
            "id": "1101062733110", "name": "*潇", "balance": balance, "arrears": 0.0,
            "bills": [{"month": "202609", "cost": 84.48, "kwh": 173.0}],
            "daily": [{"day": "2026-10-15", "kwh": 5.86}, {"day": "2026-10-14", "kwh": 4.23}],
        }],
        "errors": errors or [],
    }


def test_summary_reports_fresh_data(monkeypatch):
    monkeypatch.setattr(main.store, "credentials", lambda: {"account": "1", "password_digest": "d", "target_id": "1101062733110",
                                                            "target_meter": {"consNo_dst": "1101062733110"}})
    monkeypatch.setattr(main.store, "snapshot", lambda: _snapshot(datetime.now(timezone.utc) - timedelta(minutes=5)))
    summary = client.get("/api/v1/summary").json()
    assert summary["status"] == "ok"
    assert summary["data"]["balance"] == 54.47
    assert summary["data"]["average_7d_kwh"] == pytest.approx(5.045, abs=0.01)


def test_summary_degrades_when_stale(monkeypatch):
    monkeypatch.setattr(main.store, "credentials", lambda: {"account": "1", "password_digest": "d", "target_id": "1101062733110",
                                                            "target_meter": {"consNo_dst": "1101062733110"}})
    monkeypatch.setattr(main.store, "snapshot", lambda: _snapshot(datetime.now(timezone.utc) - timedelta(hours=30)))
    summary = client.get("/api/v1/summary").json()
    assert summary["status"] == "degraded"
    assert "24 小时" in summary["error"]


def test_findings_flag_low_balance_and_pending_slices(monkeypatch):
    monkeypatch.setattr(main.store, "credentials", lambda: {"account": "1", "password_digest": "d", "target_id": "1101062733110",
                                                            "target_meter": {"consNo_dst": "1101062733110"}})
    monkeypatch.setattr(main.store, "snapshot", lambda: _snapshot(
        datetime.now(timezone.utc) - timedelta(minutes=1), balance=8.5, errors=["逐日用电：为空"]))
    findings = client.get("/api/v1/findings").json()
    assert findings["status"] == "degraded"
    titles = [item["title"] for item in findings["items"]]
    assert any("余额低于" in title for title in titles)
    assert any("待重试" in title for title in titles)
    ids = [item["id"] for item in findings["items"]]
    assert len(ids) == len(set(ids))


def test_overview_requires_configuration_and_returns_snapshot(monkeypatch):
    monkeypatch.setattr(main.store, "credentials", lambda: {"account": "", "password_digest": "", "target_id": "", "target_meter": {}})
    assert client.get("/api/v1/overview").status_code == 503

    monkeypatch.setattr(main.store, "credentials", lambda: {"account": "1", "password_digest": "d", "target_id": "1101062733110",
                                                            "target_meter": {"consNo_dst": "1101062733110"}})
    monkeypatch.setattr(main.store, "snapshot", lambda: _snapshot(datetime.now(timezone.utc)))
    body = client.get("/api/v1/overview").json()
    assert body["households"][0]["balance"] == 54.47


def test_module_contract_and_page():
    info = client.get("/api/v1/module").json()
    assert info["key"] == "electricity"
    assert "electricity:read" in info["capabilities"]
    assert client.get("/health/live").json()["status"] == "ok"
    page = client.get("/ui")
    assert page.status_code == 200
    assert "电费监测" in page.text
    assert "ops-access-token" in page.text
    # alert panel is part of the self-contained page
    assert "提醒告警" in page.text
    assert "/api/v1/alerts" in page.text or "alerts/test" in page.text


# --------------------------------------------------------------------------- #
# alert engine
# --------------------------------------------------------------------------- #
import alerts as alerting  # noqa: E402
import notify  # noqa: E402
import storage  # noqa: E402

TZ = alerting.TZ


def _cfg(**overrides):
    value = alerting.config()
    value.update(overrides)
    return value


def _ctx(now, balance=54.47, arrears=None, daily=None, errors=None, age_minutes=5,
         updated=True, history=None):
    snapshot = {
        "updated_at": (now - timedelta(minutes=age_minutes)).isoformat(timespec="seconds") if updated else None,
        "households": [{
            "id": "1101062733110", "name": "*潇", "balance": balance, "arrears": arrears, "due": 0.0,
            "bills": [{"month": "202609", "cost": 84.48, "kwh": 173.0}],
            "daily": daily if daily is not None else [{"day": "2026-10-15", "kwh": 5.0}],
        }],
        "errors": errors or [],
        "_balance_history": history or [],
    }
    return alerting.build_context(snapshot, True, now, alerting.config())


def _ids(alerts):
    return sorted(item["id"] for item in alerts)


def test_rules_flag_critical_and_warning_balances():
    now = datetime.now(timezone.utc)
    critical = alerting.evaluate_rules(_ctx(now, balance=3.2), now, _cfg())
    assert alerting.RULE_BALANCE_CRITICAL in _ids(critical)
    assert alerting.RULE_BALANCE_LOW not in _ids(critical)

    warning = alerting.evaluate_rules(_ctx(now, balance=15.0), now, _cfg())
    assert alerting.RULE_BALANCE_LOW in _ids(warning)
    assert alerting.RULE_BALANCE_CRITICAL not in _ids(warning)

    healthy = alerting.evaluate_rules(_ctx(now, balance=300.0), now, _cfg())
    assert _ids(healthy) == []


def test_rules_flag_arrears_stale_and_slice_retry():
    now = datetime.now(timezone.utc)
    ctx = _ctx(now, balance=120.0, arrears=38.5, age_minutes=60 * 30, errors=["逐日用电：为空"])
    items = alerting.evaluate_rules(ctx, now, _cfg())
    ids = _ids(items)
    assert alerting.RULE_ARREARS in ids
    assert alerting.RULE_STALE in ids
    assert any(item.startswith(alerting.RULE_SLICE_RETRY) for item in ids)


def test_depletion_rule_uses_kwh_price_estimate():
    now = datetime.now(timezone.utc)
    # 5 kWh/day * 0.5 元 = 2.5 元/天 → 54 元 可用约 21 天
    ctx = _ctx(now, balance=54.0)
    assert ctx["remaining_days"] == pytest.approx(21.6, abs=0.1)
    assert alerting.RULE_DEPLETION not in _ids(alerting.evaluate_rules(ctx, now, _cfg()))
    assert alerting.RULE_DEPLETION in _ids(alerting.evaluate_rules(ctx, now, _cfg(depletion_days=30)))


def test_depletion_prefers_measured_burn_rate():
    now = datetime.now(timezone.utc)
    history = [
        {"at": (now - timedelta(days=4)).isoformat(timespec="seconds"), "balance": 100.0, "id": "x"},
        {"at": now.isoformat(timespec="seconds"), "balance": 90.0, "id": "x"},
    ]
    assert alerting.measured_burn_rate(history) == pytest.approx(2.5, abs=0.01)
    assert alerting.measured_burn_rate(history[:1]) is None
    # recharge (balance went up) must not produce a bogus burn rate
    assert alerting.measured_burn_rate([history[0], {"at": history[1]["at"], "balance": 200.0}]) is None

    ctx = _ctx(now, balance=54.0, history=history)
    assert ctx["estimate_source"] == "measured"
    assert ctx["estimated_daily_cost"] == pytest.approx(2.5, abs=0.01)


def test_daily_spike_rule_thresholds():
    now = datetime.now(timezone.utc)
    daily = [{"day": "2026-10-15", "kwh": 20.0}] + [{"day": f"2026-10-{day:02d}", "kwh": 2.0} for day in range(1, 7)]
    assert alerting.RULE_DAILY_SPIKE in _ids(alerting.evaluate_rules(_ctx(now, daily=daily), now, _cfg()))
    # below the absolute floor → no alert even if the ratio is high
    tiny = [{"day": "2026-10-15", "kwh": 1.0}] + [{"day": f"2026-10-{day:02d}", "kwh": 0.1} for day in range(1, 7)]
    assert alerting.RULE_DAILY_SPIKE not in _ids(alerting.evaluate_rules(_ctx(now, daily=tiny), now, _cfg()))


def test_state_machine_fires_once_then_repeats_on_interval():
    now = datetime.now(timezone.utc)
    cfg = _cfg(repeat_interval_seconds=3600, pending_observations=1)
    ctx = _ctx(now, balance=15.0)

    first = alerting.evaluate(ctx, {}, now, cfg)
    assert len(first["events"]) == 1
    assert first["events"][0]["action"] == "firing"
    entry = first["state"]["active"][alerting.RULE_BALANCE_LOW]
    assert entry["status"] == "firing"
    assert entry["notify_count"] == 1

    quiet = alerting.evaluate(ctx, first["state"], now + timedelta(minutes=5), cfg)
    assert quiet["events"] == []
    assert quiet["state"]["active"][alerting.RULE_BALANCE_LOW]["notify_count"] == 1

    repeat = alerting.evaluate(ctx, quiet["state"], now + timedelta(hours=2), cfg)
    assert [event["action"] for event in repeat["events"]] == ["re-fire"]
    assert repeat["state"]["active"][alerting.RULE_BALANCE_LOW]["notify_count"] == 2


def test_state_machine_emits_single_resolved_event():
    now = datetime.now(timezone.utc)
    cfg = _cfg(repeat_interval_seconds=3600)
    fired = alerting.evaluate(_ctx(now, balance=15.0), {}, now, cfg)
    recovered = alerting.evaluate(_ctx(now, balance=200.0), fired["state"], now + timedelta(hours=1), cfg)
    assert recovered["alerts"] == []
    assert [item["id"] for item in recovered["resolved"]] == [alerting.RULE_BALANCE_LOW]
    assert recovered["state"]["active"] == {}

    again = alerting.evaluate(_ctx(now, balance=200.0), recovered["state"], now + timedelta(hours=2), cfg)
    assert again["resolved"] == []


def test_pending_observations_delays_first_notification():
    now = datetime.now(timezone.utc)
    cfg = _cfg(pending_observations=2)
    ctx = _ctx(now, balance=15.0)
    first = alerting.evaluate(ctx, {}, now, cfg)
    assert first["events"] == []
    assert first["state"]["active"][alerting.RULE_BALANCE_LOW]["status"] == "pending"
    second = alerting.evaluate(ctx, first["state"], now + timedelta(minutes=30), cfg)
    assert len(second["events"]) == 1


def test_quiet_hours_suppress_warning_but_not_critical():
    night = datetime(2026, 10, 17, 23, 30, tzinfo=TZ)
    cfg = _cfg(quiet_hours="23:00-07:00")
    assert alerting.in_quiet_hours("23:00-07:00", night) is True
    assert alerting.in_quiet_hours("23:00-07:00", night.replace(hour=12, minute=0)) is False
    assert alerting.in_quiet_hours("09:00-18:00", night.replace(hour=10, minute=0)) is True

    warning = alerting.evaluate(_ctx(night, balance=15.0), {}, night, cfg)
    assert warning["alerts"] and warning["events"] == []

    critical = alerting.evaluate(_ctx(night, balance=2.0), {}, night, cfg)
    assert [event["action"] for event in critical["events"]] == ["firing"]


def test_silence_blocks_notifications_until_expiry():
    now = datetime.now(timezone.utc)
    cfg = _cfg()
    ctx = _ctx(now, balance=15.0)
    state = alerting.apply_silence({}, alerting.RULE_BALANCE_LOW, 12, now)
    silenced = alerting.evaluate(ctx, state, now, cfg)
    assert silenced["events"] == []
    assert alerting.is_silenced(silenced["state"], alerting.RULE_BALANCE_LOW, now) is True
    assert silenced["alerts"], "silenced alerts stay visible in the workbench"

    after = alerting.evaluate(ctx, silenced["state"], now + timedelta(hours=13), cfg)
    assert [event["action"] for event in after["events"]] == ["firing"]


def test_record_balance_history_is_bounded_and_deduped():
    now = datetime.now(timezone.utc)
    snapshot = {"households": [{"id": "x", "balance": 42.5}]}
    state = alerting.record_balance(snapshot, {}, now)
    assert state["balance_history"] == [{"at": now.isoformat(timespec="seconds"), "balance": 42.5, "id": "x"}]
    state = alerting.record_balance(snapshot, state, now + timedelta(hours=12))
    assert len(state["balance_history"]) == 1  # same value: keep the newest only
    state = alerting.record_balance({"households": [{"id": "x", "balance": 40.0}]}, state, now + timedelta(days=1))
    assert len(state["balance_history"]) == 2


# --------------------------------------------------------------------------- #
# notification channels
# --------------------------------------------------------------------------- #
def test_channels_from_json_and_shorthand(monkeypatch):
    monkeypatch.delenv("ELECTRICITY_NOTIFY_CHANNELS", raising=False)
    for name in ("FEISHU", "WECOM", "DINGTALK", "SERVERCHAN", "BARK", "NTFY", "WEBHOOK"):
        monkeypatch.delenv(f"ELECTRICITY_NOTIFY_{name}", raising=False)
    assert notify.channels() == []
    assert notify.enabled() is False

    monkeypatch.setenv("ELECTRICITY_NOTIFY_CHANNELS", json.dumps([
        {"type": "feishu", "webhook": "https://open.feishu.cn/hook/x", "min_severity": "info"},
        {"type": "wecom", "webhook": "https://qyapi.weixin.qq.com/x", "enabled": False},
    ]))
    monkeypatch.setenv("ELECTRICITY_NOTIFY_BARK", "https://api.day.app/key")
    kinds = [item["type"] for item in notify.channels()]
    assert kinds == ["feishu", "bark"]  # disabled entry dropped, shorthand appended

    monkeypatch.setenv("ELECTRICITY_NOTIFY_CHANNELS", "not-json")
    assert [item["type"] for item in notify.channels()] == ["bark"]


def test_channel_severity_filter():
    assert notify._severity_ok("warning", "info") is False
    assert notify._severity_ok("warning", "warning") is True
    assert notify._severity_ok("info", "info") is True
    assert notify._severity_ok("critical", "warning") is False


def test_feishu_payload_is_signed_and_keeps_body():
    alert = {"id": "electricity:test", "severity": "critical", "title": "余额不足", "summary": "立即充值"}
    text = notify.render_text({"action": "firing", "alert": alert})
    payload = notify.build_channel_payload(
        {"type": "feishu", "webhook": "https://open.feishu.cn/hook/x", "secret": "s3cr3t"}, text, alert, "firing")
    assert payload["method"] == "POST"
    # v0.2.1: the default is an interactive card; the text fallback is opt-in.
    assert payload["json"]["msg_type"] == "interactive"
    body = json.dumps(payload["json"], ensure_ascii=False)
    assert "余额不足" in body
    assert payload["json"]["timestamp"] and payload["json"]["sign"]

    fallback = notify.build_channel_payload(
        {"type": "feishu", "webhook": "https://open.feishu.cn/hook/x", "card": False}, text, alert, "firing")
    assert fallback["json"]["msg_type"] == "text"
    assert "余额不足" in fallback["json"]["content"]["text"]

    unsigned = notify.build_channel_payload(
        {"type": "feishu", "webhook": "https://open.feishu.cn/hook/x"}, text, alert, "firing")
    assert "sign" not in unsigned["json"]



def test_other_channel_payloads():
    alert = {"id": "electricity:low", "severity": "warning", "title": "余额偏低", "summary": "请充值"}
    text = notify.render_text({"action": "resolved", "alert": alert})
    wecom = notify.build_channel_payload({"type": "wecom", "webhook": "https://x"}, text, alert, "resolved")
    assert wecom["json"]["msgtype"] == "markdown"
    dingtalk = notify.build_channel_payload({"type": "dingtalk", "webhook": "https://x"}, text, alert, "resolved")
    assert dingtalk["json"]["markdown"]["title"]
    sc = notify.build_channel_payload({"type": "serverchan", "sendkey": "SCT123"}, text, alert, "firing")
    assert sc["url"].endswith("/SCT123.send")
    bark = notify.build_channel_payload({"type": "bark", "webhook": "https://api.day.app/k"}, text, alert, "firing")
    assert bark["json"]["group"] == "电费监测"
    ntfy = notify.build_channel_payload({"type": "ntfy", "webhook": "https://ntfy.sh", "topic": "power"}, text, alert, "firing")
    assert ntfy["url"] == "https://ntfy.sh/power" and ntfy["headers"]["Title"]
    hook = notify.build_channel_payload({"type": "webhook", "webhook": "https://example.com/hook"}, text, alert, "firing")
    assert hook["json"]["source"] == "electricity" and hook["json"]["alert"]["id"] == alert["id"]


def test_deliver_without_channels_is_noop(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_NOTIFY_CHANNELS", "[]")
    for name in ("FEISHU", "WECOM", "DINGTALK", "SERVERCHAN", "BARK", "NTFY", "WEBHOOK"):
        monkeypatch.delenv(f"ELECTRICITY_NOTIFY_{name}", raising=False)
    alert = {"id": "electricity:low", "severity": "warning", "title": "t", "summary": "s"}
    assert asyncio.run(notify.deliver([{"action": "firing", "alert": alert}])) == []


# --------------------------------------------------------------------------- #
# new API surface
# --------------------------------------------------------------------------- #
def _patch_store(monkeypatch, balance=54.47, arrears=None, daily=None, snapshot=None):
    monkeypatch.setattr(main.store, "credentials", lambda: {"account": "1", "password_digest": "d",
                                                            "target_id": "1101062733110",
                                                            "target_meter": {"consNo_dst": "1101062733110"}})
    monkeypatch.setattr(main.store, "snapshot",
                        lambda: snapshot or _snapshot(datetime.now(timezone.utc), balance=balance))
    monkeypatch.setattr(main.store, "alert_state", lambda: {})
    monkeypatch.setattr(main.store, "alert_history", lambda limit=20: [])
    monkeypatch.setattr(main.store, "daily_history", lambda days=120: [])
    monkeypatch.setattr(main.store, "monthly_history", lambda months=24: [])
    monkeypatch.setattr(main.store, "list_raw", lambda limit=40: [])


def test_alerts_api_returns_items_and_context(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_NOTIFY_CHANNELS", "[]")
    _patch_store(monkeypatch, balance=12.0)
    body = client.get("/api/v1/alerts").json()
    assert body["source"] == "electricity"
    assert body["counts"]["warning"] >= 1
    assert any(item["id"] == alerting.RULE_BALANCE_LOW for item in body["items"])
    assert body["context"]["balance"] == 12.0
    assert "remaining_days" in body["context"]
    assert body["channels"] == []


def test_alerts_rules_api_documents_thresholds():
    body = client.get("/api/v1/alerts/rules").json()
    ids = [item["id"] for item in body["rules"]]
    assert alerting.RULE_BALANCE_CRITICAL in ids
    assert alerting.RULE_DEPLETION in ids
    assert body["thresholds"]["low_balance"] >= 0
    assert isinstance(body["channel_count"], int)


def test_silence_api_writes_state(monkeypatch):
    saved = {}
    monkeypatch.setattr(main.store, "alert_state", lambda: {})
    monkeypatch.setattr(main.store, "save_alert_state", lambda value: saved.update(value))
    ok = client.post("/api/v1/alerts/silence", json={"id": "*", "hours": 6})
    assert ok.status_code == 200
    assert saved["silences"]["*"]

    released = client.post("/api/v1/alerts/unsilence", json={"id": "*"})
    assert released.status_code == 200
    assert saved["silences"] == {}

    assert client.post("/api/v1/alerts/silence", json={"hours": 0}).status_code == 400
    assert client.post("/api/v1/alerts/silence", json={"id": "other:alert"}).status_code == 400


def test_test_notification_requires_channels(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_NOTIFY_CHANNELS", "[]")
    for name in ("FEISHU", "WECOM", "DINGTALK", "SERVERCHAN", "BARK", "NTFY", "WEBHOOK"):
        monkeypatch.delenv(f"ELECTRICITY_NOTIFY_{name}", raising=False)
    assert client.post("/api/v1/alerts/test").status_code == 503


def test_findings_use_the_same_rule_engine(monkeypatch):
    _patch_store(monkeypatch, balance=8.5)
    findings = client.get("/api/v1/findings").json()
    assert findings["status"] == "degraded"
    assert any(item["id"] == alerting.RULE_BALANCE_LOW for item in findings["items"])
    # findings must not carry alert-engine extras (contract is extra="forbid")
    assert set(findings["items"][0]) <= {"id", "resource_id", "severity", "title", "summary", "observed_at", "url"}


def test_summary_exposes_alert_rollup(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_NOTIFY_CHANNELS", "[]")
    _patch_store(monkeypatch, balance=12.0)
    summary = client.get("/api/v1/summary").json()
    assert summary["status"] == "ok"
    assert summary["data"]["alerts"] >= 1
    assert summary["data"]["notify_channels"] == 0
    assert "remaining_days" in summary["data"]


# --------------------------------------------------------------------------- #
# v0.2.1 — retry / breaker / scheduling
# --------------------------------------------------------------------------- #
def test_next_run_delay_uses_fixed_job_start(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_JOB_START_TIME", "07:30")
    now = datetime(2026, 10, 17, 9, 0, tzinfo=alerting.TZ)
    delay = main.next_run_delay(now)
    expected = (datetime(2026, 10, 17, 19, 30, tzinfo=alerting.TZ) - now).total_seconds()
    assert delay == pytest.approx(expected, abs=1)

    # right before the window start the delay is short
    before = datetime(2026, 10, 17, 7, 25, tzinfo=alerting.TZ)
    assert main.next_run_delay(before) == pytest.approx(300, abs=1)


def test_next_run_delay_falls_back_to_interval(monkeypatch):
    monkeypatch.delenv("ELECTRICITY_JOB_START_TIME", raising=False)
    assert main.next_run_delay(datetime.now(timezone.utc)) == pytest.approx(12 * 3600, abs=1)
    # an invalid value is ignored rather than crashing the scheduler
    monkeypatch.setenv("ELECTRICITY_JOB_START_TIME", "7点半")
    assert main.next_run_delay(datetime.now(timezone.utc)) == pytest.approx(12 * 3600, abs=1)


def test_collect_window_gate(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_COLLECT_WINDOW", "06:00-23:30")
    assert main._in_collect_window(datetime(2026, 10, 17, 9, 0, tzinfo=alerting.TZ)) is True
    assert main._in_collect_window(datetime(2026, 10, 17, 3, 0, tzinfo=alerting.TZ)) is False
    monkeypatch.delenv("ELECTRICITY_COLLECT_WINDOW", raising=False)
    assert main._in_collect_window(datetime(2026, 10, 17, 3, 0, tzinfo=alerting.TZ)) is True


class _FakeResponse:
    def __init__(self, status, text):
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        index = min(self.calls - 1, len(self.responses) - 1)
        return self.responses[index]


def test_post_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_NOTIFY_CHANNELS", "[]")
    session = _FakeSession([_FakeResponse(500, "boom"), _FakeResponse(200, '{"code":0}')])
    plan = {"method": "POST", "url": "https://example.com/hook", "json": {}}
    outcome = asyncio.run(notify._post_with_retry(session, plan, 5, retries=2))
    assert outcome["ok"] is True
    assert outcome["attempts"] == 2
    assert session.calls == 2


def test_post_does_not_retry_client_error():
    session = _FakeSession([_FakeResponse(400, "bad request")])
    plan = {"method": "POST", "url": "https://example.com/hook", "json": {}}
    outcome = asyncio.run(notify._post_with_retry(session, plan, 5, retries=2))
    assert outcome["ok"] is False
    assert outcome["attempts"] == 1


def test_post_detects_business_error_in_200_body():
    session = _FakeSession([_FakeResponse(200, '{"code":9499,"msg":"sign error"}')])
    plan = {"method": "POST", "url": "https://example.com/hook", "json": {}}
    outcome = asyncio.run(notify._post_with_retry(session, plan, 5, retries=0))
    assert outcome["ok"] is False


def test_circuit_breaker_opens_and_recovers(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_NOTIFY_BREAKER_FAILURES", "2")
    monkeypatch.setenv("ELECTRICITY_NOTIFY_BREAKER_SECONDS", "600")
    now = datetime.now(timezone.utc)
    notify.import_breaker({})
    notify._record_result("feishu#0", False, "timeout", now)
    assert notify._breaker_allows("feishu#0", now) is True
    notify._record_result("feishu#0", False, "timeout", now)
    assert notify._breaker_allows("feishu#0", now) is False
    assert notify._breaker_allows("feishu#0", now + timedelta(seconds=601)) is True
    notify._record_result("feishu#0", True, None, now + timedelta(seconds=601))
    assert notify.export_breaker()["feishu#0"]["failures"] == 0


def test_feishu_card_payload_carries_severity_colour_and_link(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_ALERT_URL", "https://ops.example/extensions/electricity")
    alert = {"id": "electricity:balance-low", "severity": "critical", "title": "余额不足",
             "summary": "请立即充值", "details": {"remaining_days": 2.5}}
    text = notify.render_text({"action": "firing", "alert": alert})
    plan = notify.build_channel_payload({"type": "feishu", "webhook": "https://x"}, text, alert, "firing")
    assert plan["json"]["msg_type"] == "interactive"
    assert plan["json"]["card"]["header"]["template"] == "red"
    assert any(element.get("tag") == "action" for element in plan["json"]["card"]["elements"])
    assert "查看详情" in text

    plain = notify.build_channel_payload({"type": "feishu", "webhook": "https://x", "card": False},
                                         text, alert, "firing")
    assert plain["json"]["msg_type"] == "text"


# --------------------------------------------------------------------------- #
# v0.3.0 — forecasts, tiered tariff, time-of-use, heuristics, histories
# --------------------------------------------------------------------------- #
def test_forecast_usage_uses_weekday_pattern():
    daily = []
    for offset in range(14):
        day = (datetime(2026, 10, 16).date() - timedelta(days=offset))
        daily.append({"day": day.isoformat(), "kwh": 10.0 if day.weekday() >= 5 else 2.0})
    forecast = alerting.forecast_usage(daily, 7)
    assert forecast["days"] == 7
    assert forecast["method"] == "weekday-average"
    weekend = [item for item in forecast["daily_kwh"]
               if datetime.strptime(item["day"], "%Y-%m-%d").weekday() >= 5]
    weekday = [item for item in forecast["daily_kwh"]
               if datetime.strptime(item["day"], "%Y-%m-%d").weekday() < 5]
    assert weekend and weekday
    assert all(item["kwh"] == pytest.approx(10.0) for item in weekend)
    assert all(item["kwh"] == pytest.approx(2.0) for item in weekday)


def test_forecast_usage_empty_history():
    assert alerting.forecast_usage([], 7)["method"] == "none"
    assert alerting.forecast_usage([], 7)["total_kwh"] is None


def _steps_fixture():
    return [
        {"stage": 1, "used": 2000.0, "remain": 760.0, "price": 0.4883, "current": True},
        {"stage": 2, "used": 0.0, "remain": 600.0, "price": 0.5383, "current": False},
        {"stage": 3, "used": 0.0, "remain": 9999.0, "price": 0.7883, "current": False},
    ]


def _ctx_with_steps(now, balance, steps=None, kwh=5.0):
    snapshot = {
        "updated_at": (now - timedelta(minutes=5)).isoformat(timespec="seconds"),
        "households": [{"id": "1101062733110", "name": "*潇", "balance": balance, "arrears": 0.0, "due": 0.0,
                        "bills": [], "steps": steps or [], "tou": None,
                        "daily": [{"day": "2026-10-15", "kwh": kwh}]}],
        "errors": [], "_balance_history": [], "_daily_history": [], "_monthly_history": [],
    }
    return alerting.build_context(snapshot, True, now, alerting.config())


def test_remaining_days_is_tier_aware():
    now = datetime.now(timezone.utc)
    ctx = _ctx_with_steps(now, balance=300.0, steps=_steps_fixture(), kwh=5.0)
    assert ctx["estimate_source"] == "tiered-kwh-price"
    # tier 1: 760 kWh at 0.4883 → 371 元 capacity > 300 元 balance, so it all burns in tier 1
    assert ctx["remaining_days"] == pytest.approx(300 / (5 * 0.4883), abs=0.2)

    # a balance big enough to cross into tier 2 must cost less per kWh in tier 1 only
    ctx2 = _ctx_with_steps(now, balance=800.0, steps=_steps_fixture(), kwh=5.0)
    flat_days = 800 / (5 * 0.4883)
    assert ctx2["remaining_days"] < flat_days  # tier 2 is pricier


def test_step_enter_rule_warns_before_next_tier():
    now = datetime.now(timezone.utc)
    steps = _steps_fixture()
    steps[0]["remain"] = 60.0
    items = alerting.evaluate_rules(_ctx_with_steps(now, 500.0, steps), now, _cfg())
    step_alert = next(item for item in items if item["id"] == alerting.RULE_STEP_ENTER)
    assert step_alert["severity"] == "info"       # entering tier 2
    assert step_alert["value"] == pytest.approx(60.0)

    steps[0]["remain"] = 20.0
    steps[1]["remain"] = 0.0
    for item in steps:
        item["current"] = False
    steps[1]["current"] = True
    items = alerting.evaluate_rules(_ctx_with_steps(now, 500.0, steps), now, _cfg())
    step_alert = next(item for item in items if item["id"] == alerting.RULE_STEP_ENTER)
    assert step_alert["severity"] == "warning"    # entering tier 3


def test_tou_tip_heavy_rule():
    now = datetime.now(timezone.utc)
    snapshot = {
        "updated_at": now.isoformat(timespec="seconds"),
        "households": [{"id": "x", "balance": 100.0, "arrears": 0.0, "due": 0.0, "bills": [],
                        "daily": [{"day": "2026-10-15", "kwh": 5.0}],
                        "tou": {"tip": 30.0, "peak": 90.0, "flat": 60.0, "valley": 20.0, "total": 200.0}}],
        "errors": [], "_balance_history": [],
    }
    ctx = alerting.build_context(snapshot, True, now, alerting.config())
    items = alerting.evaluate_rules(ctx, now, _cfg())
    alert = next(item for item in items if item["id"] == alerting.RULE_TOU_TIP_HEAVY)
    assert alert["value"] == pytest.approx(0.6, abs=0.01)

    snapshot["households"][0]["tou"] = {"tip": 5.0, "peak": 5.0, "flat": 80.0, "valley": 60.0}
    ctx = alerting.build_context(snapshot, True, now, alerting.config())
    assert alerting.RULE_TOU_TIP_HEAVY not in [item["id"] for item in alerting.evaluate_rules(ctx, now, _cfg())]


def test_extract_steps_from_realistic_payload():
    payload = {"code": "0000", "data": {"bizrt": {"stepEleList": [
        {"stepNo": "1", "stepUsedPq": "2000", "stepRemainPq": "760", "stepPrice": "0.4883"},
        {"stepNo": "2", "stepUsedPq": "0", "stepRemainPq": "600", "stepPrice": "0.5383"},
    ]}}}
    steps = collector.extract_steps(payload)
    assert [item["stage"] for item in steps] == [1, 2]
    assert steps[0]["remain"] == pytest.approx(760.0)
    assert steps[0]["current"] is True
    assert steps[0]["price"] == pytest.approx(0.4883)


def test_extract_steps_rejects_unrelated_payload():
    payload = {"data": {"list": [{"month": "202609", "monthEleNum": "173"}]}}
    assert collector.extract_steps(payload) == []


def test_extract_tou_from_realistic_payload():
    payload = {"data": {"bizrt": {"elePq": {"tipElePq": "12.5", "peakElePq": "88.0",
                                            "flatElePq": "60.0", "valleyElePq": "12.5"}}}}
    tou = collector.extract_tou(payload)
    assert tou["tip"] == pytest.approx(12.5)
    assert tou["total"] == pytest.approx(173.0)
    assert collector.extract_tou({"data": {"bizrt": {"x": 1}}}) is None


def test_parse_steps_never_breaks_collection(monkeypatch):
    """A missing/broken tiered payload must simply produce no steps."""
    assert collector.extract_steps(None) == []
    assert collector.extract_tou(None) is None
    assert collector.extract_tou([{"tip": "oops", "peak": None}]) is None


# --------------------------------------------------------------------------- #
# storage: histories, rotation, redaction, raw capture
# --------------------------------------------------------------------------- #
def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("ELECTRICITY_DATA_DIR", str(tmp_path))
    return storage.ElectricityStore()


def test_alert_history_rotation(tmp_path, monkeypatch):
    monkeypatch.setenv("ELECTRICITY_HISTORY_MAX_BYTES", "200")
    monkeypatch.setenv("ELECTRICITY_HISTORY_KEEP", "2")
    store = _store(tmp_path, monkeypatch)
    for index in range(20):
        store.append_alert_history([{"at": f"2026-10-17T00:00:{index:02d}", "title": "x" * 20}])
    assert (tmp_path / "alert_history.jsonl").exists()
    assert (tmp_path / "alert_history.jsonl.1").exists()
    assert store.alert_history(5)


def test_daily_history_dedupes_and_sorts(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.append_daily_history([{"day": "2026-10-15", "kwh": 5.0}, {"day": "2026-10-16", "kwh": 6.0}])
    store.append_daily_history([{"day": "2026-10-16", "kwh": 7.5}])
    rows = store.daily_history(10)
    assert [row["day"] for row in rows] == ["2026-10-16", "2026-10-15"]
    assert rows[0]["kwh"] == pytest.approx(7.5)


def test_monthly_history_dedupes(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.append_monthly_history([{"month": "202609", "cost": 84.48}, {"month": "202608", "cost": 90.0}])
    store.append_monthly_history([{"month": "202609", "cost": 85.0}])
    rows = store.monthly_history(12)
    assert [row["month"] for row in rows] == ["202609", "202608"]
    assert rows[0]["cost"] == pytest.approx(85.0)


def test_history_retention_trim(tmp_path, monkeypatch):
    monkeypatch.setenv("ELECTRICITY_DAILY_HISTORY_DAYS", "10")
    store = _store(tmp_path, monkeypatch)
    old = (datetime.now(alerting.TZ) - timedelta(days=40)).date().isoformat()
    fresh = datetime.now(alerting.TZ).date().isoformat()
    store.append_daily_history([{"day": old, "kwh": 1.0}, {"day": fresh, "kwh": 2.0}])
    removed = store.trim_history()
    assert removed["daily"] == 1
    assert [row["day"] for row in store.daily_history(10)] == [fresh]


def test_redact_masks_identity_but_keeps_shape():
    payload = {"data": {"bizrt": {"acctId": "1234567890", "consNo": "1101062733110",
                                  "sumMoney": "52.49", "list": [{"day": "20261015", "dayElePq": "5.86"}]}}}
    masked = storage.redact(payload)
    assert masked["data"]["bizrt"]["acctId"] != "1234567890"
    assert masked["data"]["bizrt"]["sumMoney"] == "52.49"
    assert masked["data"]["bizrt"]["list"][0]["dayElePq"] == "5.86"
    assert "1101062733110" not in json.dumps(masked, ensure_ascii=False)


def test_describe_shape_reports_keys():
    shape = storage.describe_shape({"data": {"bizrt": {"list": [{"a": 1}], "n": 2}}})
    assert shape["type"] == "object"
    assert "data" in shape["keys"]


def test_raw_capture_respects_flag_and_redacts(tmp_path, monkeypatch):
    monkeypatch.delenv("ELECTRICITY_RAW_CAPTURE", raising=False)
    store = _store(tmp_path, monkeypatch)
    assert store.raw_capture_enabled() is False
    assert store.save_raw("balance", {"a": 1}, {"acctId": "9" * 10}) is None
    assert store.list_raw() == []

    monkeypatch.setenv("ELECTRICITY_RAW_CAPTURE", "true")
    store = _store(tmp_path, monkeypatch)
    name = store.save_raw("balance", {"acctId": "1234567890"},
                          {"code": "0000", "data": {"bizrt": {"sumMoney": "52.49"}}})
    assert name and name.startswith("balance-")
    assert store.list_raw()[0]["name"] == name
    assert "1234567890" not in json.dumps(store.read_raw(name), ensure_ascii=False)
    assert "bizrt" in json.dumps(store.raw_shape(name))


def test_raw_name_validation_blocks_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("ELECTRICITY_RAW_CAPTURE", "true")
    store = _store(tmp_path, monkeypatch)
    assert store.read_raw("../../etc/passwd") is None
    assert store.read_raw("nope.txt") is None


# --------------------------------------------------------------------------- #
# v0.4.0 — budget and session churn rules
# --------------------------------------------------------------------------- #
def _ctx_with_month(now, cost, budget_probe=True):
    snapshot = {
        "updated_at": now.isoformat(timespec="seconds"),
        "month_label": now.astimezone(alerting.TZ).strftime("%Y%m"),
        "month_cost": cost,
        "households": [{"id": "x", "balance": 500.0, "arrears": 0.0, "due": 0.0, "bills": [],
                        "daily": [{"day": "2026-10-15", "kwh": 5.0}], "steps": [], "tou": None}],
        "errors": [], "_balance_history": [],
    }
    return alerting.build_context(snapshot, True, now, alerting.config())


def test_budget_rule_warns_then_criticals():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)  # day 10 of 31 → factor 3.1
    ctx = _ctx_with_month(now, 40.0)
    assert ctx["month_cost_projection"] == pytest.approx(124.0, abs=0.5)

    cfg = _cfg(monthly_budget=200.0)
    assert alerting.RULE_BUDGET not in [item["id"] for item in alerting.evaluate_rules(ctx, now, cfg)]

    cfg = _cfg(monthly_budget=100.0)
    items = alerting.evaluate_rules(ctx, now, cfg)
    budget = next(item for item in items if item["id"] == alerting.RULE_BUDGET)
    assert budget["severity"] == "warning"

    over = alerting.evaluate_rules(_ctx_with_month(now, 130.0), now, cfg)
    assert next(item for item in over if item["id"] == alerting.RULE_BUDGET)["severity"] == "critical"


def test_budget_rule_disabled_when_zero():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    ctx = _ctx_with_month(now, 999.0)
    assert alerting.RULE_BUDGET not in [item["id"] for item in alerting.evaluate_rules(ctx, now, _cfg())]


def test_relogin_storm_rule():
    now = datetime.now(timezone.utc)
    snapshot = {
        "updated_at": now.isoformat(timespec="seconds"), "relogin_count": 6,
        "households": [{"id": "x", "balance": 100.0, "arrears": 0.0, "due": 0.0, "bills": [],
                        "daily": [{"day": "2026-10-15", "kwh": 5.0}]}],
        "errors": [], "_balance_history": [],
    }
    ctx = alerting.build_context(snapshot, True, now, alerting.config())
    assert alerting.RULE_RELOGIN_STORM in [item["id"] for item in alerting.evaluate_rules(ctx, now, _cfg())]
    ctx["relogin_count"] = 1
    assert alerting.RULE_RELOGIN_STORM not in [item["id"] for item in alerting.evaluate_rules(ctx, now, _cfg())]


def test_relogin_counter_window(monkeypatch):
    collector._login_events.clear()
    collector._note_login()
    assert collector.relogin_count() == 1
    collector._login_events.insert(0, datetime.now(timezone.utc) - timedelta(hours=20))
    assert collector.relogin_count() == 1
    collector._login_events.clear()


# --------------------------------------------------------------------------- #
# v0.5.0 — new API surface
# --------------------------------------------------------------------------- #
def test_usage_api_returns_steps_and_forecast(monkeypatch):
    now = datetime.now(timezone.utc)
    snapshot = {
        "updated_at": now.isoformat(timespec="seconds"), "month_label": "202610", "month_cost": 40.0,
        "households": [{"id": "x", "balance": 300.0, "arrears": 0.0, "due": 0.0,
                        "bills": [{"month": "202609", "cost": 84.48, "kwh": 173.0}],
                        "daily": [{"day": "2026-10-15", "kwh": 5.0}],
                        "steps": _steps_fixture(),
                        "tou": {"tip": 10.0, "peak": 20.0, "flat": 30.0, "valley": 40.0}}],
        "errors": [],
    }
    _patch_store(monkeypatch, snapshot=snapshot)
    body = client.get("/api/v1/usage").json()
    assert body["current_step"]["stage"] == 1
    assert body["tou"]["valley"] == 40.0
    assert body["forecast"]["days"] == 7
    assert body["monthly_budget"] is None


def test_diagnostics_raw_api_lists_capture_state(monkeypatch):
    monkeypatch.delenv("ELECTRICITY_RAW_CAPTURE", raising=False)
    _patch_store(monkeypatch)
    body = client.get("/api/v1/diagnostics/raw").json()
    assert body["capture_enabled"] is False
    assert body["files"] == []


def test_diagnostics_raw_content_rejects_bad_name(monkeypatch):
    _patch_store(monkeypatch)
    assert client.get("/api/v1/diagnostics/raw/content?name=../../etc/passwd").status_code in (400, 404)


def test_rules_api_exposes_scheduling_config(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_COLLECT_WINDOW", "06:00-23:30")
    monkeypatch.setenv("ELECTRICITY_JOB_START_TIME", "07:30")
    body = client.get("/api/v1/alerts/rules").json()
    assert body["collect_window"] == "06:00-23:30"
    assert body["job_start_time"] == "07:30"
    ids = [item["id"] for item in body["rules"]]
    assert alerting.RULE_BUDGET in ids and alerting.RULE_STEP_ENTER in ids


def test_report_csv_export(monkeypatch):
    monkeypatch.setattr(main.store, "daily_history", lambda days=365: [
        {"day": "2026-10-15", "kwh": 5.86}, {"day": "2026-09-30", "kwh": 4.0}])
    monkeypatch.setattr(main.store, "monthly_history", lambda months=24: [
        {"month": "202610", "kwh": 100.0, "cost": 52.0}])
    monkeypatch.setattr(main.store, "alert_history", lambda limit=500: [
        {"at": "2026-10-16T09:00:00", "action": "firing", "title": "余额偏低"}])
    response = client.get("/api/v1/report.csv?month=202610")
    assert response.status_code == 200
    text = response.text
    assert "日用电" in text and "2026-10-15" in text
    assert "2026-09-30" not in text          # other months are excluded
    assert "月度账单" in text and "告警次数" in text
    assert "attachment" in response.headers["content-disposition"]


def test_page_contains_new_sections():
    page = client.get("/ui").text
    for fragment in ("提醒告警", "阶梯电价", "分时用电", "用电趋势与预测", "采集诊断",
                     "ops-module-download-request", "prepaid"):
        assert fragment in page


