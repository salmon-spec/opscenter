"""UFW and firewalld management with SSH lockout safeguards."""
from __future__ import annotations

import ipaddress
import re
import shlex
import shutil
import subprocess
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.database import get_db
from app.models import Server
from app.ssh_manager import get_ssh_client, ssh_exec


router = APIRouter(prefix="/api/v2", tags=["firewall"])
_PORT_RE = re.compile(r"^(\d{1,5})(?:[-:](\d{1,5}))?$")
_UFW_RULE_RE = re.compile(r"^\[\s*(\d+)\]\s+(\S+)\s+(ALLOW|DENY|REJECT)\s+(?:IN|OUT)?\s*(.*)$", re.I)


class FirewallRuleRequest(BaseModel):
    port: str
    protocol: Literal["tcp", "udp"] = "tcp"
    action: Literal["allow", "deny"] = "allow"
    source: str = "any"


class FirewallDeleteRequest(BaseModel):
    rule_id: str
    port: str
    protocol: Literal["tcp", "udp"] = "tcp"
    confirm_ssh_disruption: bool = False


class FirewallStateRequest(BaseModel):
    enabled: bool
    confirm_name: str


def _server(server_id: str) -> Server:
    try:
        uid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="主机不存在")
    with get_db() as db:
        row = db.query(Server).filter(Server.id == uid).first()
        if not row:
            raise HTTPException(status_code=404, detail="主机不存在")
        db.expunge(row)
        return row


def _port(value: str) -> tuple[str, int, int]:
    match = _PORT_RE.fullmatch((value or "").strip())
    if not match:
        raise HTTPException(status_code=400, detail="端口必须是 1-65535 或合法端口范围")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if start < 1 or end > 65535 or start > end:
        raise HTTPException(status_code=400, detail="端口范围无效")
    return (str(start) if start == end else f"{start}-{end}", start, end)


def _source(value: str) -> str:
    value = (value or "any").strip().lower()
    if value in {"any", "anywhere", "0.0.0.0/0", "::/0"}:
        return "any"
    try:
        return str(ipaddress.ip_network(value, strict=False))
    except ValueError:
        raise HTTPException(status_code=400, detail="来源必须是 IP 地址或 CIDR 网段")


def _execute(server: Server, args: list[str], timeout: int = 20) -> str:
    if server.agent_type == "local":
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise HTTPException(status_code=400, detail=f"命令不存在: {args[0]}")
        if result.returncode != 0:
            raise HTTPException(status_code=502, detail=(result.stderr or result.stdout).strip()[-500:])
        return result.stdout
    client = get_ssh_client(server)
    if not client:
        raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证或连接失败")
    try:
        out, err, code = ssh_exec(client, " ".join(shlex.quote(part) for part in args), timeout=timeout)
        if code != 0:
            raise HTTPException(status_code=502, detail=(err or out).strip()[-500:])
        return out
    finally:
        client.close()


def _detect(server: Server) -> Optional[str]:
    if server.agent_type == "local":
        if shutil.which("ufw"):
            return "ufw"
        if shutil.which("firewall-cmd"):
            return "firewalld"
        return None
    client = get_ssh_client(server)
    if not client:
        raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证或连接失败")
    try:
        out, _err, _code = ssh_exec(client, "if command -v ufw >/dev/null 2>&1; then echo ufw; elif command -v firewall-cmd >/dev/null 2>&1; then echo firewalld; fi", timeout=10)
        return out.strip().splitlines()[0] if out.strip() else None
    finally:
        client.close()


def _parse_ufw(output: str) -> list[dict]:
    rules = []
    for line in output.splitlines():
        match = _UFW_RULE_RE.match(line.strip())
        if not match:
            continue
        target = match.group(2)
        port, _, protocol = target.partition("/")
        rules.append({"id": match.group(1), "port": port, "protocol": protocol or "any", "action": match.group(3).lower(), "source": match.group(4).strip() or "any"})
    return rules


@router.get("/servers/{server_id}/firewall", dependencies=[Depends(get_current_user)])
def firewall_status(server_id: str):
    server = _server(server_id)
    backend = _detect(server)
    if not backend:
        return {"backend": None, "installed": False, "enabled": False, "rules": [], "ssh_port": server.ssh_port or 22}
    if backend == "ufw":
        output = _execute(server, ["sudo", "ufw", "status", "numbered"])
        return {"backend": backend, "installed": True, "enabled": "Status: active" in output, "rules": _parse_ufw(output), "ssh_port": server.ssh_port or 22}
    state = _execute(server, ["sudo", "firewall-cmd", "--state"]).strip()
    ports = _execute(server, ["sudo", "firewall-cmd", "--list-ports"]).split()
    rules = [{"id": token, "port": token.partition("/")[0], "protocol": token.partition("/")[2] or "tcp", "action": "allow", "source": "any"} for token in ports]
    return {"backend": backend, "installed": True, "enabled": state == "running", "rules": rules, "ssh_port": server.ssh_port or 22}


@router.post("/servers/{server_id}/firewall/rules", dependencies=[Depends(get_current_user)], status_code=201)
def add_firewall_rule(server_id: str, payload: FirewallRuleRequest):
    server = _server(server_id)
    backend = _detect(server)
    if not backend:
        raise HTTPException(status_code=400, detail="主机未安装 UFW 或 firewalld")
    port, _start, _end = _port(payload.port)
    source = _source(payload.source)
    if backend == "ufw":
        ufw_port = port.replace("-", ":")
        args = ["sudo", "ufw", "--force", payload.action]
        if source == "any":
            args += [f"{ufw_port}/{payload.protocol}"]
        else:
            args += ["from", source, "to", "any", "port", ufw_port, "proto", payload.protocol]
        _execute(server, args)
    else:
        if payload.action != "allow" or source != "any":
            raise HTTPException(status_code=400, detail="firewalld 首版仅支持开放任意来源端口")
        _execute(server, ["sudo", "firewall-cmd", "--permanent", f"--add-port={port}/{payload.protocol}"])
        _execute(server, ["sudo", "firewall-cmd", "--reload"])
    return {"ok": True, "backend": backend, "port": port, "protocol": payload.protocol, "action": payload.action, "source": source}


@router.post("/servers/{server_id}/firewall/rules/delete", dependencies=[Depends(get_current_user)])
def delete_firewall_rule(server_id: str, payload: FirewallDeleteRequest):
    server = _server(server_id)
    backend = _detect(server)
    if not backend:
        raise HTTPException(status_code=400, detail="主机未安装 UFW 或 firewalld")
    port, start, end = _port(payload.port)
    ssh_port = int(server.ssh_port or 22)
    if start <= ssh_port <= end and not payload.confirm_ssh_disruption:
        raise HTTPException(status_code=409, detail=f"该规则包含当前 SSH 端口 {ssh_port}，需要额外确认")
    if backend == "ufw":
        if not payload.rule_id.isdigit():
            raise HTTPException(status_code=400, detail="非法 UFW 规则编号")
        _execute(server, ["sudo", "ufw", "--force", "delete", payload.rule_id])
    else:
        _execute(server, ["sudo", "firewall-cmd", "--permanent", f"--remove-port={port}/{payload.protocol}"])
        _execute(server, ["sudo", "firewall-cmd", "--reload"])
    return {"ok": True, "backend": backend, "deleted": payload.rule_id}


@router.post("/servers/{server_id}/firewall/state", dependencies=[Depends(get_current_user)])
def set_firewall_state(server_id: str, payload: FirewallStateRequest):
    server = _server(server_id)
    if payload.confirm_name != server.name:
        raise HTTPException(status_code=400, detail="确认主机名称不匹配")
    backend = _detect(server)
    if not backend:
        raise HTTPException(status_code=400, detail="主机未安装 UFW 或 firewalld")
    if backend == "ufw":
        _execute(server, ["sudo", "ufw", "--force", "enable" if payload.enabled else "disable"])
    else:
        _execute(server, ["sudo", "systemctl", "enable" if payload.enabled else "disable", "--now", "firewalld"])
    return {"ok": True, "backend": backend, "enabled": payload.enabled}
