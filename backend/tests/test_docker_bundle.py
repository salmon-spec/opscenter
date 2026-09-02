from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]


def test_docker_bundle_avoids_host_root_interfaces():
    compose = (ROOT / "deploy/docker/compose.yml").read_text(encoding="utf-8")
    assert "privileged:" not in compose
    assert "/var/run/docker.sock" not in compose
    assert "/run/systemd" not in compose
    assert "host.docker.internal:host-gateway" in compose


def test_docker_bundle_has_persistent_state_and_healthchecks():
    compose = (ROOT / "deploy/docker/compose.yml").read_text(encoding="utf-8")
    for value in ("POSTGRES_DATA_DIR", "LOKI_DATA_DIR", "OPSCENTER_CONFIG_DIR"):
        assert value in compose
    assert compose.count("healthcheck:") == 4
    assert "CONTAINERIZED" in compose


def test_docker_bundle_contains_prebuilt_frontend():
    dockerfile = (ROOT / "deploy/docker/frontend.Dockerfile").read_text(encoding="utf-8")
    assert "COPY frontend-vite/dist /srv/v3" in dockerfile
    assert (ROOT / "frontend-vite/dist/index.html").is_file()


def test_docker_installer_syncs_host_agent_token():
    installer = (ROOT / "deploy/docker/install.sh").read_text(encoding="utf-8")
    agent_installer = (ROOT / "deploy/docker/install-agent.sh").read_text(encoding="utf-8")
    assert "install-agent.sh" in installer
    assert "LOCAL_AGENT_TOKEN" in agent_installer
    assert "opsagent.service" in agent_installer
    assert "INSTALL_HOST_AGENT" in installer
    assert "systemctl show opsagent.service" in installer
    assert "LOKI_BIND_IP" in installer
    assert "http://$loki_probe_host:$loki_probe_port/ready" in installer


def test_containerized_local_docker_management_uses_ssh(monkeypatch):
    from app import control

    client = SimpleNamespace(close=lambda: None)
    server = SimpleNamespace(agent_type="local", host="10.66.66.5", name="OpsCenter")
    monkeypatch.setattr(control, "CONTAINERIZED", True)
    monkeypatch.setattr(control, "get_ssh_client", lambda _server: client)
    monkeypatch.setattr(control, "_remote_container_rows", lambda _client, include_stats: ["host-container"])

    assert control._load_container_rows(server, include_stats=False) == ["host-container"]


def test_containerized_local_process_management_uses_ssh(monkeypatch):
    from app import system_control

    client = SimpleNamespace(close=lambda: None)
    server = SimpleNamespace(agent_type="local", host="10.66.66.5", name="OpsCenter")
    monkeypatch.setattr(system_control, "CONTAINERIZED", True)
    monkeypatch.setattr(system_control, "get_ssh_client", lambda _server: client)
    monkeypatch.setattr(system_control, "ssh_exec", lambda *_args, **_kwargs: ("host-process", "", 0))

    assert system_control._process_command(server, "ps") == "host-process"
