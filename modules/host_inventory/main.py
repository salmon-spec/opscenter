from datetime import datetime, timezone
import os
import secrets
from pathlib import Path
import uuid
import json
from urllib.request import Request as URLRequest, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError

from fastapi import Depends, Header, HTTPException, Response, Request
from pydantic import BaseModel, Field

from collector import findings_provider, hosts_provider, summary_provider
from runtime.auth import ModuleAuth
from runtime.app import create_module_app, require_internal_service
from storage import HostInventoryStore

app = create_module_app(
    key="host-inventory",
    version="0.2.9",
    summary_provider=summary_provider,
    findings_provider=findings_provider,
    capabilities=["summary:read", "findings:read", "hosts:read", "hosts:create", "hosts:update", "hosts:archive"],
    readiness_provider=HostInventoryStore().ping,
)
auth = ModuleAuth(HostInventoryStore)
auth.register(app)


class HostCreate(BaseModel):
    registration_id: uuid.UUID | None = None
    auto_deploy_agent: bool = True
    ssh_user: str | None = Field(None, min_length=1, max_length=50)
    ssh_password: str | None = Field(None, max_length=1000)
    ssh_key: str | None = Field(None, max_length=16000)
    name: str = Field(min_length=1, max_length=50)
    host: str = Field(min_length=1, max_length=253)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    tags: list[str] = Field(default_factory=list, max_length=30)
    remark: str = Field(default="", max_length=500)
    lan_ip: str = Field(default="", max_length=253)
    wireguard_ip: str = Field(default="", max_length=253)
    preferred_management_channel: str = Field(default="auto", pattern=r"^(auto|lan|wireguard)$")
    management_address_override: str = Field(default="", max_length=253)
    cluster_id: str = Field(default="", max_length=100)
    kubernetes_node_name: str = Field(default="", max_length=253)
    node_role: str = Field(default="", max_length=100)
    runtime_type: str = Field(default="", max_length=100)


class AgentTelemetry(BaseModel):
    registration: dict | None = None
    id: uuid.UUID
    status: str = Field(default="unknown", pattern=r"^(online|offline|unknown)$")
    docker_available: bool = False
    is_local: bool = False
    agent_status: str = Field(default="unknown", max_length=20)
    agent_type: str = Field(default="remote", pattern=r"^(remote|local)$")
    last_seen: datetime | None = None
    agent_version: str = Field(default="", max_length=40)
    log_agent_status: str = Field(default="unknown", max_length=20)
    log_agent_version: str = Field(default="", max_length=40)


def _require_ingest_token(value: str) -> None:
    path = os.getenv("HOST_INVENTORY_INGEST_TOKEN_FILE", "")
    try:
        expected = Path(path).read_text(encoding="utf-8").strip() if path else ""
    except OSError:
        expected = ""
    if not expected or not value or not secrets.compare_digest(expected, value):
        raise HTTPException(401, "未授权")


@app.on_event("startup")
def initialize_module_storage():
    if os.path.isfile(os.getenv("HOST_INVENTORY_DATABASE_URL_FILE", "/run/secrets/host_inventory_db_url")):
        HostInventoryStore().ensure_schema()
        auth.ensure_schema()


@app.get("/api/v1/hosts")
def list_hosts():
    try:
        return hosts_provider()
    except Exception:
        return {
            "status": "unavailable",
            "collected_at": datetime.now(timezone.utc),
            "items": [],
            "error": "主机清单数据源暂不可用",
        }


@app.get("/api/v1/internal/hosts", dependencies=[Depends(require_internal_service)])
def internal_hosts():
    result = hosts_provider()
    return {key: result.get(key) for key in ("status", "collected_at", "error")} | {
        "items": [{key: host.get(key) for key in (
            "id", "name", "status", "agent_status", "last_seen", "telemetry_updated_at")}
                  for host in result.get("items", [])]
    }


@app.post("/api/v1/agent/telemetry", status_code=204)
def receive_agent_telemetry(
    payload: AgentTelemetry,
    token: str = Header(default="", alias="X-OpsCenter-Module-Token"),
):
    _require_ingest_token(token)
    if not HostInventoryStore().update_agent_telemetry(payload.model_dump(mode="json")):
        raise HTTPException(404, "主机不存在")
    return Response(status_code=204)


@app.get("/api/v1/internal/active-hosts")
def active_hosts(token: str = Header(default="", alias="X-OpsCenter-Module-Token")):
    _require_ingest_token(token)
    result = hosts_provider()
    if result.get("status") == "unavailable":
        raise HTTPException(503, "资产清单暂不可用")
    return {"status": "ok", "ids": [str(host["id"]) for host in result.get("items", [])]}


@app.get("/api/v1/internal/log-coverage", dependencies=[Depends(require_internal_service)])
def log_coverage():
    result = hosts_provider()
    if result.get("status") == "unavailable":
        raise HTTPException(503, "资产清单暂不可用")
    return {"status": "ok", "items": [{key: host.get(key) for key in ("id", "name", "log_agent_status")}
                                     for host in result.get("items", [])]}


@app.post("/api/v1/hosts", status_code=201)
def create_host(payload: HostCreate, request: Request, user=Depends(app.state.require_editor)):
    data = payload.model_dump(exclude={"registration_id", "auto_deploy_agent", "ssh_user", "ssh_password", "ssh_key"})
    if any(ord(char) < 32 for value in (data["name"], data["host"]) for char in value):
        raise HTTPException(400, "主机名称或地址包含无效字符")
    data["tags"] = [tag.strip()[:80] for tag in data["tags"] if tag.strip()]
    data["id"] = str(payload.registration_id or uuid.uuid4())
    register_management_host(data["id"], payload, request)
    HostInventoryStore().create_host(data, user["username"])
    return {"id": data["id"], "name": data["name"], "host": data["host"]}


@app.put("/api/v1/hosts/{host_id}")
def update_host(host_id: uuid.UUID, payload: HostCreate, request: Request, user=Depends(app.state.require_editor)):
    data = payload.model_dump(exclude={"registration_id", "auto_deploy_agent", "ssh_user", "ssh_password", "ssh_key"})
    if any(ord(char) < 32 for value in (data["name"], data["host"]) for char in value):
        raise HTTPException(400, "主机名称或地址包含无效字符")
    data["tags"] = [tag.strip()[:80] for tag in data["tags"] if tag.strip()]
    # The core owns connection settings; a failed core write never reports success.
    register_management_host(str(host_id), payload, request)
    if not HostInventoryStore().update_host(str(host_id), data, user["username"]):
        raise HTTPException(404, "主机不存在")
    return {"id": str(host_id), "name": data["name"], "host": data["host"]}


@app.post("/api/v1/hosts/{host_id}/archive")
def archive_host(host_id: uuid.UUID, user=Depends(app.state.require_editor)):
    if not HostInventoryStore().archive_host(str(host_id), user["username"]):
        raise HTTPException(404, "主机不存在")
    return {"ok": True}


def register_management_host(host_id: str, payload: HostCreate, request: Request):
    base = os.getenv("HOST_INVENTORY_SOURCE_URL", "http://backend:9091/api/v2/servers").rstrip("/")
    headers = {"Authorization": request.headers.get("authorization", ""), "Content-Type": "application/json"}
    opener = build_opener(ProxyHandler({}))

    def call(method, url, data=None):
        body = json.dumps(data).encode() if data is not None else None
        try:
            with opener.open(URLRequest(url, data=body, headers=headers, method=method), timeout=8) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code in {400, 401, 403, 404, 409, 422}:
                raise HTTPException(error.code, "主机管理资料未保存，请检查登录状态、地址和重复记录") from None
            raise HTTPException(502, "主机管理服务暂不可用，资料未完成保存") from None
        except (URLError, OSError, ValueError):
            raise HTTPException(502, "主机管理服务暂不可用，资料未完成保存") from None

    values = payload.model_dump(exclude={"registration_id"}, exclude_none=True)
    if not values.get("cluster_id"):
        values["cluster_id"] = None
    values = {key: value for key, value in values.items() if key not in {"ssh_password", "ssh_key"} or value}
    try:
        current = call("GET", base + "/" + host_id)
    except HTTPException as error:
        if error.status_code != 404:
            raise
        return call("POST", base, {**values, "id": host_id})
    if current.get("is_local") or current.get("agent_type") == "local":
        values = {key: value for key, value in values.items() if key not in {"host", "ssh_port", "ssh_user", "ssh_password", "ssh_key"}}
    return call("PUT", base + "/" + host_id, values)
