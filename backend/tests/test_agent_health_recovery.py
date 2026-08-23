"""Agent health reconciliation regression tests."""

from contextlib import contextmanager
import io
import json
from types import SimpleNamespace

from app import main
from app import agent_manager


class _Query:
    def __init__(self, result):
        self.result = result

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.result

    def all(self):
        return self.result


class _Db:
    def __init__(self, remotes):
        self.remotes = remotes
        self.query_count = 0
        self.committed = False

    def query(self, _model):
        self.query_count += 1
        return _Query(None if self.query_count == 1 else self.remotes)

    def commit(self):
        self.committed = True


def _server(status="stopped"):
    return SimpleNamespace(
        name="remote",
        host="10.0.0.8",
        agent_type="remote",
        agent_status=status,
        agent_port=19100,
        agent_token="stale",
        agent_version="2.1.0",
        last_seen=None,
    )


def test_stopped_agent_recovers_and_refreshes_its_token(monkeypatch):
    server = _server("stopped")
    db = _Db([server])

    @contextmanager
    def fake_db():
        yield db

    monkeypatch.setattr(main, "get_db", fake_db)
    monkeypatch.setattr(
        main,
        "check_agent_status",
        lambda _srv: {
            "status": "running",
            "agent_port": 19100,
            "agent_token": "fresh",
            "agent_version": "2.2.0",
        },
    )
    monkeypatch.setattr(
        main,
        "fetch_agent_metrics",
        lambda _host, _port, token: {"cpu_percent": 1} if token == "fresh" else None,
    )

    main._run_agent_health_check()

    assert server.agent_status == "running"
    assert server.agent_token == "fresh"
    assert server.agent_version == "2.2.0"
    assert server.last_seen is not None
    assert db.committed


def test_missing_agent_remains_explicitly_not_deployed(monkeypatch):
    server = _server("stopped")
    db = _Db([server])

    @contextmanager
    def fake_db():
        yield db

    monkeypatch.setattr(main, "get_db", fake_db)
    monkeypatch.setattr(
        main,
        "check_agent_status",
        lambda _srv: {"status": "not_deployed", "message": "Agent未部署"},
    )

    main._run_agent_health_check()

    assert server.agent_status == "not_deployed"
    assert db.committed


class _RemoteFile(io.StringIO):
    def __init__(self, path, files):
        super().__init__()
        self.path = path
        self.files = files

    def close(self):
        if not self.closed:
            self.files[self.path] = self.getvalue()
        super().close()


class _Sftp:
    def __init__(self):
        self.files = {}
        self.modes = {}

    def file(self, path, _mode):
        return _RemoteFile(path, self.files)

    def chmod(self, path, mode):
        self.modes[path] = mode

    def close(self):
        pass


class _SshClient:
    def __init__(self):
        self.sftp = _Sftp()

    def open_sftp(self):
        return self.sftp

    def close(self):
        pass


def test_non_root_deploy_uses_sudo_and_keeps_token_out_of_commands(monkeypatch):
    client = _SshClient()
    commands = []
    active_checks = 0

    def fake_exec(_client, command, timeout=30):
        nonlocal active_checks
        commands.append(command)
        if command == "which python3 && python3 --version":
            return "/usr/bin/python3\nPython 3", "", 0
        if command == "systemctl is-active opsagent.service":
            active_checks += 1
            return ("inactive" if active_checks == 1 else "active"), "", 0
        return "", "", 0

    monkeypatch.setattr(agent_manager, "_get_ssh_client", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(agent_manager, "_ssh_exec", fake_exec)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    result = agent_manager.deploy_agent(SimpleNamespace(ssh_user="ubuntu"))

    assert result["success"] is True
    assert any(command.startswith("sudo -n install") for command in commands)
    assert any(command.startswith("sudo -n systemctl start") for command in commands)
    config_text = next(
        content for path, content in client.sftp.files.items() if path.endswith("-config")
        and '"token"' in content
    )
    token = json.loads(config_text)["token"]
    assert token
    assert all(token not in command for command in commands)
    config_path = next(path for path, content in client.sftp.files.items() if content == config_text)
    assert client.sftp.modes[config_path] == 0o600
