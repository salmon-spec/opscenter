"""Guarded multi-host OpenSSH service, session and key management."""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import shlex
import subprocess
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.config import CONTAINERIZED
from app.database import get_db
from app.models import Server
from app.ssh_manager import get_ssh_client, ssh_exec


router = APIRouter(prefix="/api/v2", tags=["ssh-management"])
_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_GROUP_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_KEY_TYPES = {
    "ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com",
}
# OpenSSH uses the first obtained value for most scalar settings. A leading
# drop-in name makes these guarded values take precedence over distro defaults.
_DROPIN = "/etc/ssh/sshd_config.d/00-opscenter.conf"


class AuthorizedKeyRequest(BaseModel):
    user: str = Field(min_length=1, max_length=32)
    public_key: str = Field(min_length=20, max_length=16384)


class AuthorizedKeyDeleteRequest(BaseModel):
    user: str = Field(min_length=1, max_length=32)
    fingerprint: str = Field(min_length=10, max_length=128)


class SSHConfigRequest(BaseModel):
    port: int = Field(22, ge=1, le=65535)
    permit_root_login: Literal["yes", "no", "prohibit-password"] = "prohibit-password"
    password_authentication: bool = False
    pubkey_authentication: bool = True
    max_auth_tries: int = Field(6, ge=1, le=10)
    client_alive_interval: int = Field(300, ge=0, le=3600)
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


def _run(server: Server, command: str, timeout: int = 20) -> tuple[str, str, int]:
    if server.agent_type == "local" and not CONTAINERIZED:
        try:
            result = subprocess.run(["sh", "-lc", command], capture_output=True, text=True, timeout=timeout)
            return result.stdout, result.stderr, result.returncode
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return "", str(exc), -1
    client = get_ssh_client(server)
    if not client:
        raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证或连接失败")
    try:
        return ssh_exec(client, command, timeout=timeout)
    finally:
        client.close()


def _must_run(server: Server, command: str, timeout: int = 20) -> str:
    out, err, code = _run(server, command, timeout)
    if code != 0:
        raise HTTPException(status_code=502, detail=(err or out or "SSH 命令执行失败").strip()[-500:])
    return out


def _user(value: str) -> str:
    value = (value or "").strip()
    if not _USER_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="Linux 用户名格式无效")
    return value


def _account(server: Server, username: str) -> tuple[str, str]:
    username = _user(username)
    out = _must_run(server, f"getent passwd {shlex.quote(username)}")
    fields = out.strip().split(":")
    if len(fields) < 7 or not fields[5].startswith("/"):
        raise HTTPException(status_code=404, detail="主机上不存在该用户")
    group = _must_run(server, f"id -gn {shlex.quote(username)}").strip()
    if not _GROUP_RE.fullmatch(group):
        raise HTTPException(status_code=502, detail="无法解析用户组")
    return fields[5], group


def _parse_key(line: str) -> dict:
    parts = line.strip().split(None, 2)
    if len(parts) < 2 or parts[0] not in _KEY_TYPES:
        raise HTTPException(status_code=400, detail="仅支持常用 OpenSSH 公钥格式")
    try:
        raw = base64.b64decode(parts[1], validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(status_code=400, detail="公钥 Base64 内容无效")
    if len(raw) < 16 or len(raw) > 8192:
        raise HTTPException(status_code=400, detail="公钥长度无效")
    fingerprint = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    return {
        "type": parts[0], "fingerprint": f"SHA256:{fingerprint}",
        "comment": parts[2][:200] if len(parts) > 2 else "",
    }


def _key_lines(server: Server, username: str) -> tuple[str, str, list[str]]:
    home, group = _account(server, username)
    path = f"{home}/.ssh/authorized_keys"
    out, _err, _code = _run(server, f"sudo -n cat {shlex.quote(path)} 2>/dev/null || true")
    return path, group, out.splitlines()


def _write_keys(server: Server, username: str, path: str, group: str, lines: list[str]) -> None:
    content = "\n".join(lines).rstrip("\n") + ("\n" if lines else "")
    encoded = base64.b64encode(content.encode()).decode()
    parent = path.rsplit("/", 1)[0]
    temp = f"{path}.opscenter.tmp"
    command = (
        f"sudo -n install -d -m 700 -o {shlex.quote(username)} -g {shlex.quote(group)} {shlex.quote(parent)} && "
        f"printf %s {shlex.quote(encoded)} | base64 -d | sudo -n tee {shlex.quote(temp)} >/dev/null && "
        f"sudo -n chown {shlex.quote(username)}:{shlex.quote(group)} {shlex.quote(temp)} && "
        f"sudo -n chmod 600 {shlex.quote(temp)} && sudo -n mv {shlex.quote(temp)} {shlex.quote(path)}"
    )
    _must_run(server, command)


def _service(server: Server) -> tuple[str, str]:
    out, _err, _code = _run(server, "if systemctl list-unit-files ssh.service >/dev/null 2>&1; then echo ssh; elif systemctl list-unit-files sshd.service >/dev/null 2>&1; then echo sshd; else echo unknown; fi")
    name = out.strip().splitlines()[0] if out.strip() else "unknown"
    if name not in {"ssh", "sshd"}:
        return "unknown", "unavailable"
    status_out, _status_err, status_code = _run(server, f"systemctl is-active {name}")
    return name, status_out.strip() if status_code == 0 else (status_out.strip() or "inactive")


def _effective_config(server: Server) -> dict:
    out, _err, code = _run(server, "sudo -n sshd -T 2>/dev/null || sshd -T 2>/dev/null")
    if code != 0:
        return {}
    wanted = {"port", "permitrootlogin", "passwordauthentication", "pubkeyauthentication", "maxauthtries", "clientaliveinterval"}
    values = {}
    for line in out.splitlines():
        key, _, value = line.partition(" ")
        if key in wanted:
            values[key] = value.strip()
    return values


def _parse_who(output: str) -> list[dict]:
    rows = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        remote = parts[-1].strip("()") if parts[-1].startswith("(") else ""
        rows.append({"user": parts[0], "terminal": parts[1], "login_at": " ".join(parts[2:4]), "remote": remote})
    return rows


def _parse_last(output: str) -> list[dict]:
    rows = []
    for line in output.splitlines():
        if not line.strip() or line.startswith(("wtmp begins", "btmp begins")):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        rows.append({"user": parts[0], "terminal": parts[1], "remote": parts[2], "detail": " ".join(parts[3:])[:300]})
    return rows


def _render_config(payload: SSHConfigRequest) -> str:
    yes_no = lambda value: "yes" if value else "no"
    return "\n".join([
        "# Managed by OpsCenter. Manual changes may be overwritten.",
        f"Port {payload.port}",
        f"PermitRootLogin {payload.permit_root_login}",
        f"PasswordAuthentication {yes_no(payload.password_authentication)}",
        f"PubkeyAuthentication {yes_no(payload.pubkey_authentication)}",
        f"MaxAuthTries {payload.max_auth_tries}",
        f"ClientAliveInterval {payload.client_alive_interval}",
        "",
    ])


def _write_dropin(server: Server, content: Optional[str]) -> None:
    if content is None:
        _must_run(server, f"sudo -n rm -f {shlex.quote(_DROPIN)}")
        return
    encoded = base64.b64encode(content.encode()).decode()
    temp = f"{_DROPIN}.tmp"
    _must_run(server, (
        "sudo -n install -d -m 755 /etc/ssh/sshd_config.d && "
        f"printf %s {shlex.quote(encoded)} | base64 -d | sudo -n tee {shlex.quote(temp)} >/dev/null && "
        f"sudo -n chmod 600 {shlex.quote(temp)} && sudo -n mv {shlex.quote(temp)} {shlex.quote(_DROPIN)}"
    ))


@router.get("/servers/{server_id}/ssh/overview", dependencies=[Depends(get_current_user)])
def ssh_overview(server_id: str):
    server = _server(server_id)
    name, status = _service(server)
    sessions = _parse_who(_must_run(server, "who --ips 2>/dev/null || who"))
    return {"service": name, "status": status, "configured_port": server.ssh_port or 22, "effective": _effective_config(server), "session_count": len(sessions)}


@router.get("/servers/{server_id}/ssh/sessions", dependencies=[Depends(get_current_user)])
def ssh_sessions(server_id: str):
    server = _server(server_id)
    rows = _parse_who(_must_run(server, "who --ips 2>/dev/null || who"))
    return {"items": rows, "total": len(rows)}


@router.get("/servers/{server_id}/ssh/logins", dependencies=[Depends(get_current_user)])
def ssh_logins(server_id: str, limit: int = Query(100, ge=1, le=500)):
    server = _server(server_id)
    output = _must_run(server, f"last -Fai -n {limit}")
    rows = _parse_last(output)
    return {"items": rows[:limit], "total": len(rows[:limit])}


@router.get("/servers/{server_id}/ssh/authorized-keys", dependencies=[Depends(get_current_user)])
def authorized_keys(server_id: str, user: str = Query(..., min_length=1, max_length=32)):
    server = _server(server_id)
    username = _user(user)
    _path, _group, lines = _key_lines(server, username)
    items = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            items.append(_parse_key(line))
        except HTTPException:
            items.append({"type": "unknown", "fingerprint": "", "comment": "无法解析的密钥条目"})
    return {"user": username, "items": items, "total": len(items)}


@router.post("/servers/{server_id}/ssh/authorized-keys", dependencies=[Depends(get_current_user)], status_code=201)
def add_authorized_key(server_id: str, payload: AuthorizedKeyRequest):
    server = _server(server_id)
    username = _user(payload.user)
    key_line = " ".join(payload.public_key.strip().split())
    parsed = _parse_key(key_line)
    path, group, lines = _key_lines(server, username)
    existing = []
    for line in lines:
        try:
            existing.append(_parse_key(line).get("fingerprint"))
        except HTTPException:
            pass
    if parsed["fingerprint"] in existing:
        raise HTTPException(status_code=409, detail="该公钥已经存在")
    lines.append(key_line)
    _write_keys(server, username, path, group, lines)
    return {"ok": True, **parsed}


@router.post("/servers/{server_id}/ssh/authorized-keys/delete", dependencies=[Depends(get_current_user)])
def delete_authorized_key(server_id: str, payload: AuthorizedKeyDeleteRequest):
    server = _server(server_id)
    username = _user(payload.user)
    path, group, lines = _key_lines(server, username)
    kept, removed = [], False
    for line in lines:
        try:
            if _parse_key(line)["fingerprint"] == payload.fingerprint:
                removed = True
                continue
        except HTTPException:
            pass
        kept.append(line)
    if not removed:
        raise HTTPException(status_code=404, detail="未找到该公钥")
    _write_keys(server, username, path, group, kept)
    return {"ok": True, "fingerprint": payload.fingerprint}


@router.put("/servers/{server_id}/ssh/config", dependencies=[Depends(get_current_user)])
def update_ssh_config(server_id: str, payload: SSHConfigRequest):
    server = _server(server_id)
    if payload.confirm_name != server.name:
        raise HTTPException(status_code=400, detail="确认主机名称不匹配")
    if not payload.pubkey_authentication and not payload.password_authentication:
        raise HTTPException(status_code=400, detail="公钥和密码认证不能同时关闭")
    old_exists = _run(server, f"sudo -n test -f {shlex.quote(_DROPIN)}")[2] == 0
    old_content = _must_run(server, f"sudo -n cat {shlex.quote(_DROPIN)}") if old_exists else None
    content = _render_config(payload)
    _write_dropin(server, content)
    test_out, test_err, test_code = _run(server, "sudo -n sshd -t")
    if test_code != 0:
        _write_dropin(server, old_content)
        raise HTTPException(status_code=400, detail=f"sshd 配置校验失败，已回滚：{(test_err or test_out).strip()[-300:]}")
    service, _status = _service(server)
    if service == "unknown":
        _write_dropin(server, old_content)
        raise HTTPException(status_code=502, detail="无法识别 SSH systemd 服务，配置已回滚")
    reload_out, reload_err, reload_code = _run(server, f"sudo -n systemctl reload {service}")
    if reload_code != 0:
        _write_dropin(server, old_content)
        _run(server, f"sudo -n sshd -t && sudo -n systemctl reload {service}")
        raise HTTPException(status_code=502, detail=f"SSH 重载失败，已回滚：{(reload_err or reload_out).strip()[-300:]}")
    if int(server.ssh_port or 22) != payload.port:
        with get_db() as db:
            db.query(Server).filter(Server.id == server.id).update({Server.ssh_port: payload.port})
            db.commit()
    return {
        "ok": True, "service": service, "port": payload.port,
        "effective": {
            "port": str(payload.port), "permitrootlogin": payload.permit_root_login,
            "passwordauthentication": "yes" if payload.password_authentication else "no",
            "pubkeyauthentication": "yes" if payload.pubkey_authentication else "no",
            "maxauthtries": str(payload.max_auth_tries),
            "clientaliveinterval": str(payload.client_alive_interval),
        },
    }
