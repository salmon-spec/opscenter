"""Failure-path regressions from the October 23 production audit."""
from contextlib import nullcontext
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from fastapi import HTTPException
from app import file_control, firewall_control, main, databases
from app.models import Server, MetricHistory


@pytest.mark.parametrize("failure", ["write", "rename", None])
def test_remote_save_failure_preserves_original(monkeypatch, failure):
    files = {"/tmp/config": b"original configuration"}
    ownership = []
    class Writer:
        def __init__(self, path): self.path = path
        def __enter__(self): files[self.path] = b""; return self
        def __exit__(self, *args): pass
        def write(self, data):
            files[self.path] = data[:3] if failure == "write" else data
            if failure == "write": raise OSError("interrupted")
        def flush(self): pass
    def rename(source, target):
        assert files[target] == b"original configuration"
        if failure == "rename": raise OSError("unsupported extension")
        files[target] = files.pop(source)
    sftp = SimpleNamespace(normalize=lambda path: path,
        stat=lambda path: SimpleNamespace(st_size=len(files[path]), st_mtime=10, st_mode=0o640, st_uid=1000, st_gid=1000),
        open=lambda path, mode: BytesIO(files[path]) if mode == "rb" else Writer(path), chmod=lambda *args: ownership.append(args),
        chown=lambda *args: ownership.append(args), posix_rename=rename,
        remove=lambda path: files.pop(path, None))
    monkeypatch.setattr(file_control, "_server", lambda _: object())
    monkeypatch.setattr(file_control, "_filesystem", lambda _: nullcontext((True, sftp, None)))
    if failure:
        with pytest.raises(HTTPException) as error:
            file_control.write_file("host", "/tmp/config", file_control.FileWriteRequest(content="new configuration"))
        assert error.value.status_code == 502
        assert files == {"/tmp/config": b"original configuration"}
    else:
        file_control.write_file("host", "/tmp/config", file_control.FileWriteRequest(content="new configuration"))
        assert files == {"/tmp/config": b"new configuration"}
        assert ownership[0][1] == 0o640 and ownership[1][1:] == (1000, 1000)


def test_stale_ufw_number_cannot_delete_ssh(monkeypatch):
    calls = []
    monkeypatch.setattr(firewall_control, "_server", lambda _: SimpleNamespace(ssh_port=22))
    monkeypatch.setattr(firewall_control, "_detect", lambda _: "ufw")
    monkeypatch.setattr(firewall_control, "_execute", lambda _, args: calls.append(args) or "[ 1] 22/tcp ALLOW IN Anywhere")
    with pytest.raises(HTTPException) as error:
        firewall_control.delete_firewall_rule("host", firewall_control.FirewallDeleteRequest(rule_id="1", port="80"))
    assert error.value.status_code == 409
    assert calls == [["sudo", "ufw", "status", "numbered"]]


def test_ufw_rule_without_protocol_can_be_deleted_by_verified_content(monkeypatch):
    calls = []
    monkeypatch.setattr(firewall_control, "_server", lambda _: SimpleNamespace(ssh_port=22))
    monkeypatch.setattr(firewall_control, "_detect", lambda _: "ufw")
    monkeypatch.setattr(firewall_control, "_execute", lambda _, args: calls.append(args) or "[ 2] 80 ALLOW IN Anywhere")
    firewall_control.delete_firewall_rule("host", firewall_control.FirewallDeleteRequest(rule_id="2",port="80",protocol="any"))
    assert calls[-1] == ["sudo","ufw","--force","delete","allow","in","from","0.0.0.0/0","to","0.0.0.0/0","port","80"]


@pytest.mark.parametrize("sample,expected", [({"cpu_percent": 0}, {"cpu": 0}), ({"cpu_percent": float("nan")}, {}), ({"cpu_percent": 12, "timestamp": 0}, {})])
def test_missing_or_invalid_agent_values_do_not_become_zero_history(monkeypatch, sample, expected):
    main.Base.metadata.drop_all(main.engine)
    main.Base.metadata.create_all(main.engine)
    identifier = uuid.uuid4()
    with main.SessionLocal() as db:
        db.add(Server(id=identifier, name="audit", host="127.0.0.1", agent_type="local", status="online"))
        db.commit()
    monkeypatch.setattr(main, "active_host_ids", lambda: {str(identifier)})
    monkeypatch.setattr(main, "_fetch_monitoring_sample", lambda _: sample)
    monkeypatch.delenv("HOST_MONITOR_INGEST_URL", raising=False)
    main._collect_agent_metrics()
    with main.SessionLocal() as db:
        stored = {row.metric: row.value for row in db.query(MetricHistory).all()}
    assert stored == expected


def test_retired_asset_is_not_collected(monkeypatch):
    main.Base.metadata.drop_all(main.engine)
    main.Base.metadata.create_all(main.engine)
    with main.SessionLocal() as db:
        db.add(Server(id=uuid.uuid4(), name="retired", host="127.0.0.1", agent_type="local")); db.commit()
    fetch = Mock()
    monkeypatch.setattr(main, "active_host_ids", lambda: set())
    monkeypatch.setattr(main, "_fetch_monitoring_sample", fetch)
    monkeypatch.delenv("HOST_MONITOR_INGEST_URL", raising=False)
    main._collect_agent_metrics()
    fetch.assert_not_called()


def test_postgres_account_ddl_quotes_password_without_server_parameters(monkeypatch):
    conn = Mock()
    monkeypatch.setattr(databases, "_load_instance", lambda _: (SimpleNamespace(engine="postgresql"), object()))
    monkeypatch.setattr(databases, "_connect", lambda *_: nullcontext(conn))
    password = "quote'\\password"
    databases.create_account("id", databases.AccountCreate(username="test_user", password=password, databases=[]))
    databases.update_account("id", "test_user", databases.AccountUpdate(password=password))
    for call in conn.execute.call_args_list:
        assert len(call.args) == 1
        query = call.args[0].as_string()
        assert "%s" not in query and "quote''" in query and '"test_user"' in query
