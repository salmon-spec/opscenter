"""Lightweight per-host system summary and process management for v4.2."""
from __future__ import annotations

import os
import signal as signal_module
import subprocess
import time
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.agent_manager import fetch_agent_metrics, fetch_agent_processes, fetch_agent_system_summary
from app.auth import get_current_user
from app.config import PREVIEW_MODE
from app.database import get_db
from app.models import Server
from app.ssh_manager import collect_remote_metrics, get_ssh_client, ssh_exec

router = APIRouter(prefix="/api/v2", tags=["system"])
_SUMMARY_CACHE: Dict[str, Dict[str, Any]] = {}
_VALID_SIGNALS = {
    "TERM": getattr(signal_module, "SIGTERM", 15),
    "KILL": getattr(signal_module, "SIGKILL", 9),
    "STOP": getattr(signal_module, "SIGSTOP", 19),
    "CONT": getattr(signal_module, "SIGCONT", 18),
}


class ProcessSignalRequest(BaseModel):
    signal: str = "TERM"


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


def _normalize(data: Dict[str, Any], server_id: str, source: str) -> Dict[str, Any]:
    interfaces = data.get("network_interfaces") or []
    rx_rate = data.get("net_rx_rate")
    tx_rate = data.get("net_tx_rate")
    if rx_rate is None:
        rx_rate = sum(float(item.get("rx_rate_mbps") or 0) for item in interfaces) * 1_000_000 / 8
    if tx_rate is None:
        tx_rate = sum(float(item.get("tx_rate_mbps") or 0) for item in interfaces) * 1_000_000 / 8
    return {
        "server_id": server_id,
        "source": source,
        "timestamp": data.get("timestamp") or time.time(),
        "agent_version": data.get("agent_version") or "",
        "hostname": data.get("hostname") or "",
        "platform": data.get("platform") or "",
        "kernel": data.get("kernel") or "",
        "metrics": {
            "cpu": data.get("cpu_percent", data.get("cpu", 0)),
            "cpu_count": data.get("cpu_count", 0),
            "memory": data.get("memory_percent", data.get("memory", 0)),
            "memory_total": data.get("memory_total", 0),
            "memory_used": data.get("memory_used", 0),
            "memory_avail": data.get("memory_available", data.get("memory_avail", 0)),
            "swap": data.get("swap_percent", data.get("swap", 0)),
            "swap_total": data.get("swap_total", 0),
            "swap_used": data.get("swap_used", 0),
            "disk": data.get("disk_percent", data.get("disk", 0)),
            "disk_total": data.get("disk_total", 0),
            "disk_used": data.get("disk_used", 0),
            "disk_avail": data.get("disk_avail", 0),
            "load1": data.get("load1", 0),
            "load5": data.get("load5", 0),
            "load15": data.get("load15", 0),
            "net_rx": rx_rate if rx_rate is not None else data.get("net_rx", 0),
            "net_tx": tx_rate if tx_rate is not None else data.get("net_tx", 0),
            "uptime": data.get("uptime", 0),
        },
        "disks": data.get("disks") or [],
        "disk_devices": data.get("disk_devices") or [],
        "network_interfaces": interfaces,
    }


@router.get("/servers/{server_id}/system/summary", dependencies=[Depends(get_current_user)])
def system_summary(server_id: str, refresh: bool = Query(False)):
    cached = _SUMMARY_CACHE.get(server_id)
    if cached and not refresh and time.time() - cached["time"] < 2:
        return {**cached["data"], "cached": True}
    server = _server(server_id)
    if PREVIEW_MODE:
        now = time.time()
        return _normalize({
            "timestamp": now, "agent_version": "2.4.0-preview", "hostname": server.name,
            "platform": "Linux preview (isolated)", "kernel": "6.8-preview", "cpu_percent": 18.6,
            "cpu_count": 8, "memory_percent": 42.3, "memory_total": 16 * 1024**3,
            "memory_used": int(6.77 * 1024**3), "disk_percent": 36.8,
            "disk_total": 256 * 1024**3, "disk_used": int(94.2 * 1024**3),
            "load1": 0.42, "load5": 0.36, "load15": 0.31, "uptime": 12 * 86400 + 4521,
            "network_interfaces": [{"interface": "eth0", "address": server.host, "rx_rate_mbps": 1.26, "tx_rate_mbps": 0.48, "rx_bytes": 8_193_928_112, "tx_bytes": 3_482_118_990}],
            "disks": [{"mountpoint": "/", "device": "/dev/sda1", "fstype": "ext4", "total": 256 * 1024**3, "used": int(94.2 * 1024**3), "percent": 36.8}],
        }, server_id, "preview") | {"cached": False}
    host = "127.0.0.1" if server.agent_type == "local" else server.host
    data = None
    source = "agent"
    if server.agent_status == "running":
        data = fetch_agent_system_summary(host, server.agent_port or 19100, server.agent_token or "")
        if data is None:
            data = fetch_agent_metrics(host, server.agent_port or 19100, server.agent_token or "")
            source = "agent-legacy"
    if data is None and server.agent_type != "local":
        client = get_ssh_client(server)
        if client:
            try:
                data = collect_remote_metrics(client)
                source = "ssh"
            finally:
                client.close()
    if data is None:
        raise HTTPException(status_code=502, detail="无法读取主机系统信息")
    response = _normalize(data, server_id, source)
    _SUMMARY_CACHE[server_id] = {"time": time.time(), "data": response}
    return {**response, "cached": False}


def _ssh_process_rows(server: Server, search: str, user: str, state: str, sort: str, limit: int):
    client = get_ssh_client(server)
    if not client:
        raise HTTPException(status_code=400, detail="主机未配置 SSH 凭证")
    sort_arg = "-%cpu" if sort == "cpu" else "-rss"
    try:
        out, err, code = ssh_exec(client, f"ps -eo pid=,ppid=,user=,comm=,%cpu=,%mem=,rss=,stat= --sort={sort_arg}", timeout=15)
        if code != 0:
            raise HTTPException(status_code=502, detail=(err or out).strip()[-300:])
    finally:
        client.close()
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 7)
        if len(parts) != 8 or parts[3] == "ps":
            continue
        row = {"pid": int(parts[0]), "ppid": int(parts[1]), "user": parts[2], "command": parts[3], "cpu_percent": float(parts[4]), "memory_percent": float(parts[5]), "rss_bytes": int(parts[6]) * 1024, "state": parts[7]}
        if search and search.lower() not in f"{row['pid']} {row['command']} {row['user']}".lower():
            continue
        if user and row["user"] != user:
            continue
        if state and not row["state"].startswith(state):
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


@router.get("/servers/{server_id}/processes", dependencies=[Depends(get_current_user)])
def list_processes(server_id: str, search: str = Query("", max_length=80), user: str = Query("", max_length=40), state: str = Query("", max_length=4), sort: str = Query("cpu", pattern="^(cpu|memory)$"), limit: int = Query(200, ge=1, le=500)):
    server = _server(server_id)
    if PREVIEW_MODE:
        rows = [
            {"pid": 1421, "ppid": 1, "user": "ops", "command": "uvicorn", "cpu_percent": 2.4, "memory_percent": 3.8, "rss_bytes": 652_214_272, "state": "S"},
            {"pid": 936, "ppid": 1, "user": "root", "command": "dockerd", "cpu_percent": 0.7, "memory_percent": 1.9, "rss_bytes": 326_107_136, "state": "S"},
            {"pid": 1882, "ppid": 936, "user": "root", "command": "containerd-shim", "cpu_percent": 0.2, "memory_percent": 0.6, "rss_bytes": 103_079_215, "state": "S"},
        ]
        needle = search.lower().strip()
        rows = [row for row in rows if (not needle or needle in f"{row['pid']} {row['command']} {row['user']}".lower()) and (not user or row["user"] == user) and (not state or row["state"].startswith(state))]
        rows.sort(key=lambda row: row["cpu_percent" if sort == "cpu" else "memory_percent"], reverse=True)
        return {"items": rows[:limit], "total": len(rows), "timestamp": time.time(), "source": "preview"}
    host = "127.0.0.1" if server.agent_type == "local" else server.host
    result = None
    if server.agent_status == "running":
        result = fetch_agent_processes(host, server.agent_port or 19100, server.agent_token or "", search=search, user=user, state=state, sort=sort, limit=limit)
    if result is not None:
        return {**result, "source": "agent"}
    if server.agent_type == "local":
        raise HTTPException(status_code=502, detail="本机 Agent 不支持进程列表，请升级至 2.4.0")
    rows = _ssh_process_rows(server, search, user, state, sort, limit)
    return {"items": rows, "total": len(rows), "timestamp": time.time(), "source": "ssh"}


def _process_command(server: Server, command: str, timeout: int = 10) -> str:
    if server.agent_type == "local":
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise HTTPException(status_code=502, detail=(result.stderr or result.stdout)[-300:])
        return result.stdout
    client = get_ssh_client(server)
    if not client:
        raise HTTPException(status_code=400, detail="主机未配置 SSH 凭证")
    try:
        out, err, code = ssh_exec(client, command, timeout=timeout)
        if code != 0:
            raise HTTPException(status_code=502, detail=(err or out).strip()[-300:])
        return out
    finally:
        client.close()


@router.get("/servers/{server_id}/processes/{pid}", dependencies=[Depends(get_current_user)])
def process_detail(server_id: str, pid: int):
    if pid <= 0:
        raise HTTPException(status_code=400, detail="非法 PID")
    server = _server(server_id)
    if PREVIEW_MODE:
        return {"pid": pid, "summary": f"{pid} 1 ops preview-process 0.2 0.6 S 1200 preview-process --safe", "network_connections": [f"tcp ESTAB 10.66.66.2:9091 10.66.66.5:5432 users:((preview-process,pid={pid},fd=8))"]}
    command = f"ps -p {pid} -o pid=,ppid=,user=,comm=,%cpu=,%mem=,rss=,stat=,etimes=,args=; printf '\\n--NET--\\n'; ss -tunap 2>/dev/null | grep 'pid={pid},' || true"
    out = _process_command(server, command)
    process_text, _, network_text = out.partition("--NET--")
    lines = [line.strip() for line in process_text.splitlines() if line.strip()]
    if not lines:
        raise HTTPException(status_code=404, detail="进程不存在")
    return {"pid": pid, "summary": lines[0], "network_connections": [line.strip() for line in network_text.splitlines() if line.strip()]}


@router.post("/servers/{server_id}/processes/{pid}/signal", dependencies=[Depends(get_current_user)])
def signal_process(server_id: str, pid: int, payload: ProcessSignalRequest):
    name = payload.signal.upper()
    if pid <= 2:
        raise HTTPException(status_code=400, detail="禁止操作系统关键进程")
    if name not in _VALID_SIGNALS:
        raise HTTPException(status_code=400, detail="仅支持 TERM / KILL / STOP / CONT")
    if PREVIEW_MODE:
        raise HTTPException(status_code=403, detail="隔离预览环境禁止发送进程信号")
    server = _server(server_id)
    if server.agent_type == "local":
        try:
            os.kill(pid, _VALID_SIGNALS[name])
        except ProcessLookupError:
            raise HTTPException(status_code=404, detail="进程不存在")
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有权限操作该进程")
    else:
        _process_command(server, f"kill -{name} {pid}")
    return {"ok": True, "pid": pid, "signal": name}
