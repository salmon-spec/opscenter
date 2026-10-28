import base64
import hashlib
import json
from types import SimpleNamespace
import uuid
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
from fastapi.testclient import TestClient
import pytest

from app import main, pve_guest
from app.models import Server


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "")
    monkeypatch.setattr(main, "publish_host_inventory_telemetry", lambda server: True)
    main.Base.metadata.drop_all(bind=main.engine)
    main.Base.metadata.create_all(bind=main.engine)
    yield


def test_new_host_dispatches_agent_install_by_default(monkeypatch):
    dispatched = []
    monkeypatch.setattr(main, "_dispatch_agent_deploy", lambda tasks, sid: dispatched.append(sid))
    client = TestClient(main.app)
    response = client.post("/api/v2/servers", json={"name": "guest", "host": "192.0.2.10", "ssh_password": "synthetic"})
    assert response.status_code == 201
    assert response.json()["agent_status"] == "deploying"
    assert dispatched == [response.json()["id"]]
    assert "synthetic" not in response.text
    response = client.post("/api/v2/servers", json={"name": "fixture", "host": "192.0.2.11", "auto_deploy_agent": False})
    assert response.json()["agent_status"] == "not_deployed"
    assert len(dispatched) == 1


@pytest.mark.parametrize("metrics,expected", [({"cpu_percent": 0, "memory_percent": 10}, "running"), (None, "error")])
def test_install_requires_live_monitoring_sample(monkeypatch, metrics, expected):
    sid = uuid.uuid4()
    with main.get_db() as db:
        db.add(Server(id=sid, name="guest", host="192.0.2.10", ssh_key="synthetic", agent_type="remote"))
        db.commit()
    monkeypatch.setattr(main, "deploy_agent", lambda *a, **k: {"success": True, "agent_token": "protected-token", "agent_version": "2.6.4"})
    monkeypatch.setattr(main, "_fetch_monitoring_sample", lambda server: metrics)
    main._deploy_agent_background(str(sid))
    with main.get_db() as db:
        row = db.query(Server).filter(Server.id == sid).one()
        assert row.agent_status == expected
        if metrics is not None:
            assert row.status == "online" and row.agent_token == "protected-token"
            assert row.last_seen is not None
        else:
            assert row.status != "online"
    task = main.AGENT_TASKS.get(str(sid))
    assert task["status"] == ("success" if metrics is not None else "failed")
    assert "protected-token" not in json.dumps(task)


def test_pve_guest_identity_address_and_matching_credential():
    server = SimpleNamespace(tags=["pve-source:" + "a" * 20, "pve-vmid:119"], host="192.0.2.10", lan_ip="192.0.2.10")
    assert pve_guest.guest_identity(server) == ("a" * 20, 119)
    assert pve_guest.guest_address({"ipconfig0": "ip=192.0.2.10/24,gw=192.0.2.1"}, server) == server.host
    with pytest.raises(ValueError):
        pve_guest.guest_address({"ipconfig0": "ip=192.0.2.11/24"}, server)
    server.tags.append("pve-vmid:120")
    assert pve_guest.guest_identity(server) is None
    key = Ed25519PrivateKey.generate()
    secret = key.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()).decode()
    public = key.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode()
    credential = SimpleNamespace(enabled=True, ssh_key=secret)
    assert pve_guest.credential_key({"sshkeys": public}, [credential])[1] == secret
    with pytest.raises(ValueError):
        pve_guest.credential_key({"sshkeys": ""}, [credential])


def test_bootstrap_rejects_wrong_pve_without_saving_credentials(monkeypatch):
    sid = uuid.uuid4()
    with main.get_db() as db:
        db.add(Server(id=sid, name="guest", host="192.0.2.10", tags=["pve-source:" + "a" * 20, "pve-vmid:119"], ssh_key=None))
        db.add(Server(name="pve", host="192.0.2.1", agent_type="local", enabled=True))
        db.commit()
    closed = []
    monkeypatch.setattr(pve_guest, "get_ssh_client", lambda server: SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(pve_guest, "ssh_exec", lambda *a, **k: ("different-machine-id", "", 0))
    with pytest.raises(ValueError, match="来源"):
        pve_guest.prepare_guest_access(str(sid))
    assert closed == [True]
    with main.get_db() as db:
        assert not db.query(Server).filter(Server.id == sid).one().ssh_key


@pytest.mark.skipif(sys.platform == "win32", reason="Linux iptables runtime")
def test_forwarding_is_limited_to_agent_ports_and_private_guest():
    import runpy
    from pathlib import Path
    rules = runpy.run_path(str(Path(__file__).resolve().parents[2] / "deploy/pve/opscenter-agent-forward.py"))["rules"]
    assert len(rules("119", "192.168.1.164")) == 12
    assert all("22,19100" in r and "tcp" in r for r in rules("119", "192.168.1.164"))
    for vmid, address in [("119;bad", "192.168.1.164"), ("119", "198.51.100.1")]:
        with pytest.raises(ValueError):
            rules(vmid, address)
