from types import SimpleNamespace

from app import agent_manager


def test_monitoring_address_prefers_lan_then_main_address(monkeypatch):
    monkeypatch.setattr(agent_manager, "LOCAL_AGENT_HOST", "host.docker.internal")

    remote = SimpleNamespace(agent_type="remote", lan_ip="192.168.1.25", host="10.66.66.25")
    assert agent_manager.resolve_agent_host(remote) == "192.168.1.25"

    remote.lan_ip = ""
    assert agent_manager.resolve_agent_host(remote) == "10.66.66.25"

    local = SimpleNamespace(agent_type="local", lan_ip="192.168.1.15", host="10.66.66.15")
    assert agent_manager.resolve_agent_host(local) == "host.docker.internal"


def test_agent_fetch_falls_back_to_wireguard_and_caches_success(monkeypatch):
    monkeypatch.setattr(agent_manager, "_LAST_WORKING_HOST", {})
    server = SimpleNamespace(
        id="server-1", agent_type="remote", lan_ip="192.168.1.25",
        wireguard_ip="10.66.66.25", host="203.0.113.25",
        preferred_management_channel="auto", management_address_override="",
    )
    attempted = []

    result = agent_manager.fetch_from_agent(
        server,
        lambda host: attempted.append(host) or ({"ok": True} if host == "10.66.66.25" else None),
    )

    assert result == {"ok": True}
    assert attempted == ["192.168.1.25", "10.66.66.25"]
    assert agent_manager.resolve_management_hosts(server)[0] == "10.66.66.25"
