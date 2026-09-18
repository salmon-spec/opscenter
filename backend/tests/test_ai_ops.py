"""Contracts for the first read-only AI Ops rollout."""
import json

import pytest
from fastapi.testclient import TestClient

from app import ai_ops, database
from app.api_keys import hash_api_key
from app.main import Base, SessionLocal, app, engine
from app.models import ApiKey


client = TestClient(app)
PLAIN_KEY = "oc_rt_ai-ops-test"
AUTH = {"Authorization": f"Bearer {PLAIN_KEY}"}


@pytest.fixture(autouse=True)
def clean_database():
    assert engine is database.engine
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.add(ApiKey(
            name="AI ops test",
            key_hash=hash_api_key(PLAIN_KEY),
            prefix=PLAIN_KEY[:10],
            scope="read",
            enabled=True,
        ))
        db.commit()
    yield


def test_ai_ops_requires_api_key_and_is_dry_run():
    assert client.get("/api/v2/ai-ops/status").status_code == 401
    response = client.get("/api/v2/ai-ops/status", headers=AUTH)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["mode"] == "read_only_dry_run"
    assert data["execution_enabled"] is False
    assert data["approval_enabled"] is False
    assert "restart_test_workload" in data["allowed_recommendations"]


def test_ai_ops_analyze_normalises_unknown_actions_and_redacts(monkeypatch):
    monkeypatch.setattr(ai_ops, "_build_context", lambda *_args: {
        "scope": {"execution_mode": "read_only_dry_run"},
        "summary": {"active_incident_count": 1},
        "hosts": [], "services": [], "active_incidents": [],
        "warnings": ["persisted snapshot"],
    })
    monkeypatch.setattr(ai_ops, "chat", lambda *_args, **_kwargs: {
        "content": json.dumps({
            "conclusion": "服务连接失败，password=secret-value 不应出现",
            "evidence": [{"source": "incident", "fact": "connection_refused"}],
            "confidence": 0.74,
            "recommended_actions": [{
                "action": "shell",
                "target": "rm -rf /",
                "reason": "token=secret-token",
                "risk": "R0",
                "approval_required": False,
            }],
        }),
        "model": "test-model",
        "request_id": "req-test",
    })

    response = client.post(
        "/api/v2/ai-ops/analyze",
        headers=AUTH,
        json={"question": "检查服务为什么异常", "service_key": "gitea"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    action = body["data"]["analysis"]["recommended_actions"][0]
    assert body["data"]["mode"] == "read_only_dry_run"
    assert action["action"] == "observe"
    assert action["risk"] == "R3"
    assert action["approval_required"] is True
    assert action["execution"] == "dry_run"
    assert "secret-value" not in response.text
    assert "secret-token" not in response.text


def test_ai_ops_analyze_rejects_blank_question():
    response = client.post(
        "/api/v2/ai-ops/analyze",
        headers=AUTH,
        json={"question": "   "},
    )
    assert response.status_code == 422


def test_ai_ops_model_failure_is_safe(monkeypatch):
    monkeypatch.setattr(ai_ops, "_build_context", lambda *_args: {
        "scope": {"execution_mode": "read_only_dry_run"},
        "summary": {}, "hosts": [], "services": [],
        "active_incidents": [], "warnings": [],
    })
    monkeypatch.setattr(ai_ops, "chat", lambda *_args, **_kwargs: {"content": "not json"})
    response = client.post(
        "/api/v2/ai-ops/analyze",
        headers=AUTH,
        json={"question": "检查当前状态"},
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_model_output"
    assert "not json" not in response.text

