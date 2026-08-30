"""Focused security and lightweight behavior checks for v4.2."""
import json

from app import control
from app.audit import _classify, _redact
from app.credential_crypto import decrypt_secret, encrypt_secret
from app.performance import _REQUEST_ID_RE


def test_database_credentials_are_encrypted_and_round_trip():
    ciphertext = encrypt_secret("not-plain-text")
    assert ciphertext
    assert "not-plain-text" not in ciphertext
    assert decrypt_secret(ciphertext) == "not-plain-text"


def test_audit_redaction_is_recursive():
    payload = {"username": "ops", "password": "one", "nested": {"api_token": "two"}, "items": [{"private_key": "three"}]}
    redacted = _redact(payload)
    assert redacted["username"] == "ops"
    assert redacted["password"] == "••••••••"
    assert redacted["nested"]["api_token"] == "••••••••"
    assert redacted["items"][0]["private_key"] == "••••••••"
    assert _redact({"content_base64": "sensitive-file"})["content_base64"] == "••••••••"


def test_new_audit_resource_classes():
    assert _classify("/api/v2/databases/instances/id/accounts", "POST")[1] == "database-account"
    assert _classify("/api/v2/servers/id/processes/42/signal", "POST")[1] == "process"
    assert _classify("/api/v2/servers/id/files/upload", "POST")[1] == "file"
    assert _classify("/api/v2/servers/id/firewall/rules", "POST")[1] == "firewall"


def test_remote_container_basic_mode_never_calls_docker_stats(monkeypatch):
    attrs = [{
        "Id": "a" * 64, "Name": "/demo", "Config": {"Image": "demo:latest"},
        "State": {"Status": "running"}, "HostConfig": {},
        "NetworkSettings": {"Networks": {}, "Ports": {}}, "Mounts": [],
    }]
    commands = []

    def fake_exec(_client, command, **_kwargs):
        commands.append(command)
        if command.startswith("docker ps"):
            return "a" * 64 + "\n", "", 0
        return json.dumps(attrs), "", 0

    monkeypatch.setattr(control, "ssh_exec", fake_exec)
    rows = control._remote_container_rows(object(), include_stats=False)
    assert rows[0]["name"] == "demo"
    assert not any("docker stats" in command for command in commands)


def test_request_id_validation_is_bounded_and_header_safe():
    assert _REQUEST_ID_RE.fullmatch("deploy-4.2.1:abc_123")
    assert not _REQUEST_ID_RE.fullmatch("bad request id")
    assert not _REQUEST_ID_RE.fullmatch("x" * 65)
