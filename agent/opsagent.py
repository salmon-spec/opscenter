#!/usr/bin/env python3
"""OpsCenter Agent v2.6.0 - Lightweight monitoring + service scanning agent.
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
import re
import base64
import hashlib
from datetime import datetime

AGENT_VERSION = "2.6.0"
VERSION = AGENT_VERSION
TOKEN = ""

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scanner


# === Network monitoring state (v3.25.1) ===
_net_last = {}    # iface -> {rx, tx, ts}
_net_daily = {}   # iface -> {rx, tx, date}
_net_lock = threading.Lock()


def _network_snapshot():
    """计算各网卡实时速率(Mbps)与今日累计字节，供 /metrics 与 /api/v1/network 使用。"""
    global _net_last, _net_daily
    now = time.time()
    today = time.strftime('%Y-%m-%d')
    cur = scanner.collect_network()
    snap = {}
    with _net_lock:
        for iface, d in cur.items():
            last = _net_last.get(iface)
            rx_rate = tx_rate = 0.0
            if last:
                dt = now - last['ts']
                if dt > 0:
                    rx_rate = max(0.0, (d['rx_bytes'] - last['rx']) / dt * 8 / 1e6)
                    tx_rate = max(0.0, (d['tx_bytes'] - last['tx']) / dt * 8 / 1e6)
            _net_last[iface] = {'rx': d['rx_bytes'], 'tx': d['tx_bytes'], 'ts': now}
            day = _net_daily.get(iface)
            if not day or day['date'] != today:
                day = {'rx': 0, 'tx': 0, 'date': today}
            if last:
                day['rx'] += max(0, d['rx_bytes'] - last['rx'])
                day['tx'] += max(0, d['tx_bytes'] - last['tx'])
            _net_daily[iface] = day
            snap[iface] = {
                'rx_rate_mbps': round(rx_rate, 3),
                'tx_rate_mbps': round(tx_rate, 3),
                'rx_bytes': d['rx_bytes'],
                'tx_bytes': d['tx_bytes'],
                'rx_errors': d['rx_errors'],
                'tx_errors': d['tx_errors'],
                'daily_rx_bytes': day['rx'],
                'daily_tx_bytes': day['tx'],
            }
    return snap


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
            print(f"[scanner] Background scan done: {len(result.get('containers', []))} containers, "
                  f"{len(result.get('ports', []))} ports, {len(result.get('pve_services', []))} PVE guest services "
                  f"({result.get('scan_duration_ms', 0)}ms)", flush=True)
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

_PSEUDO_FS = {
    'proc', 'sysfs', 'devtmpfs', 'devpts', 'tmpfs', 'cgroup', 'cgroup2',
    'overlay', 'squashfs', 'securityfs', 'pstore', 'debugfs', 'tracefs',
    'configfs', 'fusectl', 'mqueue', 'hugetlbfs', 'autofs', 'rpc_pipefs',
}


def _collect_mounts():
    """Return real mounted filesystems without pseudo/duplicate mount entries."""
    mounts = []
    seen = set()
    try:
        with open('/proc/mounts') as f:
            rows = f.readlines()
        for row in rows:
            parts = row.split()
            if len(parts) < 3:
                continue
            device, mountpoint, fstype = parts[:3]
            mountpoint = mountpoint.replace('\\040', ' ')
            if fstype in _PSEUDO_FS or mountpoint in seen or not mountpoint.startswith('/'):
                continue
            try:
                st = os.statvfs(mountpoint)
                total = st.f_blocks * st.f_frsize
                avail = st.f_bavail * st.f_frsize
                if total <= 0:
                    continue
                used = total - avail
                mounts.append({
                    'device': device, 'mountpoint': mountpoint, 'fstype': fstype,
                    'total': total, 'used': used, 'avail': avail,
                    'percent': round(used / total * 100, 1),
                })
                seen.add(mountpoint)
            except (OSError, PermissionError):
                continue
    except Exception:
        pass
    return sorted(mounts, key=lambda item: item['mountpoint'])


def _collect_disk_devices():
    """Return cumulative IO counters for physical/virtual base block devices."""
    devices = []
    try:
        with open('/proc/diskstats') as f:
            for line in f:
                parts = line.split()
                if len(parts) < 14:
                    continue
                name = parts[2]
                is_base = (
                    re.match(r'^(sd|vd|xvd)[a-z]+$', name)
                    or re.match(r'^nvme\d+n\d+$', name)
                    or re.match(r'^mmcblk\d+$', name)
                )
                if not is_base:
                    continue
                devices.append({
                    'device': name,
                    'read_ops': int(parts[3]),
                    'read_bytes': int(parts[5]) * 512,
                    'write_ops': int(parts[7]),
                    'write_bytes': int(parts[9]) * 512,
                    'io_ms': int(parts[12]),
                })
    except Exception:
        pass
    return devices


def _collect_processes(sort_key, limit=10, search='', user='', state=''):
    """Collect top processes by CPU or resident memory using procps."""
    sort_arg = '-%cpu' if sort_key == 'cpu' else '-rss'
    try:
        result = subprocess.run(
            ['ps', '-eo', 'pid=,ppid=,user=,comm=,%cpu=,%mem=,rss=,stat=', '--sort=' + sort_arg],
            capture_output=True, text=True, timeout=3,
        )
        rows = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 7)
            if len(parts) != 8:
                continue
            if parts[3] == 'ps':
                continue
            row = {
                'pid': int(parts[0]), 'ppid': int(parts[1]), 'user': parts[2],
                'command': parts[3], 'cpu_percent': float(parts[4]),
                'memory_percent': float(parts[5]), 'rss_bytes': int(parts[6]) * 1024,
                'state': parts[7],
            }
            if search and search.lower() not in f"{row['pid']} {row['command']} {row['user']}".lower():
                continue
            if user and row['user'] != user:
                continue
            if state and not row['state'].startswith(state):
                continue
            rows.append(row)
            if len(rows) >= max(1, min(int(limit), 500)):
                break
        return rows
    except Exception:
        return []


def _collect_container_stats():
    """Collect a bounded Docker resource snapshot. Failure is non-fatal."""
    try:
        result = subprocess.run(
            ['docker', 'stats', '--no-stream', '--format', '{{json .}}'],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode != 0:
            return []
        rows = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            rows.append({
                'name': raw.get('Name', ''), 'container': raw.get('Container', ''),
                'cpu_percent': raw.get('CPUPerc', '0%'),
                'memory_usage': raw.get('MemUsage', ''),
                'memory_percent': raw.get('MemPerc', '0%'),
                'network_io': raw.get('NetIO', ''), 'block_io': raw.get('BlockIO', ''),
                'pids': raw.get('PIDs', '0'),
            })
        return rows[:50]
    except Exception:
        return []

def _collect_wireguard():
    """v2.6: 只读采集 WireGuard 状态（GET /api/v1/wireguard）。

    安全约束：
    - 只解析稳定的机器格式 `wg show all dump`，不解析面向人的本地化文本。
    - 接口行第 2 列是接口私钥、Peer 行第 2 列是预共享密钥，解析时立即丢弃，
      绝不能进入返回对象、日志或异常文本。
    - 公钥只返回不可逆 SHA-256 短指纹，不返回完整公钥。
    - 未安装 wg / 权限不足 / 超时 / 无接口时返回 supported:false 与结构化原因，不抛 500。

    接口 dump 行：interface <ifname> <private_key> <listen_port> <fwmark>
    Peer dump 行：peer <pubkey> <preshared_key> <endpoint> <allowed_ips> <latest_handshake> <rx> <tx> <keepalive>
    """
    result = {"supported": False, "generated_at": None, "interfaces": [], "reason": None}
    try:
        proc = subprocess.run(["wg", "show", "all", "dump"], capture_output=True, text=True, timeout=3)
    except FileNotFoundError:
        result["reason"] = "wg 命令未安装"
        return result
    except subprocess.TimeoutExpired:
        result["reason"] = "wg 命令超时"
        return result
    except Exception as e:
        result["reason"] = f"执行 wg 失败: {type(e).__name__}"
        return result
    if proc.returncode != 0:
        result["reason"] = (proc.stderr or "wg 命令执行失败").strip()[:200] or "wg 命令执行失败"
        return result
    result["supported"] = True
    result["generated_at"] = datetime.utcnow().isoformat() + "Z"
    result["interfaces"] = _parse_wg_dump(proc.stdout)
    # 地址读取失败不影响接口与 Peer 列表返回（/proc 数据不可用时静默跳过）
    try:
        addr_proc = subprocess.run(["ip", "-j", "addr", "show"], capture_output=True, text=True, timeout=3)
        if addr_proc.returncode == 0:
            iface_map = {}
            for item in json.loads(addr_proc.stdout):
                iface_map[item.get("ifname")] = [
                    f"{a.get('local')}/{a.get('prefixlen')}"
                    for a in (item.get("addr_info") or [])
                    if a.get("local")
                ]
            for iface in result["interfaces"]:
                iface["addresses"] = iface_map.get(iface["name"], [])
    except Exception:
        pass
    return result


def _parse_wg_dump(dump_text):
    """解析 `wg show all dump` 机器格式，丢弃私钥/预共享密钥，只返回安全字段。

    单独抽离便于单元测试：输入可包含私钥与 PSK，输出与日志中不得出现原值。
    """
    interfaces = []
    current = None
    now = time.time()
    for raw_line in dump_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if parts[0] == "interface":
            if len(parts) < 4:
                continue
            ifname = parts[1]
            # parts[2] 是接口私钥 → 立即丢弃，绝不进入响应/日志
            try:
                listen_port = int(parts[3])
            except (ValueError, IndexError):
                listen_port = None
            current = {"name": ifname, "addresses": [], "listen_port": listen_port,
                       "public_key_fingerprint": None, "peers": []}
            interfaces.append(current)
        elif parts[0] == "peer" and current is not None:
            if len(parts) < 9:
                continue
            pub_key = parts[1]
            # parts[2] 是预共享密钥 → 立即丢弃，绝不进入响应/日志
            try:
                fingerprint = "sha256:" + hashlib.sha256(base64.b64decode(pub_key)).hexdigest()[:16]
            except Exception:
                fingerprint = None
            endpoint = parts[3] or None
            allowed_ips = [ip for ip in parts[4].split(",") if ip]
            try:
                latest_handshake = int(parts[5])
            except ValueError:
                latest_handshake = 0
            try:
                rx = int(parts[6])
            except ValueError:
                rx = 0
            try:
                tx = int(parts[7])
            except ValueError:
                tx = 0
            current["peers"].append({
                "public_key_fingerprint": fingerprint,
                "endpoint": endpoint,
                "allowed_ips": allowed_ips,
                "latest_handshake_at": (
                    datetime.utcfromtimestamp(latest_handshake).isoformat() + "Z"
                    if latest_handshake else None
                ),
                "latest_handshake_age_seconds": (
                    max(0, int(now - latest_handshake)) if latest_handshake else None
                ),
                "rx_bytes": rx,
                "tx_bytes": tx,
            })
    return interfaces


def collect_metrics(lightweight=False):
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
        m['memory_cached'] = info.get('Cached', 0) + info.get('SReclaimable', 0)
        m['memory_buffers'] = info.get('Buffers', 0)
        swap_total = info.get('SwapTotal', 0)
        swap_free = info.get('SwapFree', 0)
        swap_used = max(0, swap_total - swap_free)
        m['swap_total'] = swap_total
        m['swap_used'] = swap_used
        m['swap_free'] = swap_free
        m['swap_percent'] = round(swap_used / swap_total * 100, 1) if swap_total > 0 else 0
    except Exception:
        m['memory_total'] = m['memory_used'] = m['memory_available'] = 0
        m['memory_percent'] = 0
        m['memory_cached'] = m['memory_buffers'] = 0
        m['swap_total'] = m['swap_used'] = m['swap_free'] = 0
        m['swap_percent'] = 0

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

    m['disks'] = _collect_mounts()

    # --- Disk IO ---
    m['disk_devices'] = _collect_disk_devices()
    m['disk_read_bytes'] = sum(item['read_bytes'] for item in m['disk_devices'])
    m['disk_write_bytes'] = sum(item['write_bytes'] for item in m['disk_devices'])

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
        if lightweight:
            raise RuntimeError('skip docker in lightweight profile')
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

    m['container_stats'] = [] if lightweight else _collect_container_stats()
    m['top_cpu_processes'] = [] if lightweight else _collect_processes('cpu')
    m['top_memory_processes'] = [] if lightweight else _collect_processes('memory')

    # --- Host info ---
    m['hostname'] = platform.node()
    m['platform'] = platform.platform()
    m['kernel'] = platform.release()
    m['agent_version'] = AGENT_VERSION
    m['timestamp'] = time.time()

    # 网络快照（v3.25.1）
    try:
        m['network'] = _network_snapshot()
        m['network_interfaces'] = [dict({'interface': name}, **values) for name, values in m['network'].items()]
    except Exception:
        m['network'] = {}
        m['network_interfaces'] = []

    return m


# === HTTP Handler ===

class AgentHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler serving metrics and service scan endpoints."""

    def _check_auth(self):
        # F1: token 现已强制必填，空 token 不再放行（/health 不调用本函数，保持探活开放）
        auth = self.headers.get('Authorization', '')
        if TOKEN and auth == f'Bearer {TOKEN}':
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

        elif path == '/api/v1/system/summary':
            if not self._check_auth():
                return
            self._json_response(collect_metrics(lightweight=True))

        elif path == '/api/v1/processes':
            if not self._check_auth():
                return
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            sort_key = (q.get('sort') or ['cpu'])[0]
            limit = int((q.get('limit') or ['200'])[0])
            search = (q.get('search') or [''])[0]
            user = (q.get('user') or [''])[0]
            state = (q.get('state') or [''])[0]
            rows = _collect_processes(sort_key, limit, search, user, state)
            self._json_response({'items': rows, 'total': len(rows), 'timestamp': time.time()})

        elif path == '/health':
            self._json_response({"status": "ok", "version": VERSION})

        elif path == '/api/v1/wireguard':
            if not self._check_auth():
                return
            self._json_response(_collect_wireguard())

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

        elif path == '/api/v1/network':
            if not self._check_auth():
                return
            self._json_response({
                'interfaces': _network_snapshot(),
                'timestamp': time.time(),
            })

        elif path == '/api/v1/log/scan':
            if not self._check_auth():
                return
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            log_path = (q.get('path') or [''])[0]
            pattern = (q.get('pattern') or [''])[0]
            tail_lines = int((q.get('tail_lines') or ['200'])[0])
            if not log_path or not pattern:
                self._json_response({'error': 'path and pattern required', 'matches': []})
                return
            matches = scanner.scan_log_pattern(log_path, pattern, tail_lines)
            self._json_response({'matches': matches, 'count': len(matches)})

        elif path == '/api/v1/images':
            if not self._check_auth():
                return
            self._json_response({'images': scanner.collect_images(), 'timestamp': time.time()})

        elif path == '/api/v1/registry-proxy':
            # v3.28 E1: 代理 Docker Hub digest 查询（供 VM2 等出站被拦的主机使用）
            if not self._check_auth():
                return
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            repo = (q.get('repo') or [''])[0].strip()
            if not repo:
                self._json_response({'error': 'repo param required'}, status=400)
                return
            result = {'repo': repo, 'remote_digest': None}
            try:
                import ssl as _ssl
                import urllib.request
                import socket
                # 强制 IPv4：MFA 的 IPv6 路由不可达，urllib 默认优先 IPv6 会失败
                _ctx = _ssl.create_default_context()
                _ctx.maximum_version = _ssl.TLSVersion.TLSv1_2
                _orig_getaddrinfo = socket.getaddrinfo

                def _ipv4_family(host, port, family=0, type=0, proto=0, flags=0):
                    # 只保留 IPv4 地址族，避免 urllib 优先 IPv6
                    addrs = _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
                    return addrs
                urllib.request.socket.getaddrinfo = _ipv4_family

                if '/' not in repo:
                    repo = 'library/' + repo
                token_url = f'https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull'
                with urllib.request.urlopen(token_url, timeout=5, context=_ctx) as resp:
                    token = json.loads(resp.read()).get('token', '')
                req = urllib.request.Request(
                    f'https://registry-1.docker.io/v2/{repo}/manifests/latest',
                    headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.docker.distribution.manifest.v2+json'})
                with urllib.request.urlopen(req, timeout=5, context=_ctx) as resp:
                    result['remote_digest'] = resp.headers.get('Docker-Content-Digest')
            except Exception as e:
                result['error'] = str(e)
            self._json_response(result)

        elif path == '/api/v1/backup/check':
            if not self._check_auth():
                return
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            target = (q.get('path') or [''])[0]
            min_size = int((q.get('min_size') or ['0'])[0])
            if not target:
                self._json_response({'error': 'path required', 'check': None})
                return
            result = scanner.check_backup_path(target, min_size)
            self._json_response({'check': result})

        elif path == '/api/v1/network/ping':
            if not self._check_auth():
                return
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            target = (q.get('target') or ['8.8.8.8'])[0]
            result = scanner.ping_latency(target)
            result['timestamp'] = time.time()
            self._json_response(result)

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
    parser.add_argument('--token', type=str, required=True,
                        help='Auth token (REQUIRED since v2.2.0; 推荐 >=16 字符，缺失将拒绝启动)')
    parser.add_argument('--bind', type=str, default='0.0.0.0', help='Bind address (default: 0.0.0.0)')
    parser.add_argument('--scan-interval', type=int, default=300,
                        help='Background scan interval in seconds (default: 300)')
    args = parser.parse_args()

    global TOKEN, _scan_interval
    TOKEN = args.token
    if len(TOKEN) < 16:
        # F1: 不强制拒绝，仅告警，避免过渡期短 token 部署直接崩；但生产务必用强 token
        print(f"[WARN] Agent token 长度 {len(TOKEN)} < 16，存在弱口令风险，建议更换为 secrets.token_urlsafe(32)", flush=True)
    _scan_interval = args.scan_interval

    scan_thread = threading.Thread(target=_background_scan_loop, daemon=True)
    scan_thread.start()
    print(f"[scanner] Background scan started (interval={_scan_interval}s)", flush=True)

    # Start accepting health/metric requests immediately. Service discovery is
    # intentionally background-only so slow PVE guest agents cannot delay boot.
    server = http.server.ThreadingHTTPServer((args.bind, args.port), AgentHandler)
    print(f"OpsAgent v{VERSION} listening on {args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)
        server.server_close()


if __name__ == '__main__':
    main()
