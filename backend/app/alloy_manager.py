"""Grafana Alloy lifecycle management for long-term host log collection."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import secrets
from urllib.parse import urlparse
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
import requests

from app.config import ALLOY_VERSION, LOKI_TIMEOUT_SECONDS, LOKI_URL
from app.database import get_db
from app.models import Server
from app.ssh_manager import get_ssh_client, ssh_exec


router = APIRouter(prefix="/api/v2", tags=["log-agents"])
_CONFIG_SOURCE = Path(__file__).resolve().parents[2] / "deploy" / "observability" / "alloy.example.alloy"
_DEB_SHA256 = {
    "amd64": "6ba1cfba4e9de4d3cbc94eaf8cdeb769898fcbfae12e8c2fea39b178ecd05f52",
    "arm64": "e49dcc40d28d121668a4ed0156be76cd315c0574d31e724fcf6616ce5c8bb801",
}


def _version(output: str) -> str:
    match = re.search(r"(?:alloy,?\s+version|version)\s+v?(\d+\.\d+\.\d+)", output, re.IGNORECASE)
    return match.group(1) if match else ""


def _systemd_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("\r", " ").replace("\n", " ")


def _push_url() -> str:
    parsed = urlparse(LOKI_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请先配置有效的 LOKI_URL")
    return f"{LOKI_URL}/loki/api/v1/push"


def _probe_ingestion(server_id: str, now: datetime) -> dict:
    """Read one recent log entry for a host; called only by an explicit UI probe."""
    try:
        response = requests.get(f"{LOKI_URL}/loki/api/v1/query_range", params={
            "query": f'{{server_id="{server_id}"}}',
            "start": str(int((now - timedelta(hours=24)).replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)),
            "end": str(int(now.replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)),
            "limit": 1,
            "direction": "backward",
        }, timeout=LOKI_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "error":
            raise ValueError(payload.get("error", "Loki query failed"))
        timestamps = [
            int(value[0]) for stream in payload.get("data", {}).get("result", [])
            for value in stream.get("values", []) if value
        ]
        if not timestamps:
            return {"ingestion_status": "no_data", "last_log_at": None, "age_seconds": None}
        latest = max(timestamps)
        last_at = datetime.fromtimestamp(latest / 1_000_000_000, timezone.utc).replace(tzinfo=None)
        age = max(0, int((now - last_at).total_seconds()))
        return {
            "ingestion_status": "fresh" if age <= 600 else "stale",
            "last_log_at": last_at.isoformat() + "Z", "age_seconds": age,
        }
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        return {"ingestion_status": "error", "last_log_at": None, "age_seconds": None, "probe_error": str(exc)[:500]}


def _upload(client, content: str, destination: str, mode: str, sudo: str) -> tuple[bool, str]:
    temporary = f"/tmp/opscenter-alloy-{secrets.token_hex(5)}"
    sftp = client.open_sftp()
    try:
        with sftp.file(temporary, "w") as remote:
            remote.write(content)
        sftp.chmod(temporary, 0o600)
    finally:
        sftp.close()
    _, error, code = ssh_exec(client, f"{sudo}install -D -m {mode} {temporary} {destination} && rm -f {temporary}", timeout=30)
    return code == 0, error.strip()


def check_alloy_status(server: Server) -> dict:
    if server.agent_type == "local":
        return {"status": "unknown", "message": "本机 Alloy 由中心节点部署流程管理"}
    client = get_ssh_client(server)
    if not client:
        return {"status": "error", "message": "SSH 连接失败"}
    try:
        active, _, _ = ssh_exec(client, "systemctl is-active alloy 2>/dev/null || true")
        output, _, _ = ssh_exec(client, "alloy --version 2>/dev/null || true")
        version = _version(output)
        if active.strip() == "active":
            return {"status": "running", "version": version, "message": "Alloy 运行中"}
        if version:
            return {"status": "stopped", "version": version, "message": "Alloy 已安装但未运行"}
        return {"status": "not_deployed", "version": "", "message": "Alloy 未安装"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)[:500]}
    finally:
        client.close()


def deploy_alloy(server: Server) -> dict:
    """Install a checksum-pinned Alloy package and host-scoped collection config."""
    if server.agent_type == "local":
        return {"success": False, "message": "本机 Alloy 请通过中心观测栈部署"}
    try:
        push_url = _push_url()
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    if not _CONFIG_SOURCE.is_file():
        return {"success": False, "message": "Alloy 配置模板不存在"}
    client = get_ssh_client(server)
    if not client:
        return {"success": False, "message": "SSH 连接失败"}
    sudo = "" if server.ssh_user == "root" else "sudo -n "
    try:
        arch_out, _, _ = ssh_exec(client, "dpkg --print-architecture 2>/dev/null || true")
        arch = arch_out.strip()
        checksum = _DEB_SHA256.get(arch)
        if not checksum:
            return {"success": False, "message": f"当前仅支持 Debian/Ubuntu amd64/arm64，检测到 {arch or '未知架构'}"}
        package = f"/tmp/alloy-{ALLOY_VERSION}-{arch}.deb"
        url = f"https://github.com/grafana/alloy/releases/download/v{ALLOY_VERSION}/alloy-{ALLOY_VERSION}-1.{arch}.deb"
        command = (
            f"command -v curl >/dev/null && curl -fsSL --retry 3 -o {package} {url} && "
            f"echo '{checksum}  {package}' | sha256sum -c - && {sudo}dpkg -i {package} && rm -f {package}"
        )
        _, error, code = ssh_exec(client, command, timeout=180)
        if code != 0:
            return {"success": False, "message": f"Alloy 安装失败：{error[-500:]}"}
        ok, message = _upload(client, _CONFIG_SOURCE.read_text(encoding="utf-8"), "/etc/alloy/config.alloy", "0644", sudo)
        if not ok:
            return {"success": False, "message": f"Alloy 配置写入失败：{message[-500:]}"}
        dropin = "\n".join([
            "[Service]",
            f'Environment="OPSCENTER_SERVER_ID={server.id}"',
            f'Environment="OPSCENTER_HOST_NAME={_systemd_escape(server.name)}"',
            f'Environment="LOKI_PUSH_URL={_systemd_escape(push_url)}"',
            "",
        ])
        ok, message = _upload(client, dropin, "/etc/systemd/system/alloy.service.d/opscenter.conf", "0644", sudo)
        if not ok:
            return {"success": False, "message": f"Alloy 环境写入失败：{message[-500:]}"}
        ssh_exec(client, f"{sudo}sh -c 'for g in adm systemd-journal docker; do getent group \"$g\" >/dev/null && usermod -aG \"$g\" alloy || true; done'", timeout=30)
        _, error, code = ssh_exec(client, f"{sudo}systemctl daemon-reload && {sudo}systemctl enable --now alloy && {sudo}systemctl restart alloy", timeout=60)
        if code != 0:
            return {"success": False, "message": f"Alloy 启动失败：{error[-500:]}"}
        active, _, _ = ssh_exec(client, "systemctl is-active alloy")
        if active.strip() != "active":
            logs, _, _ = ssh_exec(client, "journalctl -u alloy --no-pager -n 20")
            return {"success": False, "message": f"Alloy 未激活：{logs[-500:]}"}
        return {"success": True, "status": "running", "version": ALLOY_VERSION, "message": "Alloy 部署成功"}
    except Exception as exc:
        return {"success": False, "message": f"Alloy 部署异常：{str(exc)[:500]}"}
    finally:
        client.close()


def _run(server_id: str, action: str) -> None:
    try:
        with get_db() as db:
            server = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
            if not server:
                return
            db.expunge(server)
        result = deploy_alloy(server) if action == "deploy" else check_alloy_status(server)
        with get_db() as db:
            row = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
            if not row:
                return
            row.log_agent_status = result.get("status", "running" if result.get("success") else "error")
            row.log_agent_version = result.get("version", row.log_agent_version or "")
            row.log_agent_error = "" if row.log_agent_status == "running" else result.get("message", "")[-1000:]
            row.log_agent_checked_at = datetime.utcnow()
            db.commit()
    except Exception as exc:
        with get_db() as db:
            row = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
            if row:
                row.log_agent_status = "error"
                row.log_agent_error = str(exc)[-1000:]
                row.log_agent_checked_at = datetime.utcnow()
                db.commit()


def _schedule(server_id: str, action: str, background_tasks: BackgroundTasks) -> dict:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(404, "主机不存在")
    with get_db() as db:
        server = db.query(Server).filter(Server.id == server_uuid).first()
        if not server:
            raise HTTPException(404, "主机不存在")
        if server.agent_type == "local":
            raise HTTPException(400, "本地主机请通过中心观测栈部署 Alloy")
        if not server.ssh_key:
            raise HTTPException(400, "主机未配置 SSH 凭证")
        server.log_agent_status = "deploying" if action == "deploy" else "checking"
        server.log_agent_error = ""
        db.commit()
    background_tasks.add_task(_run, server_id, action)
    return {"accepted": True, "server_id": server_id, "action": action, "target_version": ALLOY_VERSION}


@router.get("/logs/agents/version")
def alloy_version():
    return {"current_version": ALLOY_VERSION, "loki_configured": bool(LOKI_URL)}


@router.get("/logs/agents/overview")
def alloy_overview(probe: bool = False):
    with get_db() as db:
        rows = db.query(Server).order_by(Server.name.asc(), Server.host.asc()).all()
        agents = [{
            "server_id": str(row.id), "name": row.name, "host": row.host,
            "is_local": bool(row.agent_type == "local" or row.is_local),
            "has_credentials": bool(row.ssh_key),
            "collector_status": row.log_agent_status or "unknown",
            "collector_version": row.log_agent_version or "",
            "collector_error": row.log_agent_error or "",
            "checked_at": row.log_agent_checked_at.isoformat() + "Z" if row.log_agent_checked_at else None,
            "ingestion_status": "not_probed", "last_log_at": None, "age_seconds": None,
        } for row in rows]
    if probe and LOKI_URL and agents:
        now = datetime.utcnow()
        workers = min(8, len(agents))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="loki-ingestion") as pool:
            futures = {item["server_id"]: pool.submit(_probe_ingestion, item["server_id"], now) for item in agents}
            for item in agents:
                item.update(futures[item["server_id"]].result())
    elif probe and not LOKI_URL:
        for item in agents:
            item.update({"ingestion_status": "error", "probe_error": "Loki 尚未配置"})

    for item in agents:
        collector = item["collector_status"]
        ingestion = item["ingestion_status"]
        if collector == "error":
            item["diagnostic_level"] = "error"
            item["diagnostic"] = item["collector_error"] or "采集器异常"
        elif ingestion == "error":
            item["diagnostic_level"] = "error"
            item["diagnostic"] = item.get("probe_error") or "Loki 探测失败"
        elif collector == "running" and ingestion in {"stale", "no_data"}:
            item["diagnostic_level"] = "warning"
            item["diagnostic"] = "采集器运行中，但最近24小时没有新日志" if ingestion == "no_data" else "最近日志已超过10分钟"
        elif collector in {"running", "deploying", "checking"}:
            item["diagnostic_level"] = "ok" if collector == "running" else "info"
            item["diagnostic"] = "采集正常" if ingestion in {"fresh", "not_probed"} else "正在处理"
        elif item["is_local"]:
            item["diagnostic_level"] = "warning"
            item["diagnostic"] = "请部署中心观测栈"
        elif not item["has_credentials"]:
            item["diagnostic_level"] = "warning"
            item["diagnostic"] = "缺少 SSH 凭证，无法自动部署"
        else:
            item["diagnostic_level"] = "warning"
            item["diagnostic"] = "日志采集器未部署"

    total = len(agents)
    running = sum(item["collector_status"] == "running" for item in agents)
    fresh = sum(item["ingestion_status"] == "fresh" for item in agents)
    abnormal = sum(item["diagnostic_level"] in {"warning", "error"} for item in agents)
    return {
        "loki_configured": bool(LOKI_URL), "probed": probe,
        "total": total, "running": running, "fresh": fresh, "abnormal": abnormal,
        "coverage_percent": round(running * 100 / total, 1) if total else 0,
        "agents": agents,
    }


@router.post("/servers/{server_id}/logs/agent/check", status_code=202)
def check_agent(server_id: str, background_tasks: BackgroundTasks):
    return _schedule(server_id, "check", background_tasks)


@router.post("/servers/{server_id}/logs/agent/deploy", status_code=202)
def deploy_agent(server_id: str, background_tasks: BackgroundTasks):
    if not LOKI_URL:
        raise HTTPException(503, "请先配置 LOKI_URL")
    return _schedule(server_id, "deploy", background_tasks)


@router.post("/logs/agents/deploy-missing", status_code=202)
def deploy_missing(background_tasks: BackgroundTasks):
    if not LOKI_URL:
        raise HTTPException(503, "请先配置 LOKI_URL")
    with get_db() as db:
        targets = [str(server.id) for server in db.query(Server).filter(Server.agent_type != "local").all()
                   if server.ssh_key and server.log_agent_status != "running"]
        if targets:
            db.query(Server).filter(Server.id.in_([uuid.UUID(item) for item in targets])).update(
                {Server.log_agent_status: "deploying", Server.log_agent_error: ""}, synchronize_session=False,
            )
            db.commit()
    for server_id in targets:
        background_tasks.add_task(_run, server_id, "deploy")
    return {"accepted": len(targets), "server_ids": targets, "target_version": ALLOY_VERSION}
