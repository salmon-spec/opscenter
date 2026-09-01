"""Regression tests for v4.6.6 service discovery coverage."""

import json
import os
import sys

from app import main
from fastapi.testclient import TestClient


AGENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'agent'))
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)
import scanner  # noqa: E402


def test_proxied_openapi_documentation():
    client = TestClient(main.app)
    schema = client.get('/api/v2/openapi.json')
    assert schema.status_code == 200
    assert schema.json()['info']['version'] == '4.6.6'
    docs = client.get('/api/v2/docs')
    assert docs.status_code == 200
    assert '/api/v2/openapi.json' in docs.text


def test_reachable_bind_supports_wildcards_ipv6_and_concrete_host_addresses():
    for value in ('0.0.0.0', '*', '::', '[::]', '10.66.66.6'):
        assert main._is_reachable_bind(value)
    for value in ('127.0.0.1', '::1', 'localhost'):
        assert not main._is_reachable_bind(value)


def test_agent_container_host_port_format_is_discovered():
    container = {'ports': [{'host_port': '12110', 'container_port': '12110', 'proto': 'tcp'}]}
    assert main._extract_public_ports(container) == ['12110']


def test_pve_guest_discovery_uses_guest_agent_and_lxc_addresses(monkeypatch):
    resources = [
        {'vmid': 101, 'name': 'vm-a', 'type': 'qemu', 'status': 'running', 'node': 'pve'},
        {'vmid': 102, 'name': 'ct-b', 'type': 'lxc', 'status': 'running', 'node': 'pve'},
        {'vmid': 103, 'name': 'off', 'type': 'qemu', 'status': 'stopped', 'node': 'pve'},
    ]

    def fake_run(command, timeout=5):
        if command[:3] == ['pvesh', 'get', '/cluster/resources']:
            return json.dumps(resources)
        if command[:4] == ['qm', 'guest', 'cmd', '101']:
            return json.dumps([{'ip-addresses': [{'ip-address': '10.66.66.21'}, {'ip-address': '127.0.0.1'}]}])
        if command[:3] == ['pct', 'exec', '102']:
            return '10.66.66.22 172.17.0.1'
        return ''

    monkeypatch.setattr(scanner.os.path, 'exists', lambda path: path == '/etc/pve')
    monkeypatch.setattr(scanner, 'run_cmd', fake_run)
    guests = scanner.scan_pve_guests()
    assert [(item['vmid'], item['ips']) for item in guests] == [
        (101, ['10.66.66.21']),
        (102, ['10.66.66.22']),
    ]


def test_pve_probe_is_bounded_to_configured_web_ports(monkeypatch):
    observed = []

    def fake_probe(address, port):
        observed.append((address, port))
        if port == 8080:
            return {'address': address, 'port': port, 'url': f'http://{address}:{port}/'}
        return None

    monkeypatch.setattr(scanner, '_probe_guest_port', fake_probe)
    services = scanner.scan_pve_guest_services([
        {'vmid': 101, 'name': 'vm-a', 'type': 'qemu', 'ips': ['10.66.66.21']},
    ])
    assert len(observed) == len(scanner.PVE_WEB_PORTS)
    assert services[0]['url'] == 'http://10.66.66.21:8080/'
    assert services[0]['source'] == 'pve_guest'
