"""Managed WireGuard discovery and serialized client onboarding."""
import base64
import ipaddress
import json
from pathlib import Path
import re
import shlex
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.auth import require_configured_operator
from app.database import engine, get_db
from app.models import Server
from app.agent_manager import fetch_agent_wireguard, fetch_from_agent
from app.ssh_manager import get_ssh_client
from app.host_inventory_bridge import publish_host_inventory_telemetry, active_host_ids

router = APIRouter(prefix='/api/v2', tags=['wireguard'], dependencies=[Depends(require_configured_operator)])


def discover(server):
    data = fetch_from_agent(server, lambda host: fetch_agent_wireguard(host, server.agent_port or 19100, server.agent_token or ''))
    if not isinstance(data, dict) or not data.get('supported'):
        return data
    addresses = []
    for iface in data.get('interfaces') or []:
        for value in iface.get('addresses') or []:
            try:
                address = ipaddress.ip_interface(value).ip
                if address.version == 4 and not address.is_loopback and not address.is_link_local:
                    addresses.append(str(address))
            except ValueError:
                continue
    # Keep a selected valid address; choose deterministically for multiple tunnels.
    if addresses:
        server.wireguard_ip = server.wireguard_ip if server.wireguard_ip in addresses else sorted(set(addresses), key=ipaddress.ip_address)[0]
    return data


def remote(client, action, data):
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    command = 'sudo -n python3 - ' + shlex.quote(action) + ' ' + shlex.quote(payload)
    stdin, stdout, stderr = client.exec_command(command, timeout=360)
    stdin.write(Path(__file__).with_name('wireguard_remote.py').read_text()); stdin.flush(); stdin.channel.shutdown_write()
    raw = stdout.read(128000)
    result = json.loads(raw)
    if stdout.channel.recv_exit_status() or result.get('error'):
        raise ValueError(result.get('error', 'WireGuard 远程操作失败'))
    return result


def allocate(pool, snapshots, registered):
    network = ipaddress.ip_network(pool)
    if network.version != 4 or not network.is_private or not 24 <= network.prefixlen <= 30:
        raise ValueError('地址池须为私有 IPv4 /24 至 /30 网段')
    reserved = []
    for snapshot in snapshots:
        reserved.extend(ipaddress.ip_network(str(ipaddress.ip_interface(a).ip) + '/32') for a in snapshot.get('addresses', []))
        networks = [p.get('AllowedIPs', '') for p in snapshot.get('peers', [])]
        networks += [line.split(None, 1)[1] for line in snapshot.get('allowed_ips', []) if len(line.split(None, 1)) == 2]
        for value in networks:
            reserved.extend(ipaddress.ip_network(n, strict=False) for n in value.replace(',', ' ').split() if n != '(none)')
    reserved.extend(ipaddress.ip_network(str(ipaddress.ip_address(a)) + '/32') for a in registered if a)
    return next((str(a) for a in network.hosts() if not any(a in n for n in reserved)), None)


class Install(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    server_id: uuid.UUID
    hub_id: uuid.UUID
    interface: str = Field(default='wg0', pattern=r'^[a-zA-Z0-9_=+.-]{1,15}$')
    pool: str = Field(default='10.66.66.0/24', max_length=32)
    endpoint: str = Field(default='', max_length=253)
    dry_run: bool = True


@router.get('/servers/{server_id}/wireguard')
def host_wireguard(server_id: uuid.UUID):
    with get_db() as db:
        row = db.query(Server).filter(Server.id == server_id, Server.enabled == True).first()
        if not row: raise HTTPException(404, '主机不存在或已停用')
        data = discover(row)
        db.commit(); publish_host_inventory_telemetry(row)
        return {'wireguard_ip': row.wireguard_ip or '', 'wireguard': data}


@router.post('/wireguard/install')
def install(data: Install):
    if data.server_id == data.hub_id: raise HTTPException(422, '目标主机不能是 Hub 本身')
    try:
        network = ipaddress.ip_network(data.pool)
        if network.version != 4 or not network.is_private or not 24 <= network.prefixlen <= 30:
            raise ValueError('地址池须为私有 IPv4 /24 至 /30 网段')
        if data.endpoint and not re.fullmatch(r'[a-zA-Z0-9.-]+:[0-9]{1,5}', data.endpoint):
            raise ValueError('Endpoint 使用主机名或 IPv4:端口')
        if data.endpoint and not 1 <= int(data.endpoint.rsplit(':', 1)[1]) <= 65535:
            raise ValueError('Endpoint 端口无效')
        with engine.connect() as lock:
            if not lock.execute(text('SELECT pg_try_advisory_lock(2026102801)')).scalar():
                raise HTTPException(409, '其他 WireGuard 作业正在分配地址，请稍后重试')
            lock.commit()
            try:
                return provision(data, network)
            finally:
                lock.execute(text('SELECT pg_advisory_unlock(2026102801)')); lock.commit()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


def provision(data, network):
    ids = active_host_ids()
    if ids is None: raise HTTPException(503, '主机资产清单暂不可用，未执行安装')
    with get_db() as db:
        rows = db.query(Server).all()
        active = {r.id: r for r in rows if r.enabled and str(r.id) in ids}
        target, hub = active.get(data.server_id), active.get(data.hub_id)
        if not target or not hub: raise HTTPException(404, '目标主机或 Hub 不存在/已停用')
        registered = [a for r in rows for a in (r.wireguard_ip, r.host) if a and re.fullmatch(r'\d+\.\d+\.\d+\.\d+', a)]
    clients = []
    try:
        for row in (target, hub):
            client = get_ssh_client(row)
            if not client: raise ValueError('主机 SSH 连接失败，请核对管理凭据及主机密钥')
            clients.append(client)
        tc, hc = clients
        params = {'interface': data.interface}
        hs, ts = remote(hc, 'inspect', params), remote(tc, 'inspect', params)
        if not hs['running'] or not hs['configured']: raise ValueError('Hub 接口须已运行且有持久配置')
        hub_addresses = [str(ipaddress.ip_interface(a).ip) for a in hs['addresses'] if ipaddress.ip_interface(a).ip in network]
        if not hub_addresses: raise ValueError('地址池不包含 Hub 接口地址')
        endpoint = data.endpoint or (f"{hs['endpoint_candidates'][0]}:{hs['listen_port']}" if hs['endpoint_candidates'] else '')
        if not endpoint: raise ValueError('无法识别 Hub 公网 Endpoint，请在页面填写')
        existing = [str(ipaddress.ip_interface(a).ip) for a in ts['addresses'] if ipaddress.ip_interface(a).ip in network]
        if ts['configured'] or ts['running']:
            if not existing or not any(p.get('PublicKey') == hs['public_key'] for p in ts['peers']):
                raise ValueError('目标已有其他 WireGuard 配置，保持原配置；请核对接口/Hub')
            address = existing[0]
        else:
            address = allocate(data.pool, [hs], registered)
            if not address: raise ValueError('地址池已用尽')
        result = {'server_id': str(target.id), 'hub_id': str(hub.id), 'interface': data.interface,
                  'address': address, 'pool': data.pool, 'endpoint': endpoint, 'dry_run': data.dry_run}
        if data.dry_run: return {**result, 'status': 'planned', 'installed': ts['installed']}
        params.update(server_id=str(target.id), address=address, pool=data.pool, endpoint=endpoint, hub_public_key=hs['public_key'])
        if not ts['configured']:
            ts = remote(tc, 'prepare', params)
        public = remote(tc, 'public-key', params)['public_key']
        remote(hc, 'add-peer', {**params, 'public_key': public})
        live = remote(tc, 'start', {**params, 'hub_address': hub_addresses[0]})
        with get_db() as db:
            row = db.query(Server).filter(Server.id == data.server_id, Server.enabled == True).one()
            row.wireguard_ip = address; db.commit(); publish_host_inventory_telemetry(row)
        return {**result, 'status': 'connected', 'changed': bool(ts.get('changed')), 'interfaces': live['addresses']}
    finally:
        for client in clients: client.close()
