#!/usr/bin/env python3
"""OpsAgent Scanner - Service discovery for remote servers.

Scans Docker containers, listening ports, and systemd services.
Pure stdlib, no external dependencies.
"""

import json
import subprocess
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
    by finding the container PID and filtering `ss -tlnp` by that PID
    (and its children). Avoids including all host ports since host net
    containers share the host network namespace.
    """
    ports = []
    try:
        pid_str = run_cmd(['docker', 'inspect', '--format', '{{.State.Pid}}',
                           container_id[:12] if container_id else container_name])
        if not pid_str or pid_str == '0':
            return ports
        pid = pid_str.strip()

        # Collect container PID + child PIDs
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

        # Parse ss -tlnp output, keep only entries matching our PIDs
        ss_out = run_cmd(['ss', '-tlnp'], timeout=5)
        if not ss_out:
            return ports
        for line in ss_out.split('\n')[1:]:
            parts = line.split()
            if len(parts) < 5:
                continue
            local_addr = parts[3] if len(parts) > 3 else parts[-2]
            process_info = ' '.join(parts[4:]) if len(parts) > 4 else ''

            # Match PIDs
            matched = False
            for cpid in container_pids:
                if f'pid={cpid},' in process_info or f'pid={cpid})' in process_info:
                    matched = True
                    break
            if not matched:
                continue

            # Parse address
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

        # Parse ports: "0.0.0.0:80->80/tcp, :::443->443/tcp" or ""
        ports = []
        if ports_raw:
            for p in ports_raw.split(', '):
                p = p.strip()
                if '->' in p:
                    # Extract host port and container port
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

        # Parse networks
        networks = [n.strip() for n in networks_raw.split(',') if n.strip()] if networks_raw else []

        # Detect host network mode and supplement ports for host-net containers
        is_host_network = 'host' in networks
        if is_host_network and not ports:
            host_ports = _get_host_net_ports(name, container_id)
            if host_ports:
                ports = host_ports
                # Rebuild port_summary for host network containers
                port_summaries = []
                for hp in host_ports:
                    port_summaries.append(f"{hp.get('bind_ip','0.0.0.0')}:{hp['host_port']}->{hp['container_port']}/{hp['proto']}")
                ports_raw = ', '.join(port_summaries)

        # Extract labels for service hints
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


def scan_listening_ports():
    """Scan listening TCP/UDP ports via `ss -tlnpu`."""
    ports = []
    output = run_cmd(['ss', '-tlnpu'], timeout=3)
    if not output:
        return ports
    for line in output.split('\n')[1:]:  # skip header
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0].lower()  # tcp/udp/tcp6/udp6
        state = parts[1] if len(parts) > 1 else ''
        local_addr = parts[4] if len(parts) > 4 else ''

        # Parse local address: 0.0.0.0:80 or [::]:443 or 127.0.0.1:9090
        bind_ip = ''
        port = ''
        if ':' in local_addr:
            # IPv6: [::]:443
            if local_addr.startswith('['):
                bracket_end = local_addr.index(']')
                bind_ip = local_addr[1:bracket_end]
                port = local_addr[bracket_end + 2:]  # skip ]:
            else:
                bind_ip = local_addr.rsplit(':', 1)[0]
                port = local_addr.rsplit(':', 1)[-1]

        # Get process info from last column
        process_info = ' '.join(parts[5:]) if len(parts) > 5 else ''
        process_name = ''
        pid = ''
        if 'users:((' in process_info:
            # users:(("nginx",pid=1234,fd=6))
            try:
                info_part = process_info.split('users:((')[1].rstrip('))')
                # Parse name and pid
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

        ports.append({
            'port': port_int,
            'proto': proto.replace('6', ''),  # tcp6 -> tcp
            'bind_ip': bind_ip,
            'state': state,
            'process': process_name,
            'pid': pid,
        })

    # Deduplicate by (port, proto, process)
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
        name = parts[0]  # e.g. nginx.service
        description = ' '.join(parts[4:]) if len(parts) > 4 else ''
        services.append({
            'name': name,
            'status': 'active',
            'description': description,
        })
    return services


def scan_all():
    """Run all scanners and return combined result."""
    now = time.time()
    return {
        'containers': scan_docker_containers(),
        'ports': scan_listening_ports(),
        'systemd_services': scan_systemd_services(),
        'scanned_at': now,
        'scan_duration_ms': int((time.time() - now) * 1000),
    }


# === Incremental diff ===

def diff_scans(old_result, new_result):
    """Compare two scan results, return changes."""
    changes = {'added': [], 'removed': [], 'changed': []}

    # Compare containers by name
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

    # Compare ports by (port, proto)
    old_ports = {(p['port'], p['proto']): p for p in old_result.get('ports', [])}
    new_ports = {(p['port'], p['proto']): p for p in new_result.get('ports', [])}

    for key in new_ports:
        if key not in old_ports:
            changes['added'].append({'type': 'port', 'name': f"{key[0]}/{key[1]}", 'data': new_ports[key]})
    for key in old_ports:
        if key not in new_ports:
            changes['removed'].append({'type': 'port', 'name': f"{key[0]}/{key[1]}", 'data': old_ports[key]})

    # Compare systemd by name
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
    print(f"  Ports: {len(result['ports'])}")
    print(f"  Systemd services: {len(result['systemd_services'])}")
    print()
    pprint.pprint(result, width=120)
