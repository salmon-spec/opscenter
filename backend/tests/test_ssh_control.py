"""SSH management validation, key handling and guarded configuration tests."""
import base64
import uuid

from fastapi.testclient import TestClient
import pytest

from app import ssh_control
from app.main import Base, SessionLocal, app, engine
from app.models import Server


client = TestClient(app)
PUBLIC_KEY = "ssh-ed25519 " + base64.b64encode(b"opscenter-test-public-key-material").decode() + " workstation"


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def host(port=22):
    row_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Server(id=row_id, name="node-ssh", host="10.66.66.30", ssh_port=port, ssh_user="root", agent_type="ssh", status="online"))
        db.commit()
    return str(row_id)


def test_user_and_public_key_validation_reject_injection():
    parsed = ssh_control._parse_key(PUBLIC_KEY)
    assert parsed["type"] == "ssh-ed25519"
    assert parsed["fingerprint"].startswith("SHA256:")
    assert parsed["comment"] == "workstation"
    with pytest.raises(Exception):
        ssh_control._user("root; reboot")
    with pytest.raises(Exception):
        ssh_control._parse_key("ssh-ed25519 not-base64")


def test_ssh_audit_classification_and_public_key_redaction():
    from app.audit import _classify, _redact
    assert _classify("/api/v2/servers/id/ssh/config", "PUT") == ("update", "ssh")
    assert _redact({"public_key": PUBLIC_KEY, "user": "root"}) == {"public_key": "••••••••", "user": "root"}


def test_overview_and_session_parser(monkeypatch):
    server_id = host(2222)
    monkeypatch.setattr(ssh_control, "_service", lambda _server: ("ssh", "active"))
    monkeypatch.setattr(ssh_control, "_effective_config", lambda _server: {"port": "2222", "pubkeyauthentication": "yes"})
    monkeypatch.setattr(ssh_control, "_must_run", lambda *_args, **_kwargs: "ops pts/1 2026-08-30 10:00 (10.66.66.9)\n")
    result = client.get(f"/api/v2/servers/{server_id}/ssh/overview")
    assert result.status_code == 200, result.text
    assert result.json()["session_count"] == 1
    sessions = client.get(f"/api/v2/servers/{server_id}/ssh/sessions").json()["items"]
    assert sessions[0]["remote"] == "10.66.66.9"


def test_add_and_delete_authorized_key_by_fingerprint(monkeypatch):
    server_id = host()
    writes = []
    lines = []
    monkeypatch.setattr(ssh_control, "_key_lines", lambda *_args: ("/root/.ssh/authorized_keys", "root", list(lines)))
    monkeypatch.setattr(ssh_control, "_write_keys", lambda _server, _user, _path, _group, value: writes.append(list(value)))
    added = client.post(f"/api/v2/servers/{server_id}/ssh/authorized-keys", json={"user": "root", "public_key": PUBLIC_KEY})
    assert added.status_code == 201, added.text
    assert writes[-1] == [PUBLIC_KEY]

    lines.append(PUBLIC_KEY)
    deleted = client.post(f"/api/v2/servers/{server_id}/ssh/authorized-keys/delete", json={"user": "root", "fingerprint": added.json()["fingerprint"]})
    assert deleted.status_code == 200, deleted.text
    assert writes[-1] == []


def test_config_requires_auth_method_and_exact_confirmation(monkeypatch):
    server_id = host()
    body = {
        "port": 22, "permit_root_login": "no", "password_authentication": False,
        "pubkey_authentication": False, "max_auth_tries": 6,
        "client_alive_interval": 300, "confirm_name": "node-ssh",
    }
    assert client.put(f"/api/v2/servers/{server_id}/ssh/config", json=body).status_code == 400
    body["pubkey_authentication"] = True
    body["confirm_name"] = "wrong"
    assert client.put(f"/api/v2/servers/{server_id}/ssh/config", json=body).status_code == 400


def test_config_validates_reloads_and_updates_management_port(monkeypatch):
    server_id = host()
    written = []
    commands = []

    def run(_server, command, timeout=20):
        commands.append(command)
        if "test -f" in command:
            return "", "", 1
        return "", "", 0

    monkeypatch.setattr(ssh_control, "_run", run)
    monkeypatch.setattr(ssh_control, "_write_dropin", lambda _server, content: written.append(content))
    monkeypatch.setattr(ssh_control, "_service", lambda _server: ("sshd", "active"))
    monkeypatch.setattr(ssh_control, "_effective_config", lambda _server: {"port": "2202"})
    response = client.put(f"/api/v2/servers/{server_id}/ssh/config", json={
        "port": 2202, "permit_root_login": "prohibit-password",
        "password_authentication": False, "pubkey_authentication": True,
        "max_auth_tries": 4, "client_alive_interval": 300,
        "confirm_name": "node-ssh",
    })
    assert response.status_code == 200, response.text
    assert "Port 2202" in written[0]
    assert any("sshd -t" in command for command in commands)
    assert any("systemctl reload sshd" in command for command in commands)
    with SessionLocal() as db:
        assert db.query(Server).filter(Server.id == uuid.UUID(server_id)).first().ssh_port == 2202


def test_invalid_sshd_config_is_rolled_back(monkeypatch):
    server_id = host()
    written = []

    def run(_server, command, timeout=20):
        if "test -f" in command:
            return "", "", 1
        if "sshd -t" in command:
            return "", "bad configuration", 1
        return "", "", 0

    monkeypatch.setattr(ssh_control, "_run", run)
    monkeypatch.setattr(ssh_control, "_write_dropin", lambda _server, content: written.append(content))
    response = client.put(f"/api/v2/servers/{server_id}/ssh/config", json={
        "port": 22, "permit_root_login": "no", "password_authentication": False,
        "pubkey_authentication": True, "max_auth_tries": 4,
        "client_alive_interval": 300, "confirm_name": "node-ssh",
    })
    assert response.status_code == 400
    assert written[-1] is None
