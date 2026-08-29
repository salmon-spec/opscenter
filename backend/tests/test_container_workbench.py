"""Container workbench helpers used by OpsCenter 4.2.0."""

import pytest
from fastapi import HTTPException

from app import control
from app.control import (
    _container_id,
    _container_summary,
    _docker_memory,
    _docker_resource_id,
    _image_summary,
    _network_summary,
    _percent,
    _volume_summary,
)
from app.ssh_terminal import create_session, get_session, remove_session


def test_container_identifier_allows_docker_names_and_ids():
    assert _container_id("stirling-pdf") == "stirling-pdf"
    assert _container_id("8a2f9c13d120") == "8a2f9c13d120"


@pytest.mark.parametrize("value", ["", "two containers", "name;rm", "../docker", "$(id)"])
def test_container_identifier_rejects_shell_syntax(value):
    with pytest.raises(HTTPException):
        _container_id(value)


def test_docker_resource_identifier_supports_image_references():
    assert _docker_resource_id("ghcr.io/example/app:4.2") == "ghcr.io/example/app:4.2"
    assert _docker_resource_id("sha256:" + "a" * 64).startswith("sha256:")


@pytest.mark.parametrize("value", ["", "name with space", "name;rm", "$(id)", "../ bad"])
def test_docker_resource_identifier_rejects_shell_syntax(value):
    with pytest.raises(HTTPException):
        _docker_resource_id(value)


def test_container_summary_normalizes_network_ports_and_mounts():
    attrs = {
        "Id": "a" * 64,
        "Name": "/it-tools",
        "Created": "2026-08-28T00:00:00Z",
        "Config": {"Image": "corentinth/it-tools:latest"},
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
        "NetworkSettings": {
            "Networks": {"bridge": {"IPAddress": "172.17.0.2"}},
            "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8081"}]},
        },
        "Mounts": [{"Type": "bind", "Source": "/data", "Destination": "/app/data", "RW": True}],
    }
    result = _container_summary(attrs)
    assert result["name"] == "it-tools"
    assert result["health"] == "healthy"
    assert result["ip_addresses"] == ["172.17.0.2"]
    assert result["ports"][0]["host_port"] == "8081"
    assert result["restart_policy"] == "unless-stopped"


def test_container_metric_parsers_are_defensive():
    assert _percent("12.34%") == 12.34
    assert _percent("n/a") == 0
    assert _docker_memory({"memory_stats": {"usage": 1000, "limit": 2000, "stats": {"inactive_file": 200}}}) == (800, 2000, 40.0)


def test_docker_resource_summaries_mark_usage_and_protected_networks():
    image = _image_summary({
        "Id": "sha256:" + "c" * 64,
        "RepoTags": ["example/app:latest"],
        "Size": 1024,
        "Config": {"Labels": {"org.example": "app"}},
    }, {"sha256:" + "c" * 64})
    assert image["in_use"] is True
    assert image["dangling"] is False
    assert image["size"] == 1024

    network = _network_summary({
        "Id": "d" * 64,
        "Name": "bridge",
        "Driver": "bridge",
        "IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]},
        "Containers": {"one": {}},
    })
    assert network["system"] is True
    assert network["container_count"] == 1
    assert network["subnets"] == ["172.17.0.0/16"]

    volume = _volume_summary({"Name": "app-data", "Driver": "local", "UsageData": {"Size": 2048, "RefCount": 0}}, {"app-data"})
    assert volume["in_use"] is True
    assert volume["size"] == 2048


def test_remote_rows_join_inspect_and_stats(monkeypatch):
    attrs = [{
        "Id": "b" * 64,
        "Name": "/stirling-pdf",
        "Config": {"Image": "stirlingtools/stirling-pdf:latest"},
        "State": {"Status": "running"},
        "HostConfig": {},
        "NetworkSettings": {"Networks": {}, "Ports": {}},
        "Mounts": [],
    }]
    replies = iter([
        ("b" * 64 + "\n", "", 0),
        (__import__("json").dumps(attrs), "", 0),
        (__import__("json").dumps({"Name": "stirling-pdf", "CPUPerc": "1.25%", "MemPerc": "6.50%", "MemUsage": "100MiB / 1GiB"}) + "\n", "", 0),
    ])
    monkeypatch.setattr(control, "ssh_exec", lambda *_args, **_kwargs: next(replies))
    rows = control._remote_container_rows(object())
    assert rows[0]["cpu_percent"] == 1.25
    assert rows[0]["memory_percent"] == 6.5


def test_terminal_session_keeps_server_generated_initial_command():
    sid, error = create_session("server-test", "server", "127.0.0.1", 22, "root", initial_command="docker exec -it safe-name sh")
    try:
        assert not error
        assert get_session(sid).initial_command == "docker exec -it safe-name sh"
    finally:
        remove_session(sid)
