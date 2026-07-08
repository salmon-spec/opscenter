"""Agent manager: deploy, check, upgrade, uninstall OpsAgent on remote servers."""
import paramiko
import io
import json
import secrets
from typing import Optional, Tuple, Dict
from app.models import Server


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

        # 2. Check if agent is already running
        out, _, _ = _ssh_exec(client, f"systemctl is-active {AGENT_SERVICE}")
        if out.strip() == "active":
            # Already running, check port
            out2, _, _ = _ssh_exec(client, f"cat {AGENT_DIR}/.agent_config")
            try:
                config = json.loads(out2.strip())
                return {
                    "success": True,
                    "message": "Agent已在运行",
                    "agent_port": config.get("port", port),
                    "agent_token": config.get("token", ""),
                    "agent_version": config.get("version", "1.0.0"),
                }
            except Exception:
                pass

        # 3. Create directory and upload agent script
        token = _generate_token()
        _ssh_exec(client, f"mkdir -p {AGENT_DIR}")

        # Upload agent script via SFTP
        sftp = client.open_sftp()
        # Read agent script from local file
        local_agent_path = f"{AGENT_DIR}/{AGENT_SCRIPT}"
        try:
            # Write agent script content via SFTP
            with sftp.file(f"{AGENT_DIR}/{AGENT_SCRIPT}", 'w') as f:
                with open(f"agent/{AGENT_SCRIPT}", 'r') as local_f:
                    f.write(local_f.read())
        except FileNotFoundError:
            # Fallback: read from /opt/opscenter/agent/
            try:
                with sftp.file(f"{AGENT_DIR}/{AGENT_SCRIPT}", 'w') as f:
                    with open(f"/opt/opscenter/agent/{AGENT_SCRIPT}", 'r') as local_f:
                        f.write(local_f.read())
            except FileNotFoundError:
                sftp.close()
                return {"success": False, "message": "Agent脚本文件未找到，请检查部署路径"}

        sftp.close()

        # 4. Create systemd service
        service_content = _build_service_content(port, token)
        # Write service file via python
        _ssh_exec(client, f"python3 -c \""
                   f"with open('/etc/systemd/system/{AGENT_SERVICE}', 'w') as f: "
                   f"f.write('''{service_content}''')\"")

        # 5. Save agent config
        config = {"port": port, "token": token, "version": "1.0.0"}
        _ssh_exec(client, f"python3 -c \""
                   f"import json; "
                   f"open('{AGENT_DIR}/.agent_config', 'w').write(json.dumps({config}))\"")

        # 6. Reload systemd, enable and start service
        _ssh_exec(client, "systemctl daemon-reload")
        _ssh_exec(client, f"systemctl enable {AGENT_SERVICE}")
        out, err, code = _ssh_exec(client, f"systemctl start {AGENT_SERVICE}")

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
        _ssh_exec(client, f"ufw status | grep -q 'active' && ufw allow {port}/tcp 2>/dev/null || true")

        return {
            "success": True,
            "message": "Agent部署成功",
            "agent_port": port,
            "agent_token": token,
            "agent_version": "1.0.0",
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
                    "agent_version": config.get("version", "1.0.0"),
                }
            except Exception:
                return {"status": "running", "agent_port": AGENT_DEFAULT_PORT, "agent_token": "", "agent_version": "1.0.0"}
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


def fetch_agent_metrics(host: str, port: int = AGENT_DEFAULT_PORT, token: str = "") -> Optional[Dict]:
    """Fetch metrics from a running OpsAgent via HTTP."""
    import requests as req
    try:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = req.get(f"http://{host}:{port}/metrics", headers=headers, timeout=5)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def uninstall_agent(server: Server, password: str = None) -> Dict:
    """Uninstall OpsAgent from a remote server."""
    client = _get_ssh_client(server, password=password)
    if not client:
        return {"success": False, "message": "SSH连接失败"}

    try:
        _ssh_exec(client, f"systemctl stop {AGENT_SERVICE} 2>/dev/null || true")
        _ssh_exec(client, f"systemctl disable {AGENT_SERVICE} 2>/dev/null || true")
        _ssh_exec(client, f"rm -f /etc/systemd/system/{AGENT_SERVICE}")
        _ssh_exec(client, f"rm -rf {AGENT_DIR}")
        _ssh_exec(client, "systemctl daemon-reload")
        return {"success": True, "message": "Agent已卸载"}
    except Exception as e:
        return {"success": False, "message": f"卸载异常: {str(e)}"}
    finally:
        try:
            client.close()
        except Exception:
            pass
