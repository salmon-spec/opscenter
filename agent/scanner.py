#!/usr/bin/env python3
"""OpsAgent Scanner - Service discovery for remote servers.

Scans Docker containers, listening ports, systemd services,
stopped containers, and nginx configurations.
Pure stdlib, no external dependencies.
"""

import json
import os
import subprocess
import re
import time


def run_cmd(cmd, timeout=5):
    """Run a shell command and return stdout. Returns '' on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           shell=isinstance(cmd, str))
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ''


def _get_host_net_ports(container_name, container_id):
    """For containers using network_mode=host, discover listening ports
    by finding the container PID and filtering ss -tlnp by that PID
    and its children.
    """
    ports = []
    try:
        pid_str = run_cmd(['docker', 'inspect', '--format', '{{.State.Pid}}',
                           container_id[:12] if container_id else container_name])
        if not pid_str or pid_str == '0':
            return ports
        pid = pid_str.strip()

        container_pids = {pid}
        children = run_cmd(['pgrep', '-P', pid])
        if children:
            for cp in children.strip().split('\n'):
                cp = cp.strip()
                if cp:
                    container_pids.add(cp)
                    gc = run_cmd(['pgrep', '-P', cp])
                    if gc:
                        for gcp in gc.strip().split('\n'):
                            gcp = gcp.strip()
                            if gcp:
                                container_pids.add(gcp)

        ss_out = run_cmd(['ss', '-tlnp'], timeout=5)
        if not ss_out:
            return ports
        for line in ss_out.split('\n')[1:]:
            parts = line.split()
            if len(parts) < 5:
                continue
            local_addr = parts[3] if len(parts) > 3 else parts[-2]
            process_info = ' '.join(parts[4:]) if len(parts) > 4 else ''

            matched = False
            for cpid in container_pids:
                if f'pid={cpid},' in process_info or f'pid={cpid})' in process_info:
                    matched = True
                    break
            if not matched:
                continue

            bind_ip = '0.0.0.0'
            port_num = 0
            if ':' in local_addr:
                if local_addr.startswith('['):
                    bracket_end = local_addr.index(']')
                    bind_ip = local_addr[1:bracket_end]
                    port_str = local_addr[bracket_end + 2:]
                else:
                    bind_ip = local_addr.rsplit(':', 1)[0]
                    port_str = local_addr.rsplit(':', 1)[-1]
                try:
                    port_num = int(port_str)
                except ValueError:
                    continue

            if bind_ip in ('::', ':::'):
                bind_ip = '0.0.0.0'
            elif bind_ip.startswith('127.'):
                bind_ip = '127.0.0.1'

            if port_num > 0:
                ports.append({
                    'host_port': str(port_num),
                    'container_port': str(port_num),
                    'proto': 'tcp',
                    'bind_ip': bind_ip,
                    'raw': f'{bind_ip}:{port_num}->host_net',
                })
    except Exception:
        pass
    return ports


def scan_docker_containers():
    """Scan running Docker containers via `docker ps`."""
    containers = []
    output = run_cmd(['docker', 'ps', '--format',
                      '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}|{{.Networks}}|{{.ID}}'])
    if not output:
        return containers
    for line in output.split('\n'):
        if not line.strip():
            continue
        parts = line.split('|', 5)
        name = parts[0].strip() if len(parts) > 0 else ''
        image = parts[1].strip() if len(parts) > 1 else ''
        status_raw = parts[2].strip() if len(parts) > 2 else ''
        ports_raw = parts[3].strip() if len(parts) > 3 else ''
        networks_raw = parts[4].strip() if len(parts) > 4 else ''
        container_id = parts[5].strip() if len(parts) > 5 else ''

        ports = []
        if ports_raw:
            for p in ports_raw.split(', '):
                p = p.strip()
                if '->' in p:
                    host_part = p.split('->')[0]
                    container_part = p.split('->')[1]
                    host_port = host_part.split(':')[-1] if ':' in host_part else host_part
                    container_port = container_part.split('/')[0] if '/' in container_part else container_part
                    proto = container_part.split('/')[1] if '/' in container_part else 'tcp'
                    ports.append({
                        'host_port': host_port,
                        'container_port': container_port,
                        'proto': proto,
                        'raw': p,
                    })

        networks = [n.strip() for n in networks_raw.split(',') if n.strip()] if networks_raw else []

        is_host_network = 'host' in networks
        if is_host_network and not ports:
            host_ports = _get_host_net_ports(name, container_id)
            if host_ports:
                ports = host_ports
                port_summaries = []
                for hp in host_ports:
                    port_summaries.append(f"{hp.get('bind_ip','0.0.0.0')}:{hp['host_port']}->{hp['container_port']}/{hp['proto']}")
                ports_raw = ', '.join(port_summaries)

        labels = {}
        label_output = run_cmd(['docker', 'inspect', '--format',
                                 '{{range $k,$v := .Config.Labels}}{{$k}}={{$v}}{{println}}{{end}}',
                                 container_id[:12] if container_id else name])
        if label_output:
            for lbl in label_output.split('\n'):
                if '=' in lbl:
                    k, v = lbl.split('=', 1)
                    labels[k.strip()] = v.strip()

        running = 'Up' in status_raw
        containers.append({
            'name': name,
            'image': image,
            'status': 'running' if running else 'exited',
            'ports': ports,
            'port_summary': ports_raw,
            'networks': networks,
            'container_id': container_id[:12] if container_id else '',
            'labels': labels,
        })
    return containers


def scan_stopped_containers():
    """Scan stopped Docker containers."""
    try:
        result = subprocess.run(
            ['docker', 'ps', '-a', '--filter', 'status=exited', '--filter', 'status=created',
             '--format', '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}'],
            capture_output=True, text=True, timeout=30
        )
        containers = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) >= 5:
                containers.append({
                    'id': parts[0][:12],
                    'name': parts[1],
                    'image': parts[2],
                    'ports': parts[3],
                    'status': 'exited',
                    'state': parts[4]
                })
        return containers
    except Exception:
        return []


def scan_listening_ports():
    """Scan listening TCP/UDP ports via `ss -tlnpu`."""
    ports = []
    output = run_cmd(['ss', '-tlnpu'], timeout=3)
    if not output:
        return ports
    for line in output.split('\n')[1:]:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0].lower()
        state = parts[1] if len(parts) > 1 else ''
        local_addr = parts[4] if len(parts) > 4 else ''

        bind_ip = ''
        port = ''
        if ':' in local_addr:
            if local_addr.startswith('['):
                bracket_end = local_addr.index(']')
                bind_ip = local_addr[1:bracket_end]
                port = local_addr[bracket_end + 2:]
            else:
                bind_ip = local_addr.rsplit(':', 1)[0]
                port = local_addr.rsplit(':', 1)[-1]

        process_info = ' '.join(parts[5:]) if len(parts) > 5 else ''
        process_name = ''
        pid = ''
        if 'users:((' in process_info:
            try:
                info_part = process_info.split('users:((')[1].rstrip('))')
                for seg in info_part.split(','):
                    seg = seg.strip()
                    if seg.startswith('"'):
                        process_name = seg.strip('"')
                    elif seg.startswith('pid='):
                        pid = seg.split('=')[1]
            except Exception:
                pass

        try:
            port_int = int(port)
        except ValueError:
            continue

        _SKIP_PROCS = {'hbrclient', 'hbrclientupdater', 'snapd', 'packagekitd', 'polkitd', 'rtkit-daemon',
                       'containerd', 'dockerd', 'docker-proxy', 'containerd-shim'}
        if process_name in _SKIP_PROCS:
            continue

        ports.append({
            'port': port_int,
            'proto': proto.replace('6', ''),
            'bind_ip': bind_ip,
            'state': state,
            'process': process_name,
            'pid': pid,
        })

    seen = set()
    unique = []
    for p in ports:
        key = (p['port'], p['proto'], p['process'])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def scan_systemd_services():
    """Scan running systemd services."""
    _SKIP_PREFIXES = (
        'systemd-', 'dbus-', 'dbus.', 'user-', 'user@', 'session-',
        'getty@', 'serial-', 'multi-user-', 'graphical-', 'networkd-',
        'polkit', 'udisks', 'accounts-daemon', 'irqbalance',
        'thermald', 'powerd', 'fwupd', 'packagekit', 'snapd.',
        'ModemManager', 'NetworkManager', 'wpa_supplicant',
        'cron', 'atd', 'rsyslog', 'logrotate',
        'rsync', 'chrony', 'emergency', 'rescue',
        'kmod', 'lvm2', 'dm-event', 'multipathd', 'mdmonitor',
        'cloud-', 'snapd', 'unattended', 'apt-daily', 'dpkg-',
        'keyboard', 'console', 'plymouth', 'ufw',
        # v3.20.0 补充：根据实际扫描结果添加
        'aliyun', 'aegis', 'hbrclient', 'ssh', 'sshd',
        'containerd', 'docker', 'tuned', 'auditd', 'fail2ban',
        'opsagent', 'opscenter-backend',
        'acpid', 'apcupsd', 'autofs', 'avahi',
        'blk-availability', 'brandbot', 'cpupower',
        'dbus', 'dmraid', 'dracut', 'ebtables',
        'fstrim', 'gpm', 'halt', 'init', 'ip6tables', 'iptables',
        'kdump', 'killproc', 'kexec', 'libvirtd',
        'mcstrans', 'messagebus', 'microcode',
        'netconsole', 'netfs', 'nfs', 'nfslock', 'nscd',
        'portreserve', 'postfix', 'procps', ' quota_nld',
        'rc', 'rc-local', 'rdisc', 'restorecond',
        'rngd', 'rpcbind', 'rpcidmapd', 'saslauthd',
        'smartd', 'snmpd', 'spice-vdagentd', 'ssext',
        'sysstat', 'system-setup', 'tcsd', 'vboxadd',
        'vboxdracf', 'vgauthd', 'vmtoolsd', 'vmware',
        'wpa_supplicant', 'xen', 'yum', 'zfs',
    )
    services = []
    output = run_cmd(['systemctl', 'list-units', '--type=service', '--state=running',
                      '--no-pager', '--no-legend'], timeout=5)
    if not output:
        return services
    for line in output.split('\n'):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        name = parts[0]
        if any(name.startswith(prefix) for prefix in _SKIP_PREFIXES):
            continue
        description = ' '.join(parts[4:]) if len(parts) > 4 else ''
        services.append({
            'name': name,
            'status': 'active',
            'description': description,
        })
    return services


def scan_nginx_configs():
    """Parse nginx configs to discover routed services."""
    services = []
    nginx_dirs = ['/etc/nginx/sites-enabled/', '/etc/nginx/conf.d/']
    try:
        import glob
        import re
        config_files = []
        for d in nginx_dirs:
            config_files.extend(glob.glob(os.path.join(d, '*')))

        for cfg_file in config_files:
            try:
                with open(cfg_file) as f:
                    content = f.read()
                server_blocks = re.findall(r'server\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', content, re.DOTALL)
                for block in server_blocks:
                    server_names = re.findall(r'server_name\s+([^;]+);', block)
                    listen_ports = re.findall(r'listen\s+(\d+)', block)
                    locations = re.findall(r'location\s+([^\s{]+)\s*\{[^}]*proxy_pass\s+([^;]+);', block, re.DOTALL)

                    for loc_path, proxy in locations:
                        name = server_names[0].split()[0] if server_names else 'nginx-service'
                        port = listen_ports[0] if listen_ports else '80'
                        url = proxy.strip().rstrip(';')
                        if url.startswith('http'):
                            services.append({
                                'name': f"{name}{loc_path}",
                                'url': url,
                                'source': 'nginx',
                                'category': '网络与代理',
                                'container_name': f"nginx:{name}{loc_path}"
                            })
            except Exception:
                continue
        return services
    except Exception:
        return []


IMAGE_PREFIX_URLS = {
    'redis': {'url': 'redis://__HOST__:6379', 'category': '数据存储', 'name': 'Redis'},
    'mysql': {'url': 'mysql://__HOST__:3306', 'category': '数据存储', 'name': 'MySQL'},
    'postgres': {'url': 'postgresql://__HOST__:5432', 'category': '数据存储', 'name': 'PostgreSQL'},
    'mongo': {'url': 'mongodb://__HOST__:27017', 'category': '数据存储', 'name': 'MongoDB'},
    'rabbitmq': {'url': 'http://__HOST__:15672', 'category': '消息与注册', 'name': 'RabbitMQ Management'},
    'nacos': {'url': 'http://__HOST__:8848/nacos', 'category': '消息与注册', 'name': 'Nacos'},
    'elasticsearch': {'url': 'http://__HOST__:9200', 'category': '数据存储', 'name': 'Elasticsearch'},
}

PORT_PROTOCOL_HINTS = {
    80: 'http', 443: 'https', 8080: 'http', 8443: 'https',
    3000: 'http', 5000: 'http', 9000: 'http', 9090: 'http',
    3306: 'mysql', 5432: 'postgresql', 6379: 'redis',
    27017: 'mongodb', 9200: 'http', 15672: 'http',
}


def scan_all():
    """Run all scanners and return combined result."""
    now = time.time()
    return {
        'containers': scan_docker_containers(),
        'stopped_containers': scan_stopped_containers(),
        'ports': scan_listening_ports(),
        'systemd_services': scan_systemd_services(),
        'nginx_services': scan_nginx_configs(),
        'scanned_at': now,
        'scan_duration_ms': int((time.time() - now) * 1000),
    }


def diff_scans(old_result, new_result):
    """Compare two scan results, return changes."""
    changes = {'added': [], 'removed': [], 'changed': []}

    old_containers = {c['name']: c for c in old_result.get('containers', [])}
    new_containers = {c['name']: c for c in new_result.get('containers', [])}

    for name in new_containers:
        if name not in old_containers:
            changes['added'].append({'type': 'container', 'name': name, 'data': new_containers[name]})
        elif new_containers[name]['status'] != old_containers[name]['status'] or \
             new_containers[name]['port_summary'] != old_containers[name]['port_summary']:
            changes['changed'].append({'type': 'container', 'name': name,
                                        'old': old_containers[name], 'new': new_containers[name]})

    for name in old_containers:
        if name not in new_containers:
            changes['removed'].append({'type': 'container', 'name': name, 'data': old_containers[name]})

    old_ports = {(p['port'], p['proto']): p for p in old_result.get('ports', [])}
    new_ports = {(p['port'], p['proto']): p for p in new_result.get('ports', [])}

    for key in new_ports:
        if key not in old_ports:
            changes['added'].append({'type': 'port', 'name': f"{key[0]}/{key[1]}", 'data': new_ports[key]})
    for key in old_ports:
        if key not in new_ports:
            changes['removed'].append({'type': 'port', 'name': f"{key[0]}/{key[1]}", 'data': old_ports[key]})

    old_svc = {s['name']: s for s in old_result.get('systemd_services', [])}
    new_svc = {s['name']: s for s in new_result.get('systemd_services', [])}
    for name in new_svc:
        if name not in old_svc:
            changes['added'].append({'type': 'systemd', 'name': name, 'data': new_svc[name]})
    for name in old_svc:
        if name not in new_svc:
            changes['removed'].append({'type': 'systemd', 'name': name, 'data': old_svc[name]})

    return changes


if __name__ == '__main__':
    import pprint
    result = scan_all()
    result['scan_duration_ms'] = int((time.time() - result['scanned_at']) * 1000)
    print(f"Scan completed in {result['scan_duration_ms']}ms")
    print(f"  Containers: {len(result['containers'])}")
    print(f"  Stopped containers: {len(result['stopped_containers'])}")
    print(f"  Ports: {len(result['ports'])}")
    print(f"  Systemd services: {len(result['systemd_services'])}")
    print(f"  Nginx services: {len(result['nginx_services'])}")
    print()
    pprint.pprint(result, width=120)



# === Network monitoring (v3.25.1) ===

def collect_network():
    """读取 /proc/net/dev 返回各网卡 rx/tx 计数器（字节数）。

    返回 { iface: {rx_bytes, tx_bytes, rx_packets, tx_packets, rx_errors, tx_errors} }
    速率计算由调用方基于两次采样差值完成。
    """
    result = {}
    try:
        with open('/proc/net/dev') as f:
            lines = f.readlines()[2:]  # 跳过表头两行
        for line in lines:
            if ':' not in line:
                continue
            iface, rest = line.split(':', 1)
            fields = rest.split()
            if len(fields) < 16:
                continue
            result[iface.strip()] = {
                'rx_bytes': int(fields[0]),
                'rx_packets': int(fields[1]),
                'rx_errors': int(fields[2]),
                'tx_bytes': int(fields[8]),
                'tx_packets': int(fields[9]),
                'tx_errors': int(fields[10]),
            }
    except Exception:
        pass
    return result


def ping_latency(target, count=4):
    """对目标做 ICMP 探测，返回延迟/丢包/抖动统计。"""
    try:
        r = subprocess.run(['ping', '-c', str(count), '-W', '2', target],
                           capture_output=True, text=True, timeout=count * 3 + 5)
        out = r.stdout
    except Exception:
        return {'target': target, 'latency_ms': None, 'loss_pct': 100.0,
                'jitter_ms': None, 'error': 'ping command failed'}
    loss = 0.0
    lat = None
    jitter = None
    m = re.search(r'(\d+)% packet loss', out)
    if m:
        loss = float(m.group(1))
    m = re.search(r'rtt min/avg/max/mdev = [\d.]+/([\d.]+)/[\d.]+/([\d.]+)', out)
    if m:
        lat = float(m.group(1))
        jitter = float(m.group(2))
    return {'target': target, 'latency_ms': lat, 'loss_pct': loss, 'jitter_ms': jitter}


def scan_log_pattern(log_path, pattern, tail_lines=200, max_matches=50):
    """扫描日志尾部匹配正则，返回命中的最近若干行（D2 日志异常检测）。

    Args:
        log_path: 日志文件绝对路径
        pattern: 正则表达式（如 'OOM|Out of memory'）
        tail_lines: 扫描文件尾部行数
        max_matches: 最多返回命中行数
    Returns:
        list[str]: 命中的原始日志行（从旧到新）
    """
    import re as _re
    try:
        with open(log_path, 'r', errors='replace') as f:
            lines = f.readlines()[-tail_lines:]
    except Exception:
        return []
    try:
        rx = _re.compile(pattern)
    except Exception:
        return []
    hits = [ln.rstrip('\n') for ln in lines if rx.search(ln)]
    return hits[-max_matches:]


def check_backup_path(target_path, min_size_bytes=0):
    """检查备份文件/目录的新鲜度与大小（D3 备份状态验证）。

    Returns:
        dict: {path, exists, age_hours, size_bytes, error}
    """
    import os
    try:
        st = os.stat(target_path)
    except OSError as e:
        return {'path': target_path, 'exists': False, 'age_hours': None,
                'size_bytes': 0, 'error': str(e)[:200]}
    age_hours = None
    import time
    if st.st_mtime:
        age_hours = round((time.time() - st.st_mtime) / 3600, 2)
    return {'path': target_path, 'exists': True, 'age_hours': age_hours,
            'size_bytes': st.st_size, 'error': None}

def collect_images(with_registry=False):
    """采集运行容器镜像信息（D4 镜像更新检测）。

    默认本地模式（docker ps + inspect，<1s，始终可用）；
    with_registry=True 时额外对比 Docker Hub 远端 digest
    （强制 TLS1.2 + 短超时，registry 不可达跳过不阻塞，outdated=False）。
    """
    import subprocess, json, time
    t0 = time.time()
    try:
        out = subprocess.run(['docker', 'ps', '--format', '{{.Names}}|{{.Image}}'],
                             capture_output=True, text=True, timeout=8)
        lines = [l for l in out.stdout.strip().split('\n') if l]
    except Exception:
        return []
    result = []
    budget = 8.0 if with_registry else 0.0
    for line in lines:
        if with_registry and time.time() - t0 > budget:
            break
        parts = line.split('|')
        if len(parts) != 2:
            continue
        cname, image = parts[0], parts[1]
        local_digest = None
        try:
            r = subprocess.run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'],
                               capture_output=True, text=True, timeout=5)
            local_digest = r.stdout.strip() or None
        except Exception:
            pass
        remote_digest = None
        outdated = False
        if with_registry:
            try:
                import ssl as _ssl
                import urllib.request
                _ctx = _ssl.create_default_context()
                _ctx.maximum_version = _ssl.TLSVersion.TLSv1_2
                repo = image.split(':')[0]
                if '/' not in repo:
                    repo = 'library/' + repo
                token_url = f'https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull'
                with urllib.request.urlopen(token_url, timeout=2, context=_ctx) as resp:
                    token = json.loads(resp.read()).get('token', '')
                req = urllib.request.Request(
                    f'https://registry-1.docker.io/v2/{repo}/manifests/latest',
                    headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.docker.distribution.manifest.v2+json'})
                with urllib.request.urlopen(req, timeout=2, context=_ctx) as resp:
                    remote_digest = resp.headers.get('Docker-Content-Digest')
                if local_digest and remote_digest:
                    outdated = local_digest.split(':')[-1][:12] != remote_digest.split(':')[-1][:12]
            except Exception:
                pass
        result.append({
            'container_name': cname, 'image': image,
            'local_digest': local_digest, 'remote_digest': remote_digest,
            'outdated': outdated,
        })
    return result
