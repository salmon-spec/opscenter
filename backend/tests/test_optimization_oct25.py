"""Regressions for the second operational review."""
from contextlib import nullcontext
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from app import file_control, firewall_control, main


@pytest.mark.parametrize("when", ["before_save", "during_write"])
def test_sftp_detects_same_size_changes_with_unchanged_second(monkeypatch, when):
    files = {"/tmp/file": b"BBBB" if when == "before_save" else b"AAAA"}
    class Writer:
        def __init__(self, path): self.path = path
        def __enter__(self): files[self.path] = b""; return self
        def __exit__(self, *args): pass
        def write(self, raw): files[self.path] = raw; files["/tmp/file"] = b"BBBB"
        def flush(self): pass
    sftp = SimpleNamespace(normalize=lambda p: p,
        stat=lambda p: SimpleNamespace(st_size=len(files[p]), st_mtime=10, st_mode=0o640, st_uid=1, st_gid=1),
        open=lambda p, m: BytesIO(files[p]) if m == "rb" else Writer(p),
        chmod=lambda *a: None, chown=lambda *a: None, posix_rename=Mock(), remove=lambda p: files.pop(p, None))
    monkeypatch.setattr(file_control, "_server", lambda _: object())
    monkeypatch.setattr(file_control, "_filesystem", lambda _: nullcontext((True, sftp, None)))
    with pytest.raises(HTTPException) as error:
        file_control.write_file("host", "/tmp/file", file_control.FileWriteRequest(
            content="CCCC", expected_mtime=10, expected_sha256=sha256(b"AAAA").hexdigest()))
    assert error.value.status_code == 409
    assert files == {"/tmp/file": b"BBBB"}
    sftp.posix_rename.assert_not_called()


def test_local_file_digest_survives_read_and_blocks_stale_editor(tmp_path, monkeypatch):
    path = tmp_path / "file"
    path.write_bytes(b"AAAA")
    monkeypatch.setattr(file_control, "_server", lambda _: object())
    monkeypatch.setattr(file_control, "_filesystem", lambda _: nullcontext((False, None, None)))
    before = file_control.read_file("host", str(path))
    path.write_bytes(b"BBBB")
    with pytest.raises(HTTPException) as error:
        file_control.write_file("host", str(path), file_control.FileWriteRequest(content="CCCC", expected_sha256=before["sha256"]))
    assert error.value.status_code == 409 and path.read_bytes() == b"BBBB"


@pytest.mark.parametrize("family,marker,network", [("ipv4", "", "0.0.0.0/0"), ("ipv6", " (v6)", "::/0")])
def test_ufw_comments_and_address_family_are_preserved(monkeypatch, family, marker, network):
    line = f"[ 5] 8080/tcp{marker} ALLOW IN Anywhere{marker} # service rule"
    rule = firewall_control._parse_ufw(line)[0]
    assert rule["family"] == family and rule["source"] == "Anywhere" and rule["comment"] == "service rule"
    calls = []
    monkeypatch.setattr(firewall_control, "_server", lambda _: SimpleNamespace(ssh_port=22))
    monkeypatch.setattr(firewall_control, "_detect", lambda _: "ufw")
    monkeypatch.setattr(firewall_control, "_execute", lambda _, args: calls.append(args) or line)
    firewall_control.delete_firewall_rule("host", firewall_control.FirewallDeleteRequest(
        rule_id="5", port="8080", family=family, comment="service rule", source="Anywhere"))
    assert calls[-1] == ["sudo", "ufw", "--force", "delete", "allow", "in", "from", network, "to", network, "port", "8080", "proto", "tcp"]


def test_retired_hosts_are_excluded_from_agent_and_network_loops(monkeypatch):
    retired = SimpleNamespace(id="retired", enabled=True, name="retired")
    class Query:
        def filter(self, *a): return self
        def order_by(self, *a): return self
        def first(self): return None
        def all(self): return [retired]
    db = SimpleNamespace(query=lambda _: Query(), commit=lambda: None)
    monkeypatch.setattr(main, "get_db", lambda: nullcontext(db))
    monkeypatch.setattr(main, "active_host_ids", lambda: {"active"})
    check, publish, network = Mock(), Mock(), Mock()
    monkeypatch.setattr(main, "check_agent_status", check)
    monkeypatch.setattr(main, "publish_host_inventory_telemetry", publish)
    monkeypatch.setattr(main, "_agent_request", network)
    main._run_agent_health_check()
    main._run_network_aggregation_once()
    check.assert_not_called(); publish.assert_not_called(); network.assert_not_called()
    assert main._is_active_host(SimpleNamespace(id="active", enabled=True), {"active"})
    assert not main._is_active_host(SimpleNamespace(id="active", enabled=False), None)
    assert main._is_active_host(SimpleNamespace(id="active", enabled=True), None)
