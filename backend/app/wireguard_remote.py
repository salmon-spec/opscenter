"""Root-side WireGuard operations. Responses contain public configuration only."""
import base64
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


def run(*args, input=None, timeout=20):
    proc = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError('命令执行失败：' + args[0])
    return proc.stdout.strip()


def config_info(content):
    info = {'addresses': [], 'peers': []}
    peer = None
    for line in content.splitlines():
        line = line.split('#', 1)[0].strip()
        if line == '[Peer]':
            peer = {}; info['peers'].append(peer)
        elif line == '[Interface]':
            peer = None
        elif '=' in line:
            key, value = (s.strip() for s in line.split('=', 1))
            if key == 'Address' and peer is None:
                info['addresses'].extend(s.strip() for s in value.split(','))
            elif peer is not None and key in {'PublicKey', 'AllowedIPs', 'Endpoint'}:
                peer[key] = value
    return info


def inspect(iface):
    path = Path('/etc/wireguard') / (iface + '.conf')
    content = path.read_text() if path.exists() else ''
    result = config_info(content)
    result.update(installed=bool(shutil.which('wg')), configured=path.exists(), running=False,
                  public_key='', listen_port=0, allowed_ips=[], handshakes=[], endpoint_candidates=[])
    if shutil.which('wg') and iface in run('wg', 'show', 'interfaces').split():
        result.update(running=True, public_key=run('wg', 'show', iface, 'public-key'),
                      listen_port=int(run('wg', 'show', iface, 'listen-port')),
                      allowed_ips=run('wg', 'show', iface, 'allowed-ips').splitlines(),
                      handshakes=run('wg', 'show', iface, 'latest-handshakes').splitlines())
    for item in json.loads(run('ip', '-j', '-4', 'addr')):
        addresses = [a['local'] + '/' + str(a['prefixlen']) for a in item.get('addr_info', []) if a.get('local')]
        if item['ifname'] == iface:
            result['addresses'] = addresses
        elif item['ifname'] != 'lo':
            result['endpoint_candidates'].extend(str(ipaddress.ip_interface(a).ip) for a in addresses
                                                 if ipaddress.ip_interface(a).ip.is_global)
    result['managed'] = '# OpsCenter host ' in content
    return result


def backup(path):
    directory = Path('/var/backups/opscenter-wireguard')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = directory / (path.name + '.' + str(time.time_ns()))
    shutil.copy2(path, dest); dest.chmod(0o600)


def write_config(path, content):
    temp = path.with_suffix('.tmp')
    with temp.open('x', encoding='utf-8') as f:
        os.chmod(temp, 0o600); f.write(content); f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)


def execute(action, data):
    iface = data['interface']
    if not re.fullmatch(r'[a-zA-Z0-9_=+.-]{1,15}', iface):
        raise ValueError('接口名称无效')
    path = Path('/etc/wireguard') / (iface + '.conf')
    if action == 'inspect':
        return inspect(iface)
    if action == 'prepare':
        if path.exists():
            content = path.read_text()
            if '# OpsCenter host ' + data['server_id'] not in content:
                raise ValueError('目标已存在 WireGuard 配置，保持原配置；请先核对接口')
            return inspect(iface)
        if shutil.which('wg') and iface in run('wg', 'show', 'interfaces').split():
            raise ValueError('目标接口已存在，保持原配置')
        if not shutil.which('wg'):
            if not shutil.which('apt-get'):
                raise ValueError('自动安装当前支持 Debian/Ubuntu apt 主机')
            run('apt-get', 'update', timeout=120)
            run('env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', 'install', '-y', 'wireguard-tools', timeout=180)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        private = run('wg', 'genkey')
        public = run('wg', 'pubkey', input=private + '\n')
        content = (f"# OpsCenter host {data['server_id']}\n[Interface]\nAddress = {data['address']}/32\n"
                   f"PrivateKey = {private}\n\n[Peer]\nPublicKey = {data['hub_public_key']}\n"
                   f"Endpoint = {data['endpoint']}\nAllowedIPs = {data['pool']}\nPersistentKeepalive = 25\n")
        write_config(path, content)
        return {**inspect(iface), 'public_key': public, 'changed': True}
    if action == 'public-key':
        # Derive locally; private key never crosses the SSH response boundary.
        content = path.read_text()
        key = next(line.split('=', 1)[1].strip() for line in content.splitlines() if line.strip().startswith('PrivateKey'))
        return {'public_key': run('wg', 'pubkey', input=key + '\n')}
    if action == 'add-peer':
        original = path.read_text()
        live = inspect(iface)
        pub, address = data['public_key'], data['address'] + '/32'
        peers = config_info(original)['peers']
        used = [(p.get('PublicKey'), p.get('AllowedIPs', '')) for p in peers]
        used += [tuple(line.split(None, 1)) for line in live['allowed_ips'] if len(line.split(None, 1)) == 2]
        for key, networks in used:
            for network in networks.replace(',', ' ').split():
                if network == '(none)':
                    continue
                if ipaddress.ip_address(data['address']) in ipaddress.ip_network(network, strict=False) and key != pub:
                    raise ValueError('地址已由其他 Peer 使用')
            if key == pub and networks.replace(' ', '') != address:
                raise ValueError('现有 Peer 地址不一致')
        exists = any(p.get('PublicKey') == pub for p in peers)
        if not exists:
            backup(path)
            write_config(path, original.rstrip() + f"\n\n# OpsCenter host {data['server_id']}\n[Peer]\nPublicKey = {pub}\nAllowedIPs = {address}\n")
        try:
            run('wg', 'set', iface, 'peer', pub, 'allowed-ips', address)
        except Exception:
            if not exists: write_config(path, original)
            raise
        return {'changed': not exists}
    if action == 'start':
        run('systemctl', 'enable', '--now', 'wg-quick@' + iface, timeout=45)
        run('ping', '-c', '3', '-W', '2', '-I', iface, data['hub_address'], timeout=10)
        result = inspect(iface)
        if not any(int(line.split()[-1]) > int(time.time()) - 180 for line in result['handshakes']):
            raise ValueError('隧道未完成近期握手，请检查 UDP、防火墙和 Hub Endpoint')
        return result
    raise ValueError('操作无效')


if __name__ == '__main__':
    os.umask(0o077)
    try:
        with open('/run/lock/opscenter-wireguard.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = execute(sys.argv[1], json.loads(base64.b64decode(sys.argv[2])))
        print(json.dumps(result))
    except Exception as error:
        # Command output/configuration may contain secrets. Return our own messages only.
        print(json.dumps({'error': str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__}))
        sys.exit(1)
