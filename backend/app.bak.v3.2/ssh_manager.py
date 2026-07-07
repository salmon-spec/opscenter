"""SSH connection manager for remote server discovery and monitoring."""
import paramiko
import json
import re as _re
from typing import Optional, Tuple, List, Dict
from app.models import Server


def get_ssh_client(server: Server, password: str = None) -> Optional[paramiko.SSHClient]:
    """Create and return an SSH client connected to the server."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if password:
            client.connect(
                hostname=server.host, port=server.ssh_port or 22,
                username=server.ssh_user or 'root', password=password,
                timeout=10, allow_agent=False, look_for_keys=False,
            )
        elif server.ssh_key:
            import io
            if server.ssh_key.startswith("__password__"):
                pw = server.ssh_key[len("__password__"):]
                client.connect(
                    hostname=server.host, port=server.ssh_port or 22,
                    username=server.ssh_user or 'root', password=pw,
                    timeout=10, allow_agent=False, look_for_keys=False,
                )
            else:
                key_file = io.StringIO(server.ssh_key)
                pkey = paramiko.Ed25519Key.from_private_key(key_file)
                client.connect(
                    hostname=server.host, port=server.ssh_port or 22,
                    username=server.ssh_user or 'root', pkey=pkey,
                    timeout=10, allow_agent=False, look_for_keys=False,
                )
        else:
            return None
        return client
    except Exception as e:
        print(f"SSH connect error for {server.host}: {e}")
        try: client.close()
        except: pass
        return None


def ssh_exec(client: paramiko.SSHClient, command: str, timeout: int = 15) -> Tuple[str, str, int]:
    """Execute a command over SSH and return (stdout, stderr, exit_code)."""
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode('utf-8', errors='replace')
        err = stderr.read().decode('utf-8', errors='replace')
        return out, err, exit_code
    except Exception as e:
        return '', str(e), -1


# Mall-Swarm container -> URL mapping
MALL_SWARM_URLS = {
    'nginx': '/',
    'mall-admin-web': '/',
    'mall-app-web': '/mall/',
    'mall-portal': '/portal/',
    'mall-admin': '/admin/',
    'mall-search': '/search/',
    'mall-gateway': '/gateway/',
    'mall-auth': '/auth/',
    'mall-monitor': '/monitor/',
    'nacos-registry': '/nacos/',
    'rabbitmq': '/rabbitmq/',
    'kibana': '/kibana/',
    'elasticsearch': '',  # Usually no web UI
    'mysql': None,  # No web UI
    'redis': None,  # No web UI
    'mongo': None,  # No web UI
}


def discover_remote_docker_services(client: paramiko.SSHClient, host: str = '') -> List[Dict]:
    """Discover Docker services on a remote server via SSH."""
    cmd = 'docker ps --format "{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}"'
    out, err, code = ssh_exec(client, cmd)
    if code != 0 or not out.strip():
        return []

    containers = []
    for line in out.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('|', 3)
        name = parts[0].strip() if len(parts) > 0 else ''
        image = parts[1].strip() if len(parts) > 1 else ''
        status_str = parts[2].strip() if len(parts) > 2 else ''
        ports = parts[3].strip() if len(parts) > 3 else ''
        is_running = 'Up' in status_str

        # Generate URL
        url = ''
        if name in MALL_SWARM_URLS:
            url = MALL_SWARM_URLS[name]
            if url is None:
                url = ''
            elif url and host:
                url = f'http://{host}{url}'
        elif name.startswith('mall-'):
            if host:
                url = f'http://{host}'
        else:
            # Extract public port from ports string
            port_matches = _re.findall(r'0\.0\.0\.0:(\d+)->', ports)
            if port_matches and host:
                url = f'http://{host}:{port_matches[0]}'

        containers.append({
            'name': name,
            'image': image,
            'status': status_str,
            'ports': ports,
            'is_running': is_running,
            'auto_url': url,
        })
    return containers


def collect_remote_metrics(client: paramiko.SSHClient) -> Dict:
    """Collect system metrics from a remote server via SSH."""
    metrics = {}

    out, _, _ = ssh_exec(client, "grep 'cpu ' /proc/stat | awk '{usage=($2+$4)*100/($2+$4+$5)} END {print usage}'")
    try: metrics['cpu_percent'] = round(float(out.strip()), 1)
    except: metrics['cpu_percent'] = 0

    out, _, _ = ssh_exec(client, "nproc")
    try: metrics['cpu_count'] = int(out.strip())
    except: metrics['cpu_count'] = 0

    out, _, _ = ssh_exec(client, "free -b | grep Mem")
    try:
        parts = out.strip().split()
        total = int(parts[1])
        available = int(parts[6]) if len(parts) > 6 else int(parts[3])
        metrics['memory_total'] = total
        metrics['memory_used'] = total - available
        metrics['memory_percent'] = round((total - available) / total * 100, 1) if total > 0 else 0
    except:
        metrics['memory_total'] = 0; metrics['memory_used'] = 0; metrics['memory_percent'] = 0

    out, _, _ = ssh_exec(client, "df -B1 / | tail -1")
    try:
        parts = out.strip().split()
        total = int(parts[1]); used = int(parts[2]); avail = int(parts[3])
        metrics['disk_total'] = total; metrics['disk_used'] = used; metrics['disk_avail'] = avail
        metrics['disk_percent'] = round(used / total * 100, 1) if total > 0 else 0
    except:
        metrics['disk_total'] = 0; metrics['disk_used'] = 0; metrics['disk_avail'] = 0; metrics['disk_percent'] = 0

    out, _, _ = ssh_exec(client, "cat /proc/loadavg")
    try:
        parts = out.strip().split()
        metrics['load1'] = float(parts[0]); metrics['load5'] = float(parts[1]); metrics['load15'] = float(parts[2])
    except:
        metrics['load1'] = 0; metrics['load5'] = 0; metrics['load15'] = 0

    out, _, _ = ssh_exec(client, "cat /proc/net/dev | grep -E 'eth|ens|enp' | head -1")
    try:
        parts = out.strip().split()
        rx_idx = parts.index(':') + 1 if ':' in parts else 1
        metrics['net_rx_bytes'] = int(parts[rx_idx])
        metrics['net_tx_bytes'] = int(parts[rx_idx + 8])
    except:
        metrics['net_rx_bytes'] = 0; metrics['net_tx_bytes'] = 0

    out, _, _ = ssh_exec(client, "docker ps -q | wc -l")
    try: metrics['containers'] = int(out.strip())
    except: metrics['containers'] = 0

    return metrics


def get_remote_containers(client: paramiko.SSHClient) -> List[Dict]:
    """Get container list from remote server."""
    cmd = 'docker ps -a --format "{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}"'
    out, err, code = ssh_exec(client, cmd)
    if code != 0:
        return []
    containers = []
    for line in out.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('|', 3)
        status_str = parts[2].strip() if len(parts) > 2 else 'unknown'
        containers.append({
            'name': parts[0].strip() if len(parts) > 0 else '',
            'image': parts[1].strip() if len(parts) > 1 else '',
            'status': 'running' if 'Up' in status_str else 'exited',
            'ports': parts[3].strip() if len(parts) > 3 else '',
        })
    return containers


def test_ssh_connection(host: str, port: int, username: str, password: str = None, ssh_key: str = None) -> Tuple[bool, str]:
    """Test SSH connection and return (success, message)."""
    import io
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if password:
            client.connect(hostname=host, port=port, username=username, password=password, timeout=10, allow_agent=False, look_for_keys=False)
        elif ssh_key:
            key_file = io.StringIO(ssh_key)
            try: pkey = paramiko.Ed25519Key.from_private_key(key_file)
            except:
                key_file.seek(0)
                pkey = paramiko.RSAKey.from_private_key(key_file)
            client.connect(hostname=host, port=port, username=username, pkey=pkey, timeout=10, allow_agent=False, look_for_keys=False)
        else:
            return False, "No password or SSH key provided"
        _, out, _ = ssh_exec(client, 'echo OK')
        client.close()
        if out.strip() == 'OK':
            return True, "Connection successful"
        return True, "Connected but command execution failed"
    except paramiko.AuthenticationException:
        return False, "Authentication failed"
    except paramiko.SSHException as e:
        return False, f"SSH error: {e}"
    except Exception as e:
        return False, f"Connection failed: {e}"
    finally:
        try: client.close()
        except: pass
