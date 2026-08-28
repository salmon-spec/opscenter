# -*- coding: utf-8 -*-
"""主机与服务操控（v3.29）：服务重启/启动/停止、主机重启/关机、服务日志。

安全约束：
- 仅对已登记 SSH 凭证的远程主机执行（ssh_manager.get_ssh_client 无凭证返回 None → 400）
- 本机（agent_type=local，即 OpsCenter 所在主机）禁止重启/关机，防止自毁
- 命令参数使用 shlex.quote 防注入；日志返回截断，避免超大响应
"""
from __future__ import annotations

import json
import re
import shlex
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.database import get_db
from app.models import Server, Service
from app.ssh_manager import get_ssh_client, ssh_exec

router = APIRouter(prefix="/api/v2", tags=["control"])

_VALID_SERVICE_ACTIONS = ("restart", "start", "stop")
_VALID_POWER_ACTIONS = ("reboot", "shutdown")
_VALID_CONTAINER_ACTIONS = ("start", "stop", "restart", "kill", "pause", "unpause", "remove")
_CONTAINER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SENSITIVE_ENV_RE = re.compile(r"(password|passwd|secret|token|api[_-]?key|private[_-]?key)", re.I)


class ServiceControlRequest(BaseModel):
    """服务操控请求体。"""
    action: str = Field(...)


class PowerRequest(BaseModel):
    """主机电源操控请求体。"""
    action: str = Field(...)


class ContainerActionRequest(BaseModel):
    """在单台主机上批量执行容器生命周期操作。"""
    container_ids: List[str] = Field(..., min_length=1, max_length=100)
    action: str = Field(...)
    force: bool = False


def _container_id(value: str) -> str:
    value = (value or "").strip()
    if not _CONTAINER_ID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail=f"非法容器标识: {value[:40]}")
    return value


def _percent(value: Any) -> float:
    try:
        return round(float(str(value or "0").replace("%", "").strip()), 2)
    except (TypeError, ValueError):
        return 0.0


def _docker_cpu_percent(stats: Dict[str, Any]) -> float:
    cpu = stats.get("cpu_stats") or {}
    pre = stats.get("precpu_stats") or {}
    cpu_delta = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (pre.get("cpu_usage") or {}).get("total_usage", 0)
    system_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    online = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    return round((cpu_delta / system_delta) * online * 100, 2) if cpu_delta > 0 and system_delta > 0 else 0.0


def _docker_memory(stats: Dict[str, Any]) -> tuple[int, int, float]:
    memory = stats.get("memory_stats") or {}
    usage = int(memory.get("usage") or 0)
    cache = int((memory.get("stats") or {}).get("inactive_file") or 0)
    used = max(0, usage - cache)
    limit = int(memory.get("limit") or 0)
    return used, limit, round(used / limit * 100, 2) if limit else 0.0


def _container_summary(attrs: Dict[str, Any], stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = attrs.get("Config") or {}
    state_data = attrs.get("State") or {}
    network_data = attrs.get("NetworkSettings") or {}
    host_config = attrs.get("HostConfig") or {}
    networks = network_data.get("Networks") or {}
    ports = []
    for private_port, bindings in (network_data.get("Ports") or {}).items():
        if bindings:
            for binding in bindings:
                ports.append({
                    "private": private_port,
                    "host_ip": binding.get("HostIp") or "0.0.0.0",
                    "host_port": binding.get("HostPort") or "",
                })
        else:
            ports.append({"private": private_port, "host_ip": "", "host_port": ""})
    image = config.get("Image") or attrs.get("Image") or ""
    names = attrs.get("Name") or ""
    memory_usage = memory_limit = 0
    cpu_percent = memory_percent = 0.0
    if stats:
        cpu_percent = _docker_cpu_percent(stats)
        memory_usage, memory_limit, memory_percent = _docker_memory(stats)
    return {
        "id": attrs.get("Id") or "",
        "short_id": (attrs.get("Id") or "")[:12],
        "name": str(names).lstrip("/"),
        "image": image,
        "state": state_data.get("Status") or "unknown",
        "status": state_data.get("Status") or "unknown",
        "health": (state_data.get("Health") or {}).get("Status") or "none",
        "created_at": attrs.get("Created") or "",
        "cpu_percent": cpu_percent,
        "memory_usage": memory_usage,
        "memory_limit": memory_limit,
        "memory_percent": memory_percent,
        "ip_addresses": [v.get("IPAddress") for v in networks.values() if v.get("IPAddress")],
        "networks": list(networks.keys()),
        "ports": ports,
        "mounts": [{"type": m.get("Type"), "source": m.get("Source"), "destination": m.get("Destination"), "rw": m.get("RW")} for m in attrs.get("Mounts") or []],
        "restart_policy": (host_config.get("RestartPolicy") or {}).get("Name") or "no",
    }


def _remote_container_rows(client) -> List[Dict[str, Any]]:
    out, err, code = ssh_exec(client, "docker ps -aq --no-trunc", timeout=20)
    if code != 0:
        raise HTTPException(status_code=502, detail=f"读取远程容器失败: {(err or out).strip()[-300:]}")
    identifiers = [_container_id(line) for line in out.splitlines() if line.strip()]
    if not identifiers:
        return []
    quoted = " ".join(shlex.quote(item) for item in identifiers)
    inspect_out, inspect_err, inspect_code = ssh_exec(client, f"docker inspect {quoted}", timeout=40)
    if inspect_code != 0:
        raise HTTPException(status_code=502, detail=f"读取容器详情失败: {(inspect_err or inspect_out).strip()[-300:]}")
    try:
        inspected = json.loads(inspect_out)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"远程 Docker 返回无效数据: {exc}")

    stats_by_name: Dict[str, Dict[str, Any]] = {}
    stats_out, _stats_err, stats_code = ssh_exec(
        client,
        "docker stats --no-stream --format '{{json .}}' 2>/dev/null",
        timeout=40,
    )
    if stats_code == 0:
        for line in stats_out.splitlines():
            try:
                row = json.loads(line)
                stats_by_name[row.get("Name") or row.get("Container")] = row
            except json.JSONDecodeError:
                continue
    rows = []
    for attrs in inspected:
        row = _container_summary(attrs)
        stat = stats_by_name.get(row["name"]) or stats_by_name.get(row["short_id"]) or {}
        row["cpu_percent"] = _percent(stat.get("CPUPerc"))
        row["memory_percent"] = _percent(stat.get("MemPerc"))
        row["memory_usage_display"] = stat.get("MemUsage") or ""
        row["network_io"] = stat.get("NetIO") or ""
        row["block_io"] = stat.get("BlockIO") or ""
        row["pids"] = stat.get("PIDs") or ""
        rows.append(row)
    return rows


def _load_container_rows(server: Server) -> List[Dict[str, Any]]:
    if server.agent_type == "local":
        try:
            import docker
            client = docker.from_env()
            rows = []
            for container in client.containers.list(all=True):
                stats = None
                if (container.attrs.get("State") or {}).get("Status") == "running":
                    try:
                        stats = container.stats(stream=False)
                    except Exception:
                        stats = None
                rows.append(_container_summary(container.attrs, stats))
            return rows
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"读取本机容器失败: {exc}")
    client = get_ssh_client(server)
    if client is None:
        raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证或连接失败")
    try:
        return _remote_container_rows(client)
    finally:
        client.close()


def _server_or_404(db, server_id: str) -> Server:
    try:
        uid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="主机不存在")
    server = db.query(Server).filter(Server.id == uid).first()
    if not server:
        raise HTTPException(status_code=404, detail="主机不存在")
    return server


def _resolve_service_target(svc: Service) -> tuple:
    """识别服务部署方式，返回 (命令前缀, 目标名)。

    docker 服务：container_name；systemd 服务：服务名作为单元名。
    """
    if svc.container_name:
        return "docker", svc.container_name
    if svc.deploy_type == "systemd" or (not svc.container_name and svc.source in ("agent", "manual")):
        return "systemctl", svc.name
    raise HTTPException(status_code=400, detail=f"服务 {svc.name} 未识别部署方式（缺容器名或 systemd 单元），无法操控")


@router.post("/services/{service_id}/control", dependencies=[Depends(get_current_user)])
def control_service(service_id: str, payload: ServiceControlRequest):
    """服务重启/启动/停止（docker 或 systemd）。"""
    if payload.action not in _VALID_SERVICE_ACTIONS:
        raise HTTPException(status_code=400, detail="action 仅支持 restart / start / stop")
    try:
        uid = uuid.UUID(service_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="服务不存在")

    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uid).first()
        if not svc:
            raise HTTPException(status_code=404, detail="服务不存在")
        server = db.query(Server).filter(Server.id == svc.server_id).first()
        if not server:
            raise HTTPException(status_code=404, detail="所属主机不存在")

        kind, target = _resolve_service_target(svc)

        if server.agent_type == "local":
            # 本机：使用 docker SDK 直接操作容器
            if kind != "docker":
                raise HTTPException(status_code=400, detail="本机 systemd 服务操控暂不支持，请在主机上执行")
            try:
                import docker
                container = docker.from_env().containers.get(target)
                {"start": container.start, "stop": container.stop, "restart": container.restart}[payload.action]()
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"本机操作容器失败: {e}")
            return {"ok": True, "action": payload.action, "service": svc.name, "method": "docker-local"}

        client = get_ssh_client(server)
        if client is None:
            raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证")
        try:
            if kind == "docker":
                cmd = f"docker {payload.action} {shlex.quote(target)}"
            else:
                cmd = f"systemctl {payload.action} {shlex.quote(target)}"
            out, err, code = ssh_exec(client, cmd, timeout=30)
            if code != 0:
                raise HTTPException(status_code=500, detail=f"命令执行失败: {(err or out).strip()[-300:]}")
            return {"ok": True, "action": payload.action, "service": svc.name, "method": "ssh", "output": out.strip()[-300:]}
        finally:
            client.close()


@router.post("/servers/{server_id}/power", dependencies=[Depends(get_current_user)])
def power_server(server_id: str, payload: PowerRequest):
    """主机重启/关机（仅远程已登记 SSH 凭证主机；本机禁止）。"""
    if payload.action not in _VALID_POWER_ACTIONS:
        raise HTTPException(status_code=400, detail="action 仅支持 reboot / shutdown")
    try:
        uid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="主机不存在")

    with get_db() as db:
        server = db.query(Server).filter(Server.id == uid).first()
        if not server:
            raise HTTPException(status_code=404, detail="主机不存在")
        if server.agent_type == "local":
            raise HTTPException(
                status_code=400,
                detail="本机为 OpsCenter 所在主机，禁止远程重启/关机（请在主机控制台操作）",
            )
        client = get_ssh_client(server)
        if client is None:
            raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证")
        try:
            cmd = "reboot" if payload.action == "reboot" else "poweroff"
            out, err, _code = ssh_exec(client, cmd, timeout=10)
            # reboot/poweroff 执行后 SSH 通道随即断开，exit_code 非 0 属正常
            return {
                "ok": True,
                "action": payload.action,
                "server": server.name,
                "detail": (err or out).strip()[-200:] or "已发送指令，主机即将离线",
            }
        finally:
            try:
                client.close()
            except Exception:
                pass


@router.get("/services/{service_id}/logs", dependencies=[Depends(get_current_user)])
def get_service_logs(
    service_id: str,
    lines: int = Query(100, ge=1, le=2000, description="返回日志行数"),
):
    """服务日志（docker logs --tail / journalctl -n）。"""
    try:
        uid = uuid.UUID(service_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="服务不存在")

    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uid).first()
        if not svc:
            raise HTTPException(status_code=404, detail="服务不存在")
        server = db.query(Server).filter(Server.id == svc.server_id).first()
        if not server:
            raise HTTPException(status_code=404, detail="所属主机不存在")

        kind, target = _resolve_service_target(svc)

        if server.agent_type == "local":
            if kind != "docker":
                raise HTTPException(status_code=400, detail="本机 systemd 日志读取暂不支持，请在主机上执行 journalctl")
            try:
                import docker
                logs = (
                    docker.from_env()
                    .containers.get(target)
                    .logs(tail=lines)
                    .decode("utf-8", errors="replace")
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"读取本机容器日志失败: {e}")
            return {"service": svc.name, "logs": logs}

        client = get_ssh_client(server)
        if client is None:
            raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证")
        try:
            if kind == "docker":
                cmd = f"docker logs --tail {int(lines)} {shlex.quote(target)} 2>&1"
            else:
                cmd = f"journalctl -u {shlex.quote(target)} -n {int(lines)} --no-pager 2>&1"
            out, err, code = ssh_exec(client, cmd, timeout=20)
            if code != 0 and not out.strip():
                raise HTTPException(status_code=500, detail=f"读取日志失败: {err.strip()[-300:]}")
            return {"service": svc.name, "logs": out}
        finally:
            client.close()


# === Container workbench (v4.1) ===

@router.get("/servers/{server_id}/containers", dependencies=[Depends(get_current_user)])
def list_server_containers(
    server_id: str,
    status: str = Query("all", pattern="^(all|running|paused|stopped)$"),
    search: str = Query("", max_length=120),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """返回一台主机的容器清单与实时资源数据。"""
    with get_db() as db:
        server = _server_or_404(db, server_id)
        rows = _load_container_rows(server)
        services = db.query(Service).filter(Service.server_id == server.id).all()
        services_by_container = {s.container_name: {"id": str(s.id), "name": s.name} for s in services if s.container_name}

    stopped_states = {"created", "exited", "dead", "removing"}
    if status != "all":
        if status == "stopped":
            rows = [row for row in rows if row.get("state") in stopped_states]
        else:
            rows = [row for row in rows if row.get("state") == status]
    needle = search.strip().lower()
    if needle:
        rows = [row for row in rows if needle in f"{row.get('name', '')} {row.get('image', '')}".lower()]
    for row in rows:
        row["service"] = services_by_container.get(row.get("name"))
    rows.sort(key=lambda row: (row.get("state") != "running", row.get("name", "").lower()))
    total = len(rows)
    start = (page - 1) * page_size
    return {"items": rows[start:start + page_size], "total": total, "page": page, "page_size": page_size}


@router.get("/servers/{server_id}/containers/{container_id}/inspect", dependencies=[Depends(get_current_user)])
def inspect_server_container(server_id: str, container_id: str):
    """返回容器详情；敏感环境变量值会被遮蔽。"""
    target = _container_id(container_id)
    with get_db() as db:
        server = _server_or_404(db, server_id)
        if server.agent_type == "local":
            try:
                import docker
                attrs = docker.from_env().containers.get(target).attrs
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"读取本机容器详情失败: {exc}")
        else:
            client = get_ssh_client(server)
            if client is None:
                raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证或连接失败")
            try:
                out, err, code = ssh_exec(client, f"docker inspect {shlex.quote(target)}", timeout=30)
                if code != 0:
                    raise HTTPException(status_code=502, detail=f"读取容器详情失败: {(err or out).strip()[-300:]}")
                attrs = json.loads(out)[0]
            except (json.JSONDecodeError, IndexError) as exc:
                raise HTTPException(status_code=502, detail=f"容器详情格式错误: {exc}")
            finally:
                client.close()
    summary = _container_summary(attrs)
    environment = []
    for item in (attrs.get("Config") or {}).get("Env") or []:
        key, sep, value = item.partition("=")
        environment.append({"key": key, "value": "••••••••" if _SENSITIVE_ENV_RE.search(key) else (value if sep else "")})
    summary.update({
        "command": (attrs.get("Config") or {}).get("Cmd") or [],
        "entrypoint": (attrs.get("Config") or {}).get("Entrypoint") or [],
        "environment": environment,
        "labels": (attrs.get("Config") or {}).get("Labels") or {},
        "working_dir": (attrs.get("Config") or {}).get("WorkingDir") or "",
    })
    return summary


@router.get("/servers/{server_id}/containers/{container_id}/logs", dependencies=[Depends(get_current_user)])
def get_container_logs(
    server_id: str,
    container_id: str,
    lines: int = Query(200, ge=1, le=5000),
):
    target = _container_id(container_id)
    with get_db() as db:
        server = _server_or_404(db, server_id)
        if server.agent_type == "local":
            try:
                import docker
                content = docker.from_env().containers.get(target).logs(tail=lines, timestamps=True).decode("utf-8", errors="replace")
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"读取本机容器日志失败: {exc}")
        else:
            client = get_ssh_client(server)
            if client is None:
                raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证或连接失败")
            try:
                out, err, code = ssh_exec(client, f"docker logs --timestamps --tail {int(lines)} {shlex.quote(target)} 2>&1", timeout=30)
                if code != 0 and not out.strip():
                    raise HTTPException(status_code=502, detail=f"读取容器日志失败: {err.strip()[-300:]}")
                content = out
            finally:
                client.close()
    return {"container_id": target, "logs": content[-2_000_000:]}


@router.post("/servers/{server_id}/containers/actions", dependencies=[Depends(get_current_user)])
def operate_server_containers(server_id: str, payload: ContainerActionRequest):
    """在当前主机执行批量操作，逐项返回结果，避免部分失败覆盖成功项。"""
    if payload.action not in _VALID_CONTAINER_ACTIONS:
        raise HTTPException(status_code=400, detail="不支持的容器操作")
    targets = list(dict.fromkeys(_container_id(item) for item in payload.container_ids))
    results = []
    with get_db() as db:
        server = _server_or_404(db, server_id)
        if server.agent_type == "local":
            try:
                import docker
                client = docker.from_env()
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"连接本机 Docker 失败: {exc}")
            for target in targets:
                try:
                    container = client.containers.get(target)
                    if payload.action == "remove":
                        container.remove(force=payload.force)
                    elif payload.action == "stop":
                        container.stop(timeout=10)
                    elif payload.action == "restart":
                        container.restart(timeout=10)
                    else:
                        getattr(container, payload.action)()
                    results.append({"container_id": target, "ok": True})
                except Exception as exc:
                    results.append({"container_id": target, "ok": False, "error": str(exc)[-300:]})
        else:
            client = get_ssh_client(server)
            if client is None:
                raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证或连接失败")
            try:
                for target in targets:
                    if payload.action == "remove":
                        command = "docker rm " + ("--force " if payload.force else "") + shlex.quote(target)
                    else:
                        command = f"docker {payload.action} {shlex.quote(target)}"
                    out, err, code = ssh_exec(client, command, timeout=45)
                    results.append({
                        "container_id": target,
                        "ok": code == 0,
                        "output": out.strip()[-300:],
                        "error": "" if code == 0 else (err or out).strip()[-300:],
                    })
            finally:
                client.close()
    return {"ok": all(item["ok"] for item in results), "action": payload.action, "results": results}


@router.post("/servers/{server_id}/containers/prune", dependencies=[Depends(get_current_user)])
def prune_server_containers(server_id: str):
    """清理当前主机的已停止容器。"""
    with get_db() as db:
        server = _server_or_404(db, server_id)
        if server.agent_type == "local":
            try:
                import docker
                result = docker.from_env().containers.prune()
                return {
                    "ok": True,
                    "deleted": result.get("ContainersDeleted") or [],
                    "space_reclaimed": int(result.get("SpaceReclaimed") or 0),
                }
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"清理本机容器失败: {exc}")
        client = get_ssh_client(server)
        if client is None:
            raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证或连接失败")
        try:
            out, err, code = ssh_exec(client, "docker container prune --force", timeout=60)
            if code != 0:
                raise HTTPException(status_code=502, detail=f"清理容器失败: {(err or out).strip()[-300:]}")
            return {"ok": True, "output": out.strip()[-2000:]}
        finally:
            client.close()
