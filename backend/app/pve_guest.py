"""Resolve guest SSH access through its authenticated PVE control plane."""
import base64
import hashlib
import io
import ipaddress
import json
import re
from urllib.parse import unquote

import paramiko

from app.database import get_db
from app.models import Server
from app.ssh_host_keys import configure_host_keys, persist_host_keys
from app.ssh_manager import get_ssh_client, ssh_exec


def guest_identity(server):
    sources = [t.split(":", 1)[1] for t in server.tags or [] if re.fullmatch(r"pve-source:[0-9a-f]{20}", t)]
    vmids = [int(t.split(":", 1)[1]) for t in server.tags or [] if re.fullmatch(r"pve-vmid:\d+", t)]
    return (sources[0], vmids[0]) if len(sources) == len(vmids) == 1 and vmids[0] >= 100 else None


def guest_address(config, server):
    addresses = set()
    for name, value in config.items():
        if re.fullmatch(r"ipconfig\d+", name):
            for address in re.findall(r"(?:^|,)ip=([^,]+)", value):
                if address not in {"dhcp", "manual"}:
                    addresses.add(str(ipaddress.ip_interface(address).ip))
    for value in (server.lan_ip, server.host):
        if value in addresses:
            return value
    raise ValueError("PVE 配置中的地址与主机记录不一致，无法自动安装 Agent")


def credential_key(config, servers):
    public_keys = {k.split()[1] for k in unquote(config.get("sshkeys", "")).splitlines()
                   if k.startswith("ssh-ed25519 ") and len(k.split()) >= 2}
    for server in sorted(servers, key=lambda s: not s.enabled):
        value = server.ssh_key
        if not value or value.startswith("__password__"):
            continue
        try:
            key = paramiko.Ed25519Key.from_private_key(io.StringIO(value))
        except (ValueError, paramiko.SSHException):
            continue
        if key.get_base64() in public_keys:
            return key, value
    raise ValueError("未找到与虚拟机 cloud-init 公钥匹配的管理凭据，请配置该主机 SSH 凭据")


def prepare_guest_access(server_id):
    with get_db() as db:
        servers = db.query(Server).all()
        server = next(s for s in servers if str(s.id) == str(server_id))
        if server.ssh_key:
            return
        identity = guest_identity(server)
        if not identity:
            raise ValueError("主机未配置 SSH 凭据，无法安装 Agent")
        pve = next((s for s in servers if s.agent_type == "local" and s.enabled), None)
        if not pve:
            raise ValueError("PVE 管理主机不可用")
        for row in servers:
            db.expunge(row)
    source_id, vmid = identity
    client = get_ssh_client(pve)
    if not client:
        raise ValueError("PVE SSH 连接失败")
    try:
        def call(command):
            out, _, code = ssh_exec(client, command, timeout=25)
            if code != 0:
                raise ValueError("PVE 客户机资料读取失败，请检查 QEMU Guest Agent")
            return out
        machine_id = call("cat /etc/machine-id").strip()
        if hashlib.sha256(machine_id.encode()).hexdigest()[:20] != source_id:
            raise ValueError("PVE 来源标识不匹配")
        rows = json.loads(call("pvesh get /cluster/resources --type vm --output-format json"))
        vm = next((r for r in rows if r.get("type") == "qemu" and r["vmid"] == vmid), None)
        if not vm or vm.get("status") != "running" or vm.get("template"):
            raise ValueError("对应虚拟机未运行，无法安装 Agent")
        node = vm["node"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", node):
            raise ValueError("PVE 节点名称无效")
        config = json.loads(call(f"pvesh get /nodes/{node}/qemu/{vmid}/config --output-format json"))
        address = guest_address(config, server)
        username = config.get("ciuser")
        if not username or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", username):
            raise ValueError("虚拟机未配置有效的 cloud-init 管理用户")
        key, secret = credential_key(config, servers)
        result = json.loads(call(f"qm guest exec {vmid} -- cat /etc/ssh/ssh_host_ed25519_key.pub"))
        if result.get("exited") != 1 or result.get("exitcode") != 0:
            raise ValueError("无法通过 PVE 核对虚拟机 SSH 主机密钥")
        parts = result.get("out-data", "").split()
        if len(parts) < 2 or parts[0] != "ssh-ed25519":
            raise ValueError("虚拟机 SSH 主机密钥无效")
        host_key = paramiko.Ed25519Key(data=base64.b64decode(parts[1], validate=True))
        call(f"/usr/local/sbin/opscenter-agent-forward {vmid} {address}")
    finally:
        client.close()
    guest = paramiko.SSHClient()
    configure_host_keys(guest)
    try:
        for existing in (guest.get_host_keys().lookup(address), guest._system_host_keys.lookup(address)):
            if existing and (host_key.get_name() not in existing or existing[host_key.get_name()] != host_key):
                raise ValueError("虚拟机 SSH 主机密钥与可信记录不一致")
        guest.get_host_keys().add(address, host_key.get_name(), host_key)
        guest.connect(hostname=address, port=server.ssh_port or 22, username=username, pkey=key,
                      timeout=8, banner_timeout=8, auth_timeout=8, allow_agent=False, look_for_keys=False)
        _, _, code = ssh_exec(guest, "true" if username == "root" else "sudo -n true")
        if code != 0:
            raise ValueError("虚拟机管理用户没有免密 sudo 权限")
        persist_host_keys(guest)
    finally:
        guest.close()
    with get_db() as db:
        row = db.query(Server).filter(Server.id == server.id).with_for_update().one()
        if not row.ssh_key:
            if row.host != server.host or row.lan_ip != server.lan_ip or guest_identity(row) != identity:
                raise ValueError("安装准备期间主机资料已变化，请重试")
            row.ssh_user, row.ssh_key = username, secret
            db.commit()
