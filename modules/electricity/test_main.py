"""Isolated tests for the electricity module. No network and no upstream access."""
from __future__ import annotations

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
