"""Tests for the standalone SMS module."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
for candidate in (HERE, PARENT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from fastapi.testclient import TestClient  # noqa: E402

import main as sms  # noqa: E402


def test_send_requires_token_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("SMS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMS_TOKEN", "secret")
    monkeypatch.delenv("SMS_PROVIDER_URL", raising=False)
    client = TestClient(sms.app)
    assert client.post("/api/v1/send", json={"phone": "138", "message": "x"}).status_code == 401
    assert client.post("/api/v1/send", json={"phone": "138", "message": "x"},
                       headers={"X-SMS-Token": "wrong"}).status_code == 401
    # provider missing -> 503
    assert client.post("/api/v1/send", json={"phone": "138", "message": "x"},
                       headers={"X-SMS-Token": "secret"}).status_code == 503


def test_send_validates_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("SMS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SMS_TOKEN", raising=False)
    client = TestClient(sms.app)
    assert client.post("/api/v1/send", json={"phone": "", "message": "x"}).status_code == 400
    assert client.post("/api/v1/send", json={"phone": "138", "message": ""}).status_code == 400


def test_history_masks_and_returns_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("SMS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SMS_TOKEN", raising=False)
    sms._audit({"at": "2026-10-17T00:00:00+00:00", "phone": "138****0000",
                "ok": True, "status": 200, "detail": "ok", "message_chars": 5})
    client = TestClient(sms.app)
    rows = client.get("/api/v1/history").json()["rows"]
    assert rows and rows[0]["ok"] is True


def test_module_contract():
    client = TestClient(sms.app)
    info = client.get("/api/v1/module").json()
    assert info["key"] == "sms" and "sms:send" in info["capabilities"]
    assert client.get("/health/live").json()["status"] == "ok"
    assert "sms" in client.get("/metrics").text
