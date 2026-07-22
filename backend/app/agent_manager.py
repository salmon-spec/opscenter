"""Agent manager: deploy, check, upgrade, uninstall OpsAgent on remote servers."""
import paramiko
import io
import json
import secrets
from typing import Optional, Tuple, Dict
from app.models import Server
import os


# 动态读取Agent版本号
_AGENT_VERSION = "2.1.0"  # 默认值
try:
    _agent_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'agent', 'opsagent.py')
    with open(_agent_src) as f:
        for line in f:
            if line.strip().startswith('AGENT_VERSION'):
                _AGENT_VERSION = line.split('=')[1].strip().strip('"').strip("'")
                break
except:
    pass

# Agent file path on target server
AGENT_DIR = "/opt/opsagent"
AGENT_SCRIPT = "opsagent.py"
AGENT_SERVICE = "opsagent.service"
AGENT_DEFAULT_PORT = 19100


def _get_ssh_client(server: Server, password: str = None) -> Optional[paramiko.SSHClient]:
    """Reuse ssh_manager connection logic."""
    from app.ssh_manager import get_ssh_client
    return get_ssh_client(server, password=password)


def _ssh_exec(client: paramiko.SSHClient, cmd: str, timeout: int = 30) -> Tuple[str, str, int]:
    """Execute SSH command."""
    from app.ssh_manager import ssh_exec
    return ssh_exec(client, cmd, timeout=timeout)


def _generate_token() -> str:
    """Generate a random auth token."""
    return secrets.token_hex(16)


def _build_service_content(port: int, token: str, bind: str = "0.0.0.0") -> str:
    """Generate systemd service file content."""
    return f"""[Unit]
Description=OpsCenter Monitoring Agent
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 {AGENT_DIR}/{AGENT_SCRIPT} --port {port} --token {token} --bind {bind}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def deploy_agent(server: Server, password: str = None, port: int = AGENT_DEFAULT_PORT) -> Dict:
    """Deploy OpsAgent to a remote server via SSH.
    
    Returns dict with: success, message, agent_port, agent_token, agent_version
    """
    client = _get_ssh_client(server, password=password)
    if not client:
        return {"success": False, "message": "SSH连接失败，请检查凭证"}

    try:
        # 1. Check Python3
        out, err, code = _ssh_exec(client, "which python3 && python3 --version")
        if code != 0:
            return {"success": False, "message": "目标服务器未安装Python3，无法部署Agent"}

        # 2. Check if agent is already running — if so, upgrade by reinstalling
        out, _, _ = _ssh_exec(client, f"systemctl is-active {AGENT_SERVICE}")
        existing_config = {}
        if out.strip() == "active":
            out2, _, _ = _ssh_exec(client, f"cat {AGENT_DIR}/.agent_config")
            try:
                existing_config = json.loads(out2.strip())
                old_version = existing_config.get("version", "1.0.0")
                if old_version == _AGENT_VERSION:
                    return {
                        "success": True,
                        "message": f"Agent已是最新版本(v{_AGENT_VERSION})",
                        "agent_port": existing_config.get("port", port),
                        "agent_token": existing_config.get("token", ""),
                        "agent_version": _AGENT_VERSION,
                    }
            except Exception:
                pass
            # Old version detected — stop service, will reinstall below
            _ssh_exec(client, f"sudo systemctl stop {AGENT_SERVICE}")
            import time; time.sleep(1)

        # 3. Create directory and upload agent script
        token = existing_config.get('token', '') or _generate_token()
        _ssh_exec(client, f"sudo mkdir -p {AGENT_DIR} && sudo chmod 777 {AGENT_DIR}")
        _ssh_exec(client, f"sudo rm -f {AGENT_DIR}/*.py {AGENT_DIR}/.agent_config")

        # Upload agent script + scanner module via SFTP
        sftp = client.open_sftp()
        files_to_upload = [AGENT_SCRIPT, "scanner.py"]
        upload_ok = False
        for search_dir in ["agent/", "/opt/opscenter/agent/"]:
            try:
                for fname in files_to_upload:
                    local_path = f"{search_dir}{fname}"
                    remote_path = f"{AGENT_DIR}/{fname}"
                    with open(local_path, 'r') as local_f:
                        content_to_write = local_f.read()
                    with sftp.file(remote_path, 'w') as f:
                        f.write(content_to_write)
                upload_ok = True
                break
            except FileNotFoundError:
                continue
        sftp.close()
        if not upload_ok:
            return {"success": False, "message": "Agent脚本文件未找到，请检查部署路径"}

        # 4. Create systemd service
        service_content = _build_service_content(port, token)
        # Write service file via python
        _ssh_exec(client, f"sudo python3 -c \""
                   f"with open('/etc/systemd/system/{AGENT_SERVICE}', 'w') as f: "
                   f"f.write('''{service_content}''')\"")

        # 5. Save agent config
        config = {"port": port, "token": token, "version": _AGENT_VERSION}
        _ssh_exec(client, f"sudo python3 -c \""
                   f"import json; "
                   f"open('{AGENT_DIR}/.agent_config', 'w').write(json.dumps({config}))\"")

        # 6. Reload systemd, enable and start service
        _ssh_exec(client, "sudo systemctl daemon-reload")
        _ssh_exec(client, f"sudo systemctl enable {AGENT_SERVICE}")
        out, err, code = _ssh_exec(client, f"sudo systemctl start {AGENT_SERVICE}")

        if code != 0 and 'Job' not in err:
            return {"success": False, "message": f"Agent启动失败: {err.strip()}"}

        # 7. Wait and verify
        import time
        time.sleep(2)
        out, _, _ = _ssh_exec(client, f"systemctl is-active {AGENT_SERVICE}")
        if out.strip() != "active":
            # Check logs for error
            log_out, _, _ = _ssh_exec(client, f"journalctl -u {AGENT_SERVICE} --no-pager -n 10")
            return {"success": False, "message": f"Agent启动异常: {log_out[-200:]}"}

        # 8. Try to open firewall if ufw is active
        _ssh_exec(client, f"sudo ufw status | grep -q 'active' && ufw allow {port}/tcp 2>/dev/null || true")

        return {
            "success": True,
            "message": "Agent部署成功",
            "agent_port": port,
            "agent_token": token,
            "agent_version": _AGENT_VERSION,
        }

    except Exception as e:
        return {"success": False, "message": f"部署异常: {str(e)}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


def check_agent_status(server: Server, password: str = None) -> Dict:
    """Check if OpsAgent is running on the remote server."""
    client = _get_ssh_client(server, password=password)
    if not client:
        return {"status": "unreachable", "message": "SSH连接失败"}

    try:
        out, _, code = _ssh_exec(client, f"systemctl is-active {AGENT_SERVICE}")
        if out.strip() == "active":
            # Read config
            config_out, _, _ = _ssh_exec(client, f"cat {AGENT_DIR}/.agent_config")
            try:
                config = json.loads(config_out.strip())
                return {
                    "status": "running",
                    "agent_port": config.get("port", AGENT_DEFAULT_PORT),
                    "agent_token": config.get("token", ""),
                    "agent_version": config.get("version", _AGENT_VERSION),
                }
            except Exception:
                return {"status": "running", "agent_port": AGENT_DEFAULT_PORT, "agent_token": "", "agent_version": _AGENT_VERSION}
        elif out.strip() == "inactive":
            return {"status": "stopped", "message": "Agent已安装但未运行"}
        else:
            # Check if agent files exist
            out2, _, _ = _ssh_exec(client, f"test -f {AGENT_DIR}/{AGENT_SCRIPT} && echo exists")
            if "exists" in out2:
                return {"status": "installed_stopped", "message": "Agent已安装但服务未注册"}
            return {"status": "not_deployed", "message": "Agent未部署"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        try:
            client.close()
        except Exception:
            pass


def _normalize_agent_metrics(data: dict) -> dict:
    """Normalize Agent metric field names for compatibility.
    
    Agent v2.0 source uses canonical names but older deployed agents 
    may use different names. This maps old names to canonical names.
    """
    field_map = {
        "cpu_usage": "cpu_percent",
        "memory_usage": "memory_percent",
        "disk_usage": "disk_percent",
        "disk_available": "disk_avail",
        "load_1m": "load1",
        "load_5m": "load5",
        "load_15m": "load15",
        "network_rx_bytes": "net_rx_bytes",
        "network_tx_bytes": "net_tx_bytes",
        "network_iface": "net_iface",
        "uptime_seconds": "uptime",
        "docker_containers": "container_running",
    }
    result = {}
    for k, v in data.items():
        canonical = field_map.get(k, k)
        result[canonical] = v
    return result


def fetch_agent_metrics(host: str, port: int = AGENT_DEFAULT_PORT, token: str = "") -> Optional[Dict]:
    """Fetch metrics from a running OpsAgent via HTTP."""
    import requests as req
    try:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = req.get(f"http://{host}:{port}/metrics", headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return _normalize_agent_metrics(data)
        return None
    except Exception:
        return None


def uninstall_agent(server: Server, password: str = None) -> Dict:
    """Uninstall OpsAgent from a remote server."""
    client = _get_ssh_client(server, password=password)
    if not client:
        return {"success": False, "message": "SSH连接失败"}

    try:
        _ssh_exec(client, f"sudo systemctl stop {AGENT_SERVICE} 2>/dev/null || true")
        _ssh_exec(client, f"sudo systemctl disable {AGENT_SERVICE} 2>/dev/null || true")
        _ssh_exec(client, f"sudo rm -f /etc/systemd/system/{AGENT_SERVICE}")
        _ssh_exec(client, f"sudo rm -rf {AGENT_DIR}")
        _ssh_exec(client, "sudo systemctl daemon-reload")
        return {"success": True, "message": "Agent已卸载"}
    except Exception as e:
        return {"success": False, "message": f"卸载异常: {str(e)}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


def fetch_agent_services(host: str, port: int = AGENT_DEFAULT_PORT, token: str = "") -> Optional[Dict]:
    """Fetch discovered services from a running OpsAgent v2.0+ via HTTP.
    
    Returns dict with 'containers', 'ports', 'systemd_services' or None on failure.
    """
    import requests as req
    try:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = req.get(f"http://{host}:{port}/api/v1/services", headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def trigger_agent_scan(host: str, port: int = AGENT_DEFAULT_PORT, token: str = "") -> Optional[Dict]:
    """Trigger a fresh scan on a remote OpsAgent v2.0+ and return results.
    
    Returns dict with 'containers', 'ports', 'systemd_services' or None on failure.
    """
    import requests as req
    try:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = req.post(f"http://{host}:{port}/api/v1/scan", headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def upgrade_local_agent():
    """升级本机Agent：复制源码到/opt/opsagent/ + systemctl restart"""
    import shutil, subprocess
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'agent')
    dst_dir = '/opt/opsagent'
    
    # 复制源码文件
    for fname in ['opsagent.py', 'scanner.py']:
        src = os.path.join(src_dir, fname)
        dst = os.path.join(dst_dir, fname)
        if os.path.exists(src):
            shutil.copy2(src, dst)
    
    # 重启服务
    result = subprocess.run(['systemctl', 'restart', 'opsagent'], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        return {"success": False, "message": f"重启Agent失败: {result.stderr}"}
    
    # 等待验证
    import time
    time.sleep(2)
    check = subprocess.run(['systemctl', 'is-active', 'opsagent'], capture_output=True, text=True)
    if check.stdout.strip() != 'active':
        return {"success": False, "message": "Agent重启后未激活"}
    
    return {"success": True, "version": _AGENT_VERSION, "message": f"本机Agent已升级到 v{_AGENT_VERSION}"}
