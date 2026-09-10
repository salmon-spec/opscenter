"""Pure unit coverage for Alloy deployment decisions."""

from app import alloy_manager
from app.models import Server


def test_deploy_reuses_matching_alloy_without_downloading(monkeypatch):
    commands = []

    class FakeClient:
        def close(self):
            pass

    def fake_exec(_client, command, timeout=None):
        commands.append(command)
        if command.startswith("alloy --version"):
            return "alloy, version v1.18.0", "", 0
        if command == "systemctl is-active alloy":
            return "active\n", "", 0
        return "", "", 0

    monkeypatch.setattr(alloy_manager, "LOKI_PUBLIC_URL", "http://10.66.66.15:31000")
    monkeypatch.setattr(alloy_manager, "get_ssh_client", lambda _server: FakeClient())
    monkeypatch.setattr(alloy_manager, "ssh_exec", fake_exec)
    monkeypatch.setattr(alloy_manager, "_upload", lambda *_args: (True, ""))

    result = alloy_manager.deploy_alloy(Server(
        name="ready", host="10.66.66.1", ssh_user="root", agent_type="remote",
    ))

    assert result["success"] is True
    assert not any("curl -fsSL" in command or "dpkg -i" in command for command in commands)
    assert any("systemctl restart alloy" in command for command in commands)
