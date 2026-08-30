"""Firewall command validation and SSH safety tests."""
import uuid

from fastapi.testclient import TestClient
import pytest

from app import firewall_control
from app.main import Base, SessionLocal, app, engine
from app.models import Server


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def host(ssh_port=22):
    row_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Server(id=row_id, name="node-fw", host="10.66.66.20", ssh_port=ssh_port, agent_type="ssh", status="online"))
        db.commit()
    return str(row_id)


def test_validation_and_ufw_parser_reject_injection():
    assert firewall_control._port("8000-8010") == ("8000-8010", 8000, 8010)
    assert firewall_control._source("10.66.66.9/24") == "10.66.66.0/24"
    with pytest.raises(Exception):
        firewall_control._port("22; reboot")
    with pytest.raises(Exception):
        firewall_control._source("any; reboot")
    rows = firewall_control._parse_ufw("Status: active\n[ 1] 22/tcp ALLOW IN 10.66.66.0/24\n[ 2] 53/udp DENY IN Anywhere")
    assert rows == [
        {"id": "1", "port": "22", "protocol": "tcp", "action": "allow", "source": "10.66.66.0/24"},
        {"id": "2", "port": "53", "protocol": "udp", "action": "deny", "source": "Anywhere"},
    ]


def test_add_rule_builds_validated_argument_vector(monkeypatch):
    server_id = host()
    commands = []
    monkeypatch.setattr(firewall_control, "_detect", lambda _server: "ufw")
    monkeypatch.setattr(firewall_control, "_execute", lambda _server, args, timeout=20: commands.append(args) or "")
    response = client.post(f"/api/v2/servers/{server_id}/firewall/rules", json={"port": "8080", "protocol": "tcp", "action": "allow", "source": "10.66.66.0/24"})
    assert response.status_code == 201, response.text
    assert commands == [["sudo", "ufw", "--force", "allow", "from", "10.66.66.0/24", "to", "any", "port", "8080", "proto", "tcp"]]


def test_delete_rule_requires_extra_confirmation_for_ssh_port(monkeypatch):
    server_id = host(2222)
    commands = []
    monkeypatch.setattr(firewall_control, "_detect", lambda _server: "ufw")
    monkeypatch.setattr(firewall_control, "_execute", lambda _server, args, timeout=20: commands.append(args) or "")
    blocked = client.post(f"/api/v2/servers/{server_id}/firewall/rules/delete", json={"rule_id": "3", "port": "2200-2300", "protocol": "tcp"})
    assert blocked.status_code == 409
    allowed = client.post(f"/api/v2/servers/{server_id}/firewall/rules/delete", json={"rule_id": "3", "port": "2200-2300", "protocol": "tcp", "confirm_ssh_disruption": True})
    assert allowed.status_code == 200
    assert commands == [["sudo", "ufw", "--force", "delete", "3"]]


def test_state_change_requires_exact_host_name(monkeypatch):
    server_id = host()
    monkeypatch.setattr(firewall_control, "_detect", lambda _server: "ufw")
    monkeypatch.setattr(firewall_control, "_execute", lambda *_args, **_kwargs: "")
    assert client.post(f"/api/v2/servers/{server_id}/firewall/state", json={"enabled": False, "confirm_name": "wrong"}).status_code == 400
    assert client.post(f"/api/v2/servers/{server_id}/firewall/state", json={"enabled": False, "confirm_name": "node-fw"}).status_code == 200
