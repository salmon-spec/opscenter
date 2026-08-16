# -*- coding: utf-8 -*-
"""主机与服务操控（v3.29）：服务重启/启动/停止、主机重启/关机、服务日志。

安全约束：
- 仅对已登记 SSH 凭证的远程主机执行（ssh_manager.get_ssh_client 无凭证返回 None → 400）
- 本机（agent_type=local，即 OpsCenter 所在主机）禁止重启/关机，防止自毁
- 命令参数使用 shlex.quote 防注入；日志返回截断，避免超大响应
"""
from __future__ import annotations

import shlex
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.database import get_db
from app.models import Server, Service
from app.ssh_manager import get_ssh_client, ssh_exec

router = APIRouter(prefix="/api/v2", tags=["control"])

_VALID_SERVICE_ACTIONS = ("restart", "start", "stop")
_VALID_POWER_ACTIONS = ("reboot", "shutdown")


class ServiceControlRequest(BaseModel):
    """服务操控请求体。"""
    action: str = Field(...)


class PowerRequest(BaseModel):
    """主机电源操控请求体。"""
    action: str = Field(...)


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
