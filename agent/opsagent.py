#!/usr/bin/env python3
"""OpsCenter Agent v2.1.0 - Lightweight monitoring + service scanning agent.
Run as systemd service or standalone: python3 opsagent.py [--port 19100] [--token TOKEN]
"""
import http.server
import json
import os
import platform
import subprocess
import sys
import time
import argparse
import threading

VERSION = "2.1.0"
TOKEN = ""

# === Scanner module (inline, no external deps) ===

def _run_cmd(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           shell=isinstance(cmd, str))
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ''


def _scan_docker_containers():
    containers = []
    output = _run_cmd(['docker', 'ps', '--format',
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

        running = 'Up' in status_raw
        containers.append({
            'name': name,
            'image': image,
            'status': 'running' if running else 'exited',
            'ports': ports,
            'port_summary': ports_raw,
            'networks': networks,
            'container_id': container_id[:12] if container_id else '',
        })
    return containers


def _scan_listening_ports():
    ports = []
    output = _run_cmd(['ss', '-tlnpu'], timeout=3)
    if not output:
        return ports
    for line in output.split('\n')[1:]:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0].lower()
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
        ports.append({
            'port': port_int,
            'proto': proto.replace('6', ''),
            'bind_ip': bind_ip,
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


def _scan_systemd_services():
    services = []
    output = _run_cmd(['systemctl', 'list-units', '--type=service', '--state=running',
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
        description = ' '.join(parts[4:]) if len(parts) > 4 else ''
        services.append({
            'name': name,
            'status': 'active',
            'description': description,
        })
    return services


# === Scan cache and background thread ===

_scan_cache = None
_scan_lock = threading.Lock()
_scan_interval = 300  # 5 minutes


def scan_all():
    """Run all scanners and return combined result."""
    t0 = time.time()
    result = {
        'containers': _scan_docker_containers(),
        'ports': _scan_listening_ports(),
        'systemd_services': _scan_systemd_services(),
        'scanned_at': t0,
    }
    result['scan_duration_ms'] = int((time.time() - t0) * 1000)
    return result


def _background_scan_loop():
    """Background thread: periodically scan and cache results."""
    global _scan_cache
    while True:
        try:
            result = scan_all()
            with _scan_lock:
                _scan_cache = result
        except Exception as e:
            print(f"[scanner] scan error: {e}", flush=True)
        time.sleep(_scan_interval)


def get_cached_scan():
    """Return cached scan result, or run a fresh scan if none yet."""
    global _scan_cache
    with _scan_lock:
        if _scan_cache is not None:
            return _scan_cache
    # No cache yet, do a fresh scan
    result = scan_all()
    with _scan_lock:
        _scan_cache = result
    return result


# === Metrics collection (unchanged) ===

def collect_metrics():
    """Collect system metrics from /proc filesystem."""
    m = {}

    # --- CPU ---
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        parts = line.split()
        idle = int(parts[4])
        total = sum(int(x) for x in parts[1:])
        time.sleep(0.1)
        with open('/proc/stat') as f:
            line2 = f.readline()
        parts2 = line2.split()
        idle2 = int(parts2[4])
        total2 = sum(int(x) for x in parts2[1:])
        diff_idle = idle2 - idle
        diff_total = total2 - total
        m['cpu_percent'] = round((1 - diff_idle / diff_total) * 100, 1) if diff_total > 0 else 0
    except Exception:
        m['cpu_percent'] = 0

    try:
        with open('/proc/cpuinfo') as f:
            m['cpu_count'] = sum(1 for line in f if line.startswith('processor'))
    except Exception:
        m['cpu_count'] = 0

    # --- Memory ---
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                parts = line.split()
                info[parts[0].rstrip(':')] = int(parts[1]) * 1024
        total = info.get('MemTotal', 0)
        available = info.get('MemAvailable', 0)
        used = total - available
        m['memory_total'] = total
        m['memory_used'] = used
        m['memory_available'] = available
        m['memory_percent'] = round(used / total * 100, 1) if total > 0 else 0
    except Exception:
        m['memory_total'] = m['memory_used'] = m['memory_available'] = 0
        m['memory_percent'] = 0

    # --- Disk ---
    try:
        st = os.statvfs('/')
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        m['disk_total'] = total
        m['disk_used'] = used
        m['disk_avail'] = free
        m['disk_percent'] = round(used / total * 100, 1) if total > 0 else 0
    except Exception:
        m['disk_total'] = m['disk_used'] = m['disk_avail'] = 0
        m['disk_percent'] = 0

    # --- Disk IO ---
    try:
        with open('/proc/diskstats') as f:
            for line in f:
                parts = line.split()
                if len(parts) > 10 and (parts[2].startswith('vd') or parts[2].startswith('sd')):
                    m['disk_read_bytes'] = int(parts[5]) * 512
                    m['disk_write_bytes'] = int(parts[9]) * 512
                    break
    except Exception:
        m['disk_read_bytes'] = m['disk_write_bytes'] = 0

    # --- Load ---
    try:
        with open('/proc/loadavg') as f:
            parts = f.read().split()
            m['load1'] = float(parts[0])
            m['load5'] = float(parts[1])
            m['load15'] = float(parts[2])
    except Exception:
        m['load1'] = m['load5'] = m['load15'] = 0

    # --- Network ---
    try:
        with open('/proc/net/dev') as f:
            for line in f:
                if ':' in line and not line.strip().startswith('lo:'):
                    iface, data = line.split(':')
                    iface = iface.strip()
                    if iface.startswith(('eth', 'ens', 'enp', 'wlan', 'en')):
                        parts = data.split()
                        m['net_rx_bytes'] = int(parts[0])
                        m['net_tx_bytes'] = int(parts[8])
                        m['net_iface'] = iface
                        break
    except Exception:
        m['net_rx_bytes'] = m['net_tx_bytes'] = 0
        m['net_iface'] = ''

    # --- Uptime ---
    try:
        with open('/proc/uptime') as f:
            m['uptime'] = float(f.read().split()[0])
    except Exception:
        m['uptime'] = 0

    # --- Containers (Docker) - lightweight count from metrics ---
    m['container_running'] = 0
    m['container_stopped'] = 0
    m['containers'] = []
    try:
        result = subprocess.run(
            ['docker', 'ps', '-a', '--format', '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = line.split('|', 3)
                name = parts[0].strip() if len(parts) > 0 else ''
                image = parts[1].strip() if len(parts) > 1 else ''
                status = parts[2].strip() if len(parts) > 2 else ''
                ports = parts[3].strip() if len(parts) > 3 else ''
                running = 'Up' in status
                if running:
                    m['container_running'] += 1
                else:
                    m['container_stopped'] += 1
                m['containers'].append({
                    'name': name, 'image': image,
                    'status': 'running' if running else 'exited', 'ports': ports,
                })
    except Exception:
        pass

    # --- Host info ---
    m['hostname'] = platform.node()
    m['platform'] = platform.platform()
    m['kernel'] = platform.release()
    m['agent_version'] = VERSION
    m['timestamp'] = time.time()

    return m


# === HTTP Handler ===

class AgentHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler serving metrics and service scan endpoints."""

    def _check_auth(self):
        if not TOKEN:
            return True
        auth = self.headers.get('Authorization', '')
        if auth == f'Bearer {TOKEN}':
            return True
        self.send_response(401)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"error": "unauthorized"}).encode())
        return False

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_GET(self):
        path = self.path.split('?')[0]  # strip query string

        if path == '/metrics':
            if not self._check_auth():
                return
            data = collect_metrics()
            self._json_response(data)

        elif path == '/health':
            self._json_response({"status": "ok", "version": VERSION})

        elif path == '/api/v1/services':
            if not self._check_auth():
                return
            # Return cached scan result
            data = get_cached_scan()
            self._json_response(data)

        elif path == '/api/v1/containers':
            if not self._check_auth():
                return
            data = get_cached_scan()
            self._json_response({
                'containers': data.get('containers', []),
                'scanned_at': data.get('scanned_at'),
            })

        elif path == '/api/v1/ports':
            if not self._check_auth():
                return
            data = get_cached_scan()
            self._json_response({
                'ports': data.get('ports', []),
                'scanned_at': data.get('scanned_at'),
            })

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global _scan_cache
        path = self.path.split('?')[0]

        if path == '/api/v1/scan':
            if not self._check_auth():
                return
            # Trigger immediate scan
            result = scan_all()
            with _scan_lock:
                _scan_cache = result
            self._json_response(result)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description='OpsCenter Agent')
    parser.add_argument('--port', type=int, default=19100, help='HTTP listen port (default: 19100)')
    parser.add_argument('--token', type=str, default='', help='Auth token (optional)')
    parser.add_argument('--bind', type=str, default='0.0.0.0', help='Bind address (default: 0.0.0.0)')
    parser.add_argument('--scan-interval', type=int, default=300,
                        help='Background scan interval in seconds (default: 300)')
    args = parser.parse_args()

    global TOKEN, _scan_interval
    TOKEN = args.token
    _scan_interval = args.scan_interval

    # Start background scan thread
    scan_thread = threading.Thread(target=_background_scan_loop, daemon=True)
    scan_thread.start()
    print(f"[scanner] Background scan started (interval={_scan_interval}s)", flush=True)

    # Do initial scan
    print("[scanner] Running initial scan...", flush=True)
    result = scan_all()
    print(f"[scanner] Initial scan done: {len(result['containers'])} containers, "
          f"{len(result['ports'])} ports, {len(result['systemd_services'])} systemd services "
          f"({result['scan_duration_ms']}ms)", flush=True)

    server = http.server.HTTPServer((args.bind, args.port), AgentHandler)
    print(f"OpsAgent v{VERSION} listening on {args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)
        server.server_close()


if __name__ == '__main__':
    main()
