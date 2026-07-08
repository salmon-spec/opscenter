#!/usr/bin/env python3
"""OpsCenter Agent - Lightweight monitoring agent for remote servers.
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

VERSION = "1.0.0"
TOKEN = ""


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

    # --- Containers (Docker) ---
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


class MetricsHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that serves /metrics endpoint."""

    def do_GET(self):
        if self.path == '/metrics':
            # Token auth
            if TOKEN:
                auth = self.headers.get('Authorization', '')
                if auth != f'Bearer {TOKEN}':
                    self.send_response(401)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "unauthorized"}).encode())
                    return
            data = collect_metrics()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        elif self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "version": VERSION}).encode())
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
    args = parser.parse_args()

    global TOKEN
    TOKEN = args.token

    server = http.server.HTTPServer((args.bind, args.port), MetricsHandler)
    print(f"OpsAgent v{VERSION} listening on {args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)
        server.server_close()


if __name__ == '__main__':
    main()
