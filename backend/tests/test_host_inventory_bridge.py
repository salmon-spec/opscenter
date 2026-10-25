import json
from types import SimpleNamespace

from app.host_inventory_bridge import publish_host_inventory_telemetry


def test_disabled_historical_host_never_publishes_registration(monkeypatch, tmp_path):
    token = tmp_path / "token"
    token.write_text("test-token")
    monkeypatch.setenv("HOST_INVENTORY_INGEST_URL", "http://inventory/telemetry")
    monkeypatch.setenv("HOST_INVENTORY_INGEST_TOKEN_FILE", str(token))
    def unexpected_request(*args, **kwargs):
        raise AssertionError("disabled host must not be registered")
    assert publish_host_inventory_telemetry(SimpleNamespace(id="retired", enabled=False), unexpected_request) is False


def test_publish_sends_only_host_health_fields(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("test-secret", encoding="utf-8")
    monkeypatch.setenv("HOST_INVENTORY_INGEST_URL", "http://module-host-inventory:8080/api/v1/agent/telemetry")
    monkeypatch.setenv("HOST_INVENTORY_INGEST_TOKEN_FILE", str(token_file))
    captured = {}

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["token"] = request.get_header("X-opscenter-module-token")
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    server = SimpleNamespace(
        id="host-1", name="not-sent", host="not-sent", status="online", docker_available=True,
        is_local=False, agent_status="running", agent_type="remote", last_seen=None,
        agent_version="2.6.0", log_agent_status="running", log_agent_version="1.2.0",
        agent_token="never-send-this",
    )

    assert publish_host_inventory_telemetry(server, opener) is True
    assert captured["url"].endswith("/api/v1/agent/telemetry")
    assert captured["token"] == "test-secret"
    assert captured["timeout"] == 2.5
    registration = captured["payload"].pop("registration")
    assert registration["name"] == server.name and registration["host"] == server.host
    assert "agent_token" not in registration
    assert captured["payload"] == {
        "id": "host-1", "status": "online", "docker_available": True, "is_local": False,
        "agent_status": "running", "agent_type": "remote", "last_seen": None,
        "agent_version": "2.6.0", "log_agent_status": "running", "log_agent_version": "1.2.0",
    }
    assert "never-send-this" not in str(captured)


def test_publish_is_disabled_without_module_credentials(monkeypatch):
    monkeypatch.delenv("HOST_INVENTORY_INGEST_URL", raising=False)
    monkeypatch.delenv("HOST_INVENTORY_INGEST_TOKEN_FILE", raising=False)
    assert publish_host_inventory_telemetry(SimpleNamespace(id="host-1")) is False


def test_publish_failure_is_non_blocking_and_does_not_log_token(monkeypatch, tmp_path, caplog):
    token_file = tmp_path / "token"
    token_file.write_text("sensitive-token", encoding="utf-8")
    monkeypatch.setenv("HOST_INVENTORY_INGEST_URL", "http://module-host-inventory:8080/telemetry")
    monkeypatch.setenv("HOST_INVENTORY_INGEST_TOKEN_FILE", str(token_file))

    def fail(*_args, **_kwargs):
        raise OSError("sensitive-token connection error")

    assert publish_host_inventory_telemetry(SimpleNamespace(id="host-1"), fail) is False
    assert "sensitive-token" not in caplog.text
