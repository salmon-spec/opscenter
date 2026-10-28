import ipaddress
import sys
from types import SimpleNamespace

import pytest

from app import wireguard as wg


def test_allocation_excludes_live_persistent_peers_and_inventory():
    snapshot = {'addresses': ['10.66.66.1/24'], 'peers': [{'AllowedIPs': '10.66.66.2/31'}],
                'allowed_ips': ['public-key\t10.66.66.4/32']}
    assert wg.allocate('10.66.66.0/24', [snapshot], ['10.66.66.5']) == '10.66.66.6'
    with pytest.raises(ValueError): wg.allocate('0.0.0.0/0', [], [])


def test_discovery_updates_address_and_preserves_selected_tunnel(monkeypatch):
    server = SimpleNamespace(wireguard_ip='', agent_port=19100, agent_token='test')
    data = {'supported': True, 'interfaces': [{'addresses': ['bad', 'fe80::1/64', '10.66.66.14/24']}]}
    monkeypatch.setattr(wg, 'fetch_from_agent', lambda *_: data)
    assert wg.discover(server) is data
    assert server.wireguard_ip == '10.66.66.14'
    data['interfaces'].append({'addresses': ['10.1.1.1/24']})
    wg.discover(server)
    assert server.wireguard_ip == '10.66.66.14'
    data['supported'] = False
    wg.discover(server)
    assert server.wireguard_ip == '10.66.66.14'


def test_invalid_pool_endpoint_and_same_host_fail_before_access():
    host = '00000000-0000-0000-0000-000000000001'
    hub = '00000000-0000-0000-0000-000000000002'
    from fastapi import HTTPException
    for values in ({'hub_id': host}, {'pool': '0.0.0.0/0'}, {'endpoint': 'host;id:51820'}, {'endpoint': 'host:99999'}):
        with pytest.raises(HTTPException) as exc:
            wg.install(wg.Install(**({'server_id':host, 'hub_id':hub} | values)))
        assert exc.value.status_code == 422


def test_remote_config_parser_never_returns_secrets(monkeypatch):
    if sys.platform == 'win32': monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace())
    from app.wireguard_remote import config_info
    info = config_info('[Interface]\nPrivateKey=private-material\nAddress=10.66.66.21/32\n[Peer]\nPublicKey=public\nPresharedKey=secret-material\nAllowedIPs=10.66.66.0/24\n')
    assert info == {'addresses': ['10.66.66.21/32'], 'peers': [{'PublicKey':'public', 'AllowedIPs':'10.66.66.0/24'}]}


def test_dry_run_has_no_mutations_and_closes_connections(monkeypatch):
    from contextlib import contextmanager
    import uuid
    target = SimpleNamespace(id=uuid.uuid4(), enabled=True, host='192.168.1.164', wireguard_ip='')
    hub = SimpleNamespace(id=uuid.uuid4(), enabled=True, host='10.66.66.1', wireguard_ip='10.66.66.1')
    monkeypatch.setattr(wg, 'active_host_ids', lambda: {str(target.id), str(hub.id)})
    @contextmanager
    def db(): yield SimpleNamespace(query=lambda *_: SimpleNamespace(all=lambda: [target, hub]))
    monkeypatch.setattr(wg, 'get_db', db)
    closed, calls = [], []
    monkeypatch.setattr(wg, 'get_ssh_client', lambda row: SimpleNamespace(close=lambda: closed.append(row.id)))
    def remote(client, action, data):
        calls.append(action)
        return ({'running':True, 'configured':True, 'addresses':['10.66.66.1/24'], 'public_key':'pub',
                 'endpoint_candidates':[], 'listen_port':51820} if len(calls)==1 else
                {'running':False, 'configured':False, 'installed':False, 'addresses':[]})
    monkeypatch.setattr(wg, 'remote', remote)
    result=wg.provision(wg.Install(server_id=target.id, hub_id=hub.id, endpoint='vpn.example.com:51820'), ipaddress.ip_network('10.66.66.0/24'))
    assert result['status']=='planned' and result['address']=='10.66.66.2'
    assert calls==['inspect','inspect'] and len(closed)==2
