"""Safety regressions found after the 5.5.21 rollout."""
from contextlib import nullcontext
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from app import file_control as files, firewall_control as fw


@pytest.mark.parametrize("failure", [None, "race", "interrupted"])
def test_upload_publishes_complete_file_without_overwriting(monkeypatch, failure):
    contents = {}
    target = "/tmp/upload.txt"
    class Writer(BytesIO):
        def __init__(self, path): super().__init__(); self.path = path
        def write(self, raw):
            if failure == "interrupted":
                contents[self.path] = raw[:1]
                raise OSError("Connection interrupted")
            return super().write(raw)
        def close(self): contents[self.path] = self.getvalue(); super().close()
    def opening(path, mode):
        assert mode == "wx" and path != target
        if failure == "race": contents[target] = b"other task"
        return Writer(path)
    def attrs(path):
        if path not in contents: raise FileNotFoundError(path)
        return SimpleNamespace(st_size=len(contents[path]))
    def rename(source, destination):
        if destination in contents: raise OSError("Failure")
        contents[destination] = contents.pop(source)
    sftp = SimpleNamespace(normalize=lambda p:p, open=opening, stat=attrs, lstat=attrs,
        rename=rename, remove=lambda p:contents.pop(p, None))
    monkeypatch.setattr(files, "_server", lambda _:object())
    monkeypatch.setattr(files, "_filesystem", lambda _:nullcontext((True,sftp,None)))
    if failure:
        with pytest.raises(HTTPException) as error:
            files.upload_file("host", files.FileUploadRequest(parent="/tmp", name="upload.txt", content_base64="bmV3"))
        assert error.value.status_code == (409 if failure == "race" else 502)
        assert contents == ({target:b"other task"} if failure == "race" else {})
    else:
        assert files.upload_file("host", files.FileUploadRequest(parent="/tmp", name="upload.txt", content_base64="bmV3"))["ok"]
        assert contents == {target:b"new"}


@pytest.mark.parametrize("rules,peer,expected", [
    ("ufw allow 2222/tcp", "192.168.1.2", True),
    ("ufw allow from 192.168.1.0/24 to any port 2222 proto tcp", "192.168.1.2", True),
    ("ufw allow from 192.168.1.0/24 to any port 2222 proto tcp", "10.66.66.2", False),
    ("ufw deny 2222/tcp\nufw allow 2222/tcp", "192.168.1.2", False),
    ("ufw allow 2222/udp", "192.168.1.2", False),
    ("ufw allow OpenSSH", "192.168.1.2", False),
    ("(None)", "192.168.1.2", False),
])
def test_ssh_preflight_refuses_unknown_or_conflicting_rules(rules, peer, expected):
    assert fw._ssh_allowed(rules, 2222, peer) is expected


@pytest.mark.parametrize("scenario", ["managed", "missing_ssh", "timer_failed", "reconnect_failed", "success", "firewalld"])
def test_firewall_enable_requires_preflight_and_host_owned_rollback(monkeypatch, scenario):
    calls = []
    server = SimpleNamespace(name="node", ssh_port=2222, agent_type="ssh")
    def execute(_, args):
        calls.append(args)
        if args == ["sudo","ufw","status"]:
            return "Status: active" if ["sudo","ufw","--force","enable"] in calls else "Status: inactive"
        if args[:3] == ["sh","-c",'printf "%s" "${SSH_CONNECTION%% *}"']: return "192.168.1.2"
        if args[:3] == ["sudo","sh","-c"]: return "managed" if scenario == "managed" else "standalone"
        if args == ["sudo","ufw","show","added"]: return "(None)" if scenario == "missing_ssh" else "ufw allow 2222/tcp"
        if args[:3] == ["sudo","systemctl","is-active"]: return "inactive" if scenario == "timer_failed" else "active"
        return ""
    reconnect = Mock(return_value=None if scenario == "reconnect_failed" else Mock())
    monkeypatch.setattr(fw, "_execute", execute)
    monkeypatch.setattr(fw, "get_ssh_client", reconnect)
    if scenario == "success":
        fw._enable_firewall(server, "ufw")
        reconnect.assert_called_once_with(server)
        assert any('--timer-property=AccuracySec=1s' in c for c in calls)
        assert calls[-1][:3] == ["sudo","systemctl","stop"]
    else:
        with pytest.raises(HTTPException): fw._enable_firewall(server, "firewalld" if scenario == "firewalld" else "ufw")
        if scenario == "reconnect_failed":
            assert ["sudo","ufw","--force","enable"] in calls
            assert not any(c[:3] == ["sudo","systemctl","stop"] for c in calls)
        else:
            assert ["sudo","ufw","--force","enable"] not in calls
