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

AGENT_VERSION = "2.1.0"
VERSION = AGENT_VERSION
TOKEN = ""

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scanner

# === Network monitoring state (v3.25) ===
_network_last = {}      # iface -> {'rx':int,'tx':int,'ts':float}
_network_daily = {}     # iface -> {'rx':int,'tx':int,'date':str}
_latency_cache = {}     # target -> {'latency_ms':float,'loss_pct':float,'jitter_ms':float}
_latency_targets = ['8.8.8.8', '1.1.1.1']


def _collect_network_snapshot():
    """计算每网卡速率(Mbps)与今日累计字节，供 /metrics 上报。"""
    global _network_last, _network_daily
    now = time.time()
    today = time.strftime('%Y-%m-%d')
    snap = {}
    cur = scanner.collect_network()
    for iface, d in cur.items():
        last = _network_last.get(iface)
        rx_rate = tx_rate = 0.0
        if last:
            dt = now - last['ts']
            if dt > 0:
                rx_rate = max(0.0, (d['rx_bytes'] - last['rx']) / dt * 8 / 1e6)
                tx_rate = max(0.0, (d['tx_bytes'] - last['tx']) / dt * 8 / 1e6)
        _network_last[iface] = {'rx': d['rx_bytes'], 'tx': d['tx_bytes'], 'ts': now}
        day = _network_daily.get(iface)
        if not day or day['date'] != today:
            day = {'rx': 0, 'tx': 0, 'date': today}
        if last:
            day['rx'] += max(0, d['rx_bytes'] - last['rx'])
            day['tx'] += max(0, d['tx_bytes'] - last['tx'])
        _network_daily[iface] = day
        snap[iface] = {
            'rx_bytes': d['rx_bytes'], 'tx_bytes': d['tx_bytes'],
            'rx_packets': d['rx_packets'], 'tx_packets': d['tx_packets'],
            'rx_errors': d['rx_errors'], 'tx_errors': d['tx_errors'],
            'rx_rate_mbps': round(rx_rate, 3), 'tx_rate_mbps': round(tx_rate, 3),
            'daily_rx_bytes': day['rx'], 'daily_tx_bytes': day['tx'],
        }
    return snap


def _background_latency_loop():
    """后台线程：周期性 ping 目标，缓存延迟/丢包。"""
    global _latency_cache
    while True:
        for tgt in _latency_targets:
            try:
                _latency_cache[tgt] = scanner.ping_latency(tgt)
            except Exception:
                pass
        time.sleep(60)



# === Scan cache and background thread ===

_scan_cache = None
_scan_lock = threading.Lock()
_scan_interval = 300


def scan_all():
    """Run all scanners via scanner module and return combined result."""
    return scanner.scan_all()


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
    result = scan_all()
    with _scan_lock:
        _scan_cache = result
    return result


# === Metrics collection ===

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

    # --- Network per-interface (v3.25) ---
    try:
        m['network'] = _collect_network_snapshot()
    except Exception:
        m['network'] = {}
    m['network_daily'] = {k: {'rx_bytes': v['rx'], 'tx_bytes': v['tx']} for k, v in _network_daily.items()}
    m['latency'] = dict(_latency_cache)

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
    m['agent_version'] = AGENT_VERSION
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
        path = self.path.split('?')[0]

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

    scan_thread = threading.Thread(target=_background_scan_loop, daemon=True)
    scan_thread.start()

    latency_thread = threading.Thread(target=_background_latency_loop, daemon=True)
    latency_thread.start()
    print("[scanner] Background latency probe started", flush=True)

    print(f"[scanner] Background scan started (interval={_scan_interval}s)", flush=True)

    print("[scanner] Running initial scan...", flush=True)
    result = scan_all()
    stopped = len(result.get('stopped_containers', []))
    nginx = len(result.get('nginx_services', []))
    print(f"[scanner] Initial scan done: {len(result['containers'])} containers, "
          f"{stopped} stopped, "
          f"{len(result['ports'])} ports, {len(result['systemd_services'])} systemd services, "
          f"{nginx} nginx services "
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
