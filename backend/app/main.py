import os
import calendar, uuid, asyncio, re, socket
from datetime import datetime, timedelta, date
from typing import Optional, List
from contextlib import contextmanager

from fastapi import FastAPI, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, UploadFile, File as FastAPIFile
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool

from app.models import Base, Server, Service, ServiceRelation, ServerStatus, ServiceStatus, ServiceSource, MetricHistory, NetworkStats, NetworkLatency, AlertRule, AlertEvent, AlertSilence, CertCheck, LogRule, LogMatch, BackupCheck, ImageStatus, DailyReport, AuditLog, ApiKey
from app.version import VERSION
from app.discovery import discover_docker_services, parse_nginx_config
from app.ssh_manager import get_ssh_client, ssh_exec, discover_remote_docker_services, collect_remote_metrics, get_remote_containers, test_ssh_connection
from app.agent_manager import deploy_agent, check_agent_status, fetch_agent_metrics, uninstall_agent, fetch_agent_services, trigger_agent_scan
from app.ssh_terminal import create_session, get_session, remove_session, get_active_count
from app.cert_scanner import cert_scan_loop, run_cert_scan, seed_cert_rule
from app.log_scanner import log_scan_loop, run_log_scan
from app.backup_scanner import backup_check_loop, run_backup_check, seed_backup_rule
from app.image_scanner import image_check_loop, run_image_check
from app.report_engine import generate_report, report_loop
from app.audit import AuditMiddleware
from app.alerting import (
    alerting_loop, retention_loop, seed_default_rules,
    run_alerting_cycle, retention_cleanup,
)
from app.config import RETENTION_METRIC_DAYS

# === v3.29 新增模块（T2 密钥 / T3 详情拓扑大屏 / 主机操控 / T4 服务健康） ===
from app.api_keys import router as api_keys_router
from app.topology import router as topology_router
from app.control import router as control_router
from app.service_health import run_service_health_cycle, service_health_loop
from app.plaza import router as plaza_router

class TerminalCreateRequest(BaseModel):
    server_id: str
    cols: int = 80
    rows: int = 24

# === Config ===
DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://opscenter:OpsCenter2026@127.0.0.1:5433/opscenter")
LOCAL_HOST = os.getenv("LOCAL_HOST", "101.200.91.229")
LOCAL_DOMAIN = os.getenv("LOCAL_DOMAIN", "ops.salmon.xin")

# Category -> Group ID auto-mapping for service grouping
CATEGORY_TO_GROUP = {
    "代码与CI/CD": "cicd",
    "CI/CD": "cicd",
    "监控与日志": "monitor",
    "监控": "monitor",
    "网络与代理": "network",
    "数据存储": "database",
    "消息与注册": "middleware",
    "自动化工作流": "auto_workflow",
    "自动化": "auto_workflow",
    "运维管理": "ops",
    "运维面板": "ops",
    "应用服务": "app",
    "文档工具": "app",
    "开发工具": "app",
    "数据平台": "app",
    "前端应用": "app",
    "安全与认证": "security",
}

DEFAULT_GROUPS = [
    {"id": "cicd", "name": "代码与CI/CD", "order": 10, "color": "#2dd4bf", "icon": "code"},
    {"id": "security", "name": "安全与认证", "order": 15, "color": "#ef4444", "icon": "shield"},
    {"id": "monitor", "name": "监控与日志", "order": 20, "color": "#3b82f6", "icon": "chart"},
    {"id": "network", "name": "网络与代理", "order": 30, "color": "#64748b", "icon": "globe"},
    {"id": "database", "name": "数据存储", "order": 35, "color": "#a855f7", "icon": "database"},
    {"id": "app", "name": "应用服务", "order": 40, "color": "#f59e0b", "icon": "box"},
    {"id": "ops", "name": "运维管理", "order": 45, "color": "#10b981", "icon": "tool"},
    {"id": "middleware", "name": "消息与注册", "order": 50, "color": "#f97316", "icon": "cube"},
    {"id": "auto_workflow", "name": "自动化工作流", "order": 60, "color": "#ec4899", "icon": "bolt"},
    {"id": "ungrouped", "name": "未分组", "order": 999, "color": "#475569", "icon": "inbox"},
]


# Systemd service name prefixes to skip (OS-level, not user-facing)
_SKIP_SYSTEMD_PREFIXES = (
    'systemd-', 'dbus-', 'dbus.', 'user-', 'user@', 'session-',
    'getty@', 'serial-', 'multi-user-', 'graphical-', 'networkd-',
    'polkit', 'udisks', 'accounts-daemon', 'irqbalance',
    'thermald', 'powerd', 'fwupd', 'packagekit', 'snapd.',
    'ModemManager', 'NetworkManager', 'wpa_supplicant',
    'cron', 'atd', 'rsyslog', 'logrotate',
    'rsync', 'chrony', 'emergency', 'rescue',
    'kmod', 'lvm2', 'dm-event', 'multipathd', 'mdmonitor',
    'cloud-', 'snapd', 'unattended', 'apt-daily', 'dpkg-',
    'keyboard', 'console', 'plymouth', 'ufw',
    # v3.20.0 补充：根据实际扫描结果添加
    'aliyun', 'aegis', 'hbrclient', 'ssh', 'sshd',
    'containerd', 'docker', 'tuned', 'auditd', 'fail2ban',
    'opsagent', 'opscenter-backend',
    'acpid', 'apcupsd', 'autofs', 'avahi',
    'blk-availability', 'brandbot', 'cpupower',
    'dbus', 'dmraid', 'dracut', 'ebtables',
    'fstrim', 'gpm', 'halt', 'init', 'ip6tables', 'iptables',
    'kdump', 'killproc', 'kexec', 'libvirtd',
    'mcstrans', 'messagebus', 'microcode',
    'netconsole', 'netfs', 'nfs', 'nfslock', 'nscd',
    'portreserve', 'postfix', 'procps', ' quota_nld',
    'rc', 'rc-local', 'rdisc', 'restorecond',
    'rngd', 'rpcbind', 'rpcidmapd', 'saslauthd',
    'smartd', 'snmpd', 'spice-vdagentd', 'ssext',
    'sysstat', 'system-setup', 'tcsd', 'vboxadd',
    'vboxdracf', 'vgauthd', 'vmtoolsd', 'vmware',
    'wpa_supplicant', 'xen', 'yum', 'zfs',
)

# Port-based service name/URL hints for known services
_PORT_SERVICE_HINTS = {
    9100: {"name": "OpsCenter", "category": "运维管理", "icon": "tool",
           "url_tpl": "http://{host}:9100/", "desc": "运维工作台"},
    9091: {"name": "OpsCenter API", "category": "运维管理", "icon": "tool",
           "url_tpl": "http://{host}:9091/docs", "desc": "运维工作台后端API"},
    19100: {"name": "OpsAgent", "category": "运维管理", "icon": "eye",
            "url_tpl": "http://{host}:19100/health", "desc": "监控Agent"},
    8000: {"name": "2FAuth", "category": "安全与认证", "icon": "shield",
           "url_tpl": "http://{host}:8000/", "desc": "MFA虚拟验证码"},
    8080: {"name": "Jenkins", "category": "CI/CD", "icon": "hammer",
           "url_tpl": "http://{host}:8080/", "desc": "CI/CD服务器"},
    3000: {"name": "Gitea", "category": "代码与CI/CD", "icon": "code",
           "url_tpl": "http://{host}:3000/", "desc": "代码仓库"},
    3001: {"name": "Grafana", "category": "监控", "icon": "chart",
           "url_tpl": "http://{host}:3001/", "desc": "监控仪表盘"},
    9090: {"name": "Prometheus", "category": "监控", "icon": "chart",
           "url_tpl": "http://{host}:9090/", "desc": "指标采集"},
    8848: {"name": "Nacos", "category": "消息与注册", "icon": "cube",
           "url_tpl": "http://{host}:8848/nacos/", "desc": "服务注册与配置中心"},
    15672: {"name": "RabbitMQ", "category": "消息与注册", "icon": "cube",
            "url_tpl": "http://{host}:15672/", "desc": "消息队列管理"},
    5601: {"name": "Kibana", "category": "监控", "icon": "chart",
           "url_tpl": "http://{host}:5601/", "desc": "ES可视化"},
    8101: {"name": "Spring Boot Admin", "category": "监控", "icon": "chart",
           "url_tpl": "http://{host}:8101/", "desc": "Spring Boot监控"},
    9999: {"name": "1Panel", "category": "运维面板", "icon": "tool",
           "url_tpl": "http://{host}:9999/", "desc": "Linux运维面板"},
    5433: {"name": "PostgreSQL", "category": "数据存储", "icon": "database",
           "url_tpl": "postgresql://{host}:5433", "desc": "OpsCenter数据库(备用端口)"},
}

# UDP port service hints for well-known UDP services
_UDP_SERVICE_HINTS = {
    53: {"name": "DNS", "category": "网络与代理", "url": ""},
    123: {"name": "NTP", "category": "网络与代理", "url": ""},
    161: {"name": "SNMP", "category": "监控与日志", "url": ""},
    1900: {"name": "SSDP/UPnP", "category": "网络与代理", "url": ""},
    5353: {"name": "mDNS", "category": "网络与代理", "url": ""},
}

# Ports to always skip (system/ephemeral)
_SKIP_PORTS = {22, 25, 53, 68, 80, 323, 9323}
_SKIP_PROCESSES = {"hbrclient", "hbrclientupdater", "snapd", "packagekitd", "polkitd", "rtkit-daemon", "containerd", "dockerd", "docker-proxy", "containerd-shim"}


# === Database ===
engine = create_engine(DB_URL, poolclass=QueuePool, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(bind=engine)

@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# === Schemas ===
class ServerCreate(BaseModel):
    name: str
    host: str
    ssh_port: int = 22
    ssh_user: str = "ops"
    ssh_key: Optional[str] = None
    ssh_password: Optional[str] = None
    tags: List[str] = []
    is_local: bool = False

class ServerUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    ssh_key: Optional[str] = None
    ssh_password: Optional[str] = None
    tags: Optional[List[str]] = None


class SshTestRequest(BaseModel):
    host: str
    port: int = 22
    username: str = "root"
    password: Optional[str] = None
    ssh_key: Optional[str] = None

class ServiceCreate(BaseModel):
    name: str
    url: str
    category: str = "未分类"
    icon: str = "fa-cube"
    description: str = ""
    health_path: Optional[str] = None
    pinned: bool = False

class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    health_path: Optional[str] = None
    pinned: Optional[bool] = None
    hidden: Optional[bool] = None
    account: Optional[str] = None
    password: Optional[str] = None

class PinToggle(BaseModel):
    pinned: bool

# === App ===
app = FastAPI(title="OpsCenter API", version=VERSION)


# Category metadata for enhanced UI
CATEGORY_META = {
    "代码与CI/CD": {"icon": "fa-code", "color": "#8b5cf6", "order": 1},
    "应用服务": {"icon": "fa-cube", "color": "#3b82f6", "order": 2},
    "监控与日志": {"icon": "fa-chart-area", "color": "#22c55e", "order": 3},
    "网络与代理": {"icon": "fa-network-wired", "color": "#f59e0b", "order": 4},
    "自动化工作流": {"icon": "fa-robot", "color": "#ec4899", "order": 5},
    "数据存储": {"icon": "fa-database", "color": "#06b6d4", "order": 6},
    "运维管理": {"icon": "fa-gauge-high", "color": "#f97316", "order": 7},
    "安全": {"icon": "fa-shield-halved", "color": "#ef4444", "order": 8},
    "未分类": {"icon": "fa-folder", "color": "#94a3b8", "order": 99},
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# v3.28 A1 操作审计中间件（写操作记录；AUDIT_ENABLED=false 关闭）
app.add_middleware(AuditMiddleware)

# === v3.29 路由挂载（T2 密钥 / T3 详情拓扑大屏 / 主机操控） ===
app.include_router(api_keys_router)
app.include_router(topology_router)
app.include_router(control_router)
app.include_router(plaza_router)

# === Startup ===

# === Background Health Check ===
def _run_server_health_check():
    """Check host reachability without holding a DB session during socket I/O."""
    try:
        with get_db() as db:
            targets = [
                (srv.id, srv.agent_type, srv.host, srv.ssh_port or 22, srv.agent_port or 19100)
                for srv in db.query(Server).all()
            ]

        results = {}
        for server_id, agent_type, host, ssh_port, agent_port in sorted(targets, key=lambda item: str(item[0])):
            check_host = "127.0.0.1" if agent_type == "local" else host
            check_port = agent_port if agent_type == "local" else ssh_port
            try:
                with socket.create_connection((check_host, check_port), timeout=3):
                    results[server_id] = True
            except OSError:
                results[server_id] = False

        now = datetime.utcnow()
        with get_db() as db:
            for server_id, is_online in results.items():
                values = {Server.status: ServerStatus.online.value if is_online else ServerStatus.offline.value}
                if is_online:
                    values[Server.last_seen] = now
                db.query(Server).filter(Server.id == server_id).update(values)
            db.commit()
    except Exception as exc:
        print(f"Server health check error: {exc}")


async def background_health_check():
    """Periodically check host reachability; services use service_health_loop."""
    while True:
        await asyncio.to_thread(_run_server_health_check)
        await asyncio.sleep(60)


def _run_agent_health_check():
    """Run one Agent reconciliation cycle for every registered server.

    Remote Agents are checked even after they enter stopped/not_deployed so a
    transient failure or stale database token cannot permanently disable
    collection.  A running service's on-host config is authoritative for its
    port, token and version; the token is never logged or returned to clients.
    """
    with get_db() as db:
        local_srv = db.query(Server).filter(Server.agent_type == "local").first()
        if local_srv:
            try:
                local_data = fetch_agent_metrics(
                    "127.0.0.1", local_srv.agent_port or 19100, local_srv.agent_token or ""
                )
                if local_data:
                    local_srv.agent_status = "running"
                    local_srv.agent_version = local_data.get("agent_version", local_srv.agent_version or "")
                    local_srv.last_seen = datetime.utcnow()
                else:
                    local_srv.agent_status = "stopped"
            except Exception as exc:
                print(f"[AgentHealthCheck] Local Agent error: {exc}")
                local_srv.agent_status = "stopped"

        remote_servers = db.query(Server).filter(Server.agent_type != "local").all()
        for srv in remote_servers:
            try:
                result = check_agent_status(srv)
                new_status = result.get("status", "unknown")
                if new_status == "running":
                    if result.get("agent_port"):
                        srv.agent_port = result["agent_port"]
                    if result.get("agent_token"):
                        srv.agent_token = result["agent_token"]
                    if result.get("agent_version"):
                        srv.agent_version = result["agent_version"]

                    probe = fetch_agent_metrics(
                        srv.host, srv.agent_port or 19100, srv.agent_token or ""
                    )
                    if probe:
                        srv.agent_status = "running"
                        srv.last_seen = datetime.utcnow()
                    else:
                        srv.agent_status = "error"
                elif new_status in ("stopped", "installed_stopped"):
                    srv.agent_status = "stopped"
                elif new_status in ("not_deployed", "not_installed"):
                    srv.agent_status = "not_deployed"
                else:
                    srv.agent_status = "error"
            except Exception as exc:
                print(f"[AgentHealthCheck] Error checking {srv.host}: {exc}")
                srv.agent_status = "error"
        db.commit()


async def _agent_health_check_loop():
    """Reconcile Agent state every five minutes without sticky failures."""
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.to_thread(_run_agent_health_check)
        except Exception as exc:
            print(f"[AgentHealthCheck] Loop error: {exc}")
        await asyncio.sleep(300)


def _migrate_groups_json():
    """Migrate groups.json from old flat format to per-server format."""
    import json as _json
    import shutil
    if not os.path.exists(GROUPS_JSON_PATH):
        return
    try:
        with open(GROUPS_JSON_PATH, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except Exception:
        return
    if "groups" not in data or "servers" in data:
        return
    shutil.copy2(GROUPS_JSON_PATH, GROUPS_JSON_PATH + ".bak")
    old_groups = data.get("groups", [])
    old_smap = data.get("serviceGroupMap", {})
    server_smaps = {}
    for key, group_id in old_smap.items():
        parts = key.split(":")
        if len(parts) >= 3 and parts[0] == "auto":
            sid = parts[1]
        else:
            sid = "_unknown"
        server_smaps.setdefault(sid, {})[key] = group_id
    new_data = {
        "defaultGroups": old_groups if old_groups else list(DEFAULT_GROUPS),
        "servers": {}
    }
    for sid, smap in server_smaps.items():
        new_data["servers"][sid] = {
            "groups": [dict(g) for g in (old_groups if old_groups else DEFAULT_GROUPS)],
            "serviceGroupMap": smap
        }
    with open(GROUPS_JSON_PATH, "w", encoding="utf-8") as f:
        _json.dump(new_data, f, ensure_ascii=False, indent=2)
    print(f"[Migration] groups.json migrated to per-server format ({len(new_data['servers'])} servers)")

def _auto_assign_group(server_id: str, service_id: str, category: str):
    """Auto-assign a service to a group based on its category (per-server)."""
    # M5 fix: skip if service_id is None or "None" string
    if not service_id or service_id == "None":
        return "ungrouped"
    group_id = CATEGORY_TO_GROUP.get(category, "ungrouped")
    config = _read_groups_json()
    if server_id not in config.get("servers", {}):
        config.setdefault("servers", {})[server_id] = {
            "groups": [dict(g) for g in config.get("defaultGroups", DEFAULT_GROUPS)],
            "serviceGroupMap": {}
        }
    srv_cfg = config["servers"][server_id]
    existing_ids = [g["id"] for g in srv_cfg.get("groups", [])]
    for dg in DEFAULT_GROUPS:
        if dg["id"] not in existing_ids:
            srv_cfg.setdefault("groups", []).append(dg)
    key = f"auto:{server_id}:{service_id}"
    srv_cfg.setdefault("serviceGroupMap", {})[key] = group_id
    _write_groups_json(config)
    return group_id


def _auto_assign_all_groups():
    """Auto-assign all services to groups based on their categories (per-server)."""
    with get_db() as db:
        services = db.query(Service).all()
        server_ids = {str(s.id) for s in db.query(Server).all()}
    config = _read_groups_json()
    changed = False
    for sid in server_ids:
        if sid not in config.get("servers", {}):
            config.setdefault("servers", {})[sid] = {
                "groups": [dict(g) for g in config.get("defaultGroups", DEFAULT_GROUPS)],
                "serviceGroupMap": {}
            }
            changed = True
        srv_cfg = config["servers"][sid]
        existing_ids = [g["id"] for g in srv_cfg.get("groups", [])]
        for dg in DEFAULT_GROUPS:
            if dg["id"] not in existing_ids:
                srv_cfg.setdefault("groups", []).append(dg)
                changed = True
    for svc in services:
        sid = str(svc.server_id)
        if sid not in config.get("servers", {}):
            continue
        srv_cfg = config["servers"][sid]
        key = f"auto:{sid}:{svc.id}"
        group_id = CATEGORY_TO_GROUP.get(svc.category, "ungrouped")
        if srv_cfg.get("serviceGroupMap", {}).get(key) != group_id:
            srv_cfg.setdefault("serviceGroupMap", {})[key] = group_id
            changed = True
    # Clean invalid mappings
    valid_keys = set()
    for svc in services:
        valid_keys.add(f"auto:{svc.server_id}:{svc.id}")
    for sid in list(config.get("servers", {}).keys()):
        smap = config["servers"][sid].get("serviceGroupMap", {})
        invalid = [k for k in smap if k not in valid_keys]
        for k in invalid:
            del smap[k]
            changed = True
    if changed:
        _write_groups_json(config)
    return changed


# === Alerting APIs (v3.26, F4/F6) ===

class AlertRuleCreate(BaseModel):
    name: str
    server_id: Optional[str] = None
    metric: str
    value_type: str = "numeric"          # numeric | string
    operator: str = ">"
    threshold: str
    duration_sec: int = 60
    cooldown_sec: int = 300
    notify_webhooks: List[str] = []
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    server_id: Optional[str] = None
    metric: Optional[str] = None
    value_type: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[str] = None
    duration_sec: Optional[int] = None
    cooldown_sec: Optional[int] = None
    notify_webhooks: Optional[List[str]] = None
    enabled: Optional[bool] = None


@app.get("/api/v2/alert-rules")
def list_alert_rules():
    """规则列表（含 server 名）。"""
    with get_db() as db:
        rules = db.query(AlertRule).all()
        result = []
        for r in rules:
            server_name = None
            if r.server_id:
                srv = db.query(Server).filter(Server.id == r.server_id).first()
                server_name = srv.name if srv else None
            result.append({
                "id": str(r.id), "name": r.name,
                "server_id": str(r.server_id) if r.server_id else None,
                "server_name": server_name,
                "metric": r.metric, "value_type": r.value_type,
                "operator": r.operator, "threshold": r.threshold,
                "duration_sec": r.duration_sec, "cooldown_sec": r.cooldown_sec,
                "notify_webhooks": r.notify_webhooks or [], "enabled": r.enabled,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return result


@app.post("/api/v2/alert-rules")
def create_alert_rule(req: AlertRuleCreate):
    with get_db() as db:
        rule = AlertRule(
            name=req.name,
            server_id=uuid.UUID(req.server_id) if req.server_id else None,
            metric=req.metric, value_type=req.value_type, operator=req.operator,
            threshold=req.threshold, duration_sec=req.duration_sec,
            cooldown_sec=req.cooldown_sec, notify_webhooks=req.notify_webhooks,
            enabled=req.enabled,
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return {"id": str(rule.id), "name": rule.name}


@app.put("/api/v2/alert-rules/{rule_id}")
def update_alert_rule(rule_id: str, req: AlertRuleUpdate):
    with get_db() as db:
        rule = db.query(AlertRule).filter(AlertRule.id == uuid.UUID(rule_id)).first()
        if not rule:
            raise HTTPException(404, "Rule not found")
        for field, val in req.model_dump(exclude_unset=True).items():
            if val is None:
                continue
            if field == "server_id":
                setattr(rule, field, uuid.UUID(val) if val else None)
            else:
                setattr(rule, field, val)
        rule.updated_at = datetime.utcnow()
        db.commit()
        return {"id": str(rule.id), "name": rule.name}


@app.delete("/api/v2/alert-rules/{rule_id}")
def delete_alert_rule(rule_id: str):
    with get_db() as db:
        rule = db.query(AlertRule).filter(AlertRule.id == uuid.UUID(rule_id)).first()
        if not rule:
            raise HTTPException(404, "Rule not found")
        db.delete(rule)  # 级联删除 alert_events（FK ondelete CASCADE）
        db.commit()
        return {"ok": True}


@app.get("/api/v2/alert-events")
def list_alert_events(status: Optional[str] = None, server_id: Optional[str] = None, days: int = 7):
    """告警事件历史，支持按状态/主机/天数筛选。"""
    with get_db() as db:
        q = db.query(AlertEvent)
        if status:
            q = q.filter(AlertEvent.status == status)
        if server_id:
            q = q.filter(AlertEvent.server_id == uuid.UUID(server_id))
        if days and days > 0:
            q = q.filter(AlertEvent.created_at >= datetime.utcnow() - timedelta(days=days))
        events = q.order_by(AlertEvent.created_at.desc()).limit(500).all()
        result = []
        for e in events:
            rule = db.query(AlertRule).filter(AlertRule.id == e.rule_id).first()
            srv = db.query(Server).filter(Server.id == e.server_id).first()
            result.append({
                "id": str(e.id), "rule_id": str(e.rule_id),
                "rule_name": rule.name if rule else None,
                "server_id": str(e.server_id), "server_name": srv.name if srv else None,
                "status": e.status, "current_value": e.current_value,
                "fired_at": e.fired_at.isoformat() if e.fired_at else None,
                "recovered_at": e.recovered_at.isoformat() if e.recovered_at else None,
                "acked_by": e.acked_by,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            })
        return result


@app.post("/api/v2/alert-events/{event_id}/ack")
def ack_alert_event(event_id: str, acked_by: str = "admin"):
    """确认（ack）一条告警事件。"""
    with get_db() as db:
        ev = db.query(AlertEvent).filter(AlertEvent.id == uuid.UUID(event_id)).first()
        if not ev:
            raise HTTPException(404, "Event not found")
        ev.status = "acked"
        ev.acked_by = acked_by
        ev.acked_at = datetime.utcnow()
        db.commit()
        return {"ok": True, "status": "acked"}


# === Alert Silences API (v3.27, S1) ===

class AlertSilenceCreate(BaseModel):
    rule_id: Optional[str] = None           # NULL=全局静默
    server_id: Optional[str] = None         # NULL=全部服务器
    starts_at: str                          # ISO 8601
    ends_at: str                            # ISO 8601
    reason: str = ""


@app.get("/api/v2/alert-silences")
def list_alert_silences(active: Optional[int] = None):
    """静默列表；active=1 仅返回生效中的（starts<=now<ends）。"""
    now = datetime.utcnow()
    with get_db() as db:
        q = db.query(AlertSilence)
        if active:
            q = q.filter(AlertSilence.starts_at <= now, AlertSilence.ends_at > now)
        silences = q.order_by(AlertSilence.created_at.desc()).all()
        result = []
        for s in silences:
            rule_name = None
            if s.rule_id:
                r = db.query(AlertRule).filter(AlertRule.id == s.rule_id).first()
                rule_name = r.name if r else None
            srv_name = None
            if s.server_id:
                sv = db.query(Server).filter(Server.id == s.server_id).first()
                srv_name = sv.name if sv else None
            result.append({
                "id": str(s.id),
                "rule_id": str(s.rule_id) if s.rule_id else None,
                "rule_name": rule_name,
                "server_id": str(s.server_id) if s.server_id else None,
                "server_name": srv_name,
                "starts_at": s.starts_at.isoformat(),
                "ends_at": s.ends_at.isoformat(),
                "reason": s.reason,
                "created_by": s.created_by,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "active": s.starts_at <= now < s.ends_at,
            })
        return result


@app.post("/api/v2/alert-silences")
def create_alert_silence(req: AlertSilenceCreate):
    """创建静默（rule_id/server_id 为空 = 全局匹配）。"""
    try:
        starts = datetime.fromisoformat(req.starts_at.replace("Z", "+00:00")).replace(tzinfo=None)
        ends = datetime.fromisoformat(req.ends_at.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(400, "starts_at/ends_at must be ISO 8601 (e.g. 2026-08-09T12:00:00)")
    if ends <= starts:
        raise HTTPException(400, "ends_at must be after starts_at")
    with get_db() as db:
        sil = AlertSilence(
            rule_id=uuid.UUID(req.rule_id) if req.rule_id else None,
            server_id=uuid.UUID(req.server_id) if req.server_id else None,
            starts_at=starts, ends_at=ends, reason=req.reason,
            created_by="admin",
        )
        db.add(sil)
        db.commit()
        db.refresh(sil)
        return {"id": str(sil.id), "starts_at": starts.isoformat(), "ends_at": ends.isoformat()}


@app.delete("/api/v2/alert-silences/{silence_id}")
def delete_alert_silence(silence_id: str):
    """提前解除静默。"""
    with get_db() as db:
        sil = db.query(AlertSilence).filter(AlertSilence.id == uuid.UUID(silence_id)).first()
        if not sil:
            raise HTTPException(404, "Silence not found")
        db.delete(sil)
        db.commit()
        return {"ok": True}


# === Cert Checks API (v3.27, D1) ===

class CertCheckCreate(BaseModel):
    domain: str
    port: int = 443
    server_id: Optional[str] = None
    enabled: bool = True


@app.get("/api/v2/cert-checks")
def list_cert_checks():
    """证书检查项列表（含最近探测结果）。"""
    with get_db() as db:
        checks = db.query(CertCheck).order_by(CertCheck.domain).all()
        result = []
        for chk in checks:
            srv_name = None
            if chk.server_id:
                srv = db.query(Server).filter(Server.id == chk.server_id).first()
                srv_name = srv.name if srv else None
            result.append({
                "id": str(chk.id),
                "server_id": str(chk.server_id) if chk.server_id else None,
                "server_name": srv_name,
                "domain": chk.domain, "port": chk.port,
                "days_left": chk.days_left,
                "not_after": chk.not_after.isoformat() if chk.not_after else None,
                "issuer": chk.issuer,
                "last_error": chk.last_error,
                "enabled": chk.enabled,
            })
        return result


@app.post("/api/v2/cert-checks")
def create_cert_check(req: CertCheckCreate):
    with get_db() as db:
        chk = CertCheck(
            domain=req.domain, port=req.port,
            server_id=uuid.UUID(req.server_id) if req.server_id else None,
            enabled=req.enabled,
        )
        db.add(chk); db.commit(); db.refresh(chk)
        return {"id": str(chk.id), "domain": chk.domain}


@app.post("/api/v2/cert-checks/scan")
def trigger_cert_scan():
    """手动触发一轮证书扫描。"""
    run_cert_scan()
    return {"ok": True}


@app.delete("/api/v2/cert-checks/{check_id}")
def delete_cert_check(check_id: str):
    with get_db() as db:
        chk = db.query(CertCheck).filter(CertCheck.id == uuid.UUID(check_id)).first()
        if not chk:
            raise HTTPException(404, "Cert check not found")
        db.delete(chk); db.commit()
        return {"ok": True}


# === Log Watch API (v3.27, D2) ===

class LogRuleCreate(BaseModel):
    name: str
    server_id: str
    log_path: str
    pattern: str
    tail_lines: int = 200
    enabled: bool = True


@app.get("/api/v2/log-rules")
def list_log_rules():
    """日志规则列表（含 server 名与最近命中数）。"""
    with get_db() as db:
        rules = db.query(LogRule).all()
        result = []
        for r in rules:
            srv = db.query(Server).filter(Server.id == r.server_id).first()
            recent = db.query(LogMatch).filter(
                LogMatch.rule_id == r.id,
                LogMatch.matched_at >= datetime.utcnow() - timedelta(hours=24),
            ).count()
            result.append({
                "id": str(r.id), "name": r.name,
                "server_id": str(r.server_id),
                "server_name": srv.name if srv else None,
                "log_path": r.log_path, "pattern": r.pattern,
                "tail_lines": r.tail_lines, "enabled": r.enabled,
                "matches_24h": recent,
            })
        return result


@app.post("/api/v2/log-rules")
def create_log_rule(req: LogRuleCreate):
    with get_db() as db:
        rule = LogRule(
            name=req.name, server_id=uuid.UUID(req.server_id),
            log_path=req.log_path, pattern=req.pattern,
            tail_lines=req.tail_lines, enabled=req.enabled,
        )
        db.add(rule); db.commit(); db.refresh(rule)
        return {"id": str(rule.id), "name": rule.name}


@app.delete("/api/v2/log-rules/{rule_id}")
def delete_log_rule(rule_id: str):
    with get_db() as db:
        rule = db.query(LogRule).filter(LogRule.id == uuid.UUID(rule_id)).first()
        if not rule:
            raise HTTPException(404, "Log rule not found")
        db.delete(rule); db.commit()
        return {"ok": True}


@app.post("/api/v2/log-rules/scan")
def trigger_log_scan():
    """手动触发一轮日志扫描。"""
    run_log_scan()
    return {"ok": True}


@app.get("/api/v2/log-matches")
def list_log_matches(rule_id: Optional[str] = None, days: int = 1, limit: int = 100):
    """日志命中明细。"""
    with get_db() as db:
        q = db.query(LogMatch)
        if rule_id:
            q = q.filter(LogMatch.rule_id == uuid.UUID(rule_id))
        if days and days > 0:
            q = q.filter(LogMatch.matched_at >= datetime.utcnow() - timedelta(days=days))
        matches = q.order_by(LogMatch.matched_at.desc()).limit(limit).all()
        result = []
        for m in matches:
            rule = db.query(LogRule).filter(LogRule.id == m.rule_id).first()
            srv = db.query(Server).filter(Server.id == m.server_id).first()
            result.append({
                "id": str(m.id),
                "rule_id": str(m.rule_id), "rule_name": rule.name if rule else None,
                "server_id": str(m.server_id), "server_name": srv.name if srv else None,
                "matched_line": m.matched_line,
                "matched_at": m.matched_at.isoformat() if m.matched_at else None,
            })
        return result


# === Backup Checks API (v3.27, D3) ===

class BackupCheckCreate(BaseModel):
    name: str
    server_id: str
    target_path: str
    expected_interval_hours: int = 24
    min_size_bytes: int = 0
    enabled: bool = True


@app.get("/api/v2/backup-checks")
def list_backup_checks():
    """备份检查项列表（含 server 名）。"""
    with get_db() as db:
        checks = db.query(BackupCheck).all()
        result = []
        for chk in checks:
            srv = db.query(Server).filter(Server.id == chk.server_id).first()
            result.append({
                "id": str(chk.id), "name": chk.name,
                "server_id": str(chk.server_id), "server_name": srv.name if srv else None,
                "target_path": chk.target_path,
                "expected_interval_hours": chk.expected_interval_hours,
                "min_size_bytes": chk.min_size_bytes,
                "enabled": chk.enabled,
            })
        return result


@app.post("/api/v2/backup-checks")
def create_backup_check(req: BackupCheckCreate):
    with get_db() as db:
        chk = BackupCheck(
            name=req.name, server_id=uuid.UUID(req.server_id),
            target_path=req.target_path,
            expected_interval_hours=req.expected_interval_hours,
            min_size_bytes=req.min_size_bytes, enabled=req.enabled,
        )
        db.add(chk); db.commit(); db.refresh(chk)
        return {"id": str(chk.id), "name": chk.name}


@app.post("/api/v2/backup-checks/scan")
def trigger_backup_check():
    """手动触发一轮备份检查。"""
    run_backup_check()
    return {"ok": True}


@app.delete("/api/v2/backup-checks/{check_id}")
def delete_backup_check(check_id: str):
    with get_db() as db:
        chk = db.query(BackupCheck).filter(BackupCheck.id == uuid.UUID(check_id)).first()
        if not chk:
            raise HTTPException(404, "Backup check not found")
        db.delete(chk); db.commit()
        return {"ok": True}


# === Image Status API (v3.27, D4) ===

@app.get("/api/v2/images")
def list_images(outdated_only: Optional[int] = None):
    """镜像状态列表；outdated_only=1 仅返回落后镜像。"""
    with get_db() as db:
        q = db.query(ImageStatus)
        if outdated_only:
            q = q.filter(ImageStatus.outdated == True)  # noqa: E712
        rows = q.order_by(ImageStatus.server_id, ImageStatus.container_name).all()
        result = []
        for img in rows:
            srv = db.query(Server).filter(Server.id == img.server_id).first()
            result.append({
                "id": str(img.id),
                "server_id": str(img.server_id), "server_name": srv.name if srv else None,
                "container_name": img.container_name, "image": img.image,
                "local_digest": img.local_digest, "remote_digest": img.remote_digest,
                "outdated": img.outdated,
                "checked_at": img.checked_at.isoformat() if img.checked_at else None,
            })
        return result


@app.post("/api/v2/images/scan")
def trigger_image_check():
    """手动触发一轮镜像检查。"""
    run_image_check()
    return {"ok": True}


# === Status Page API (v3.27, S3) ===
# 公开端点：仅聚合健康摘要，不暴露 IP/端口/凭证

# === Reports API (v3.28, R1) ===

@app.get("/api/v2/reports")
def list_reports(days: int = 7):
    """最近 N 天日报列表（含结构化摘要）。"""
    with get_db() as db:
        since = date.today() - timedelta(days=days)
        reports = db.query(DailyReport).filter(
            DailyReport.report_date >= since).order_by(DailyReport.report_date.desc()).all()
        return [{
            "id": str(r.id), "report_date": r.report_date.isoformat(),
            "title": r.title, "summary": r.summary,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in reports]


@app.get("/api/v2/reports/{report_id}")
def get_report(report_id: str):
    """日报详情（含 Markdown 正文）。"""
    with get_db() as db:
        r = db.query(DailyReport).filter(DailyReport.id == uuid.UUID(report_id)).first()
        if not r:
            raise HTTPException(404, "Report not found")
        return {
            "id": str(r.id), "report_date": r.report_date.isoformat(),
            "title": r.title, "summary": r.summary, "content": r.content,
        }


@app.post("/api/v2/reports/generate")
def generate_report_now():
    """手动生成今日日报（幂等，已存在则覆盖）。"""
    result = generate_report()
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


# === Audit Logs API (v3.28, A2) ===

@app.get("/api/v2/audit-logs")
def list_audit_logs(action: Optional[str] = None, resource: Optional[str] = None,
                    username: Optional[str] = None, days: int = 30,
                    limit: int = 100, offset: int = 0):
    """审计日志查询（筛选 + 分页）。"""
    with get_db() as db:
        q = db.query(AuditLog)
        if action:
            q = q.filter(AuditLog.action == action)
        if resource:
            q = q.filter(AuditLog.resource == resource)
        if username:
            q = q.filter(AuditLog.username == username)
        if days and days > 0:
            q = q.filter(AuditLog.ts >= datetime.utcnow() - timedelta(days=days))
        total = q.count()
        rows = q.order_by(AuditLog.ts.desc()).offset(offset).limit(limit).all()
        return {
            "total": total,
            "items": [{
                "id": str(a.id),
                "ts": a.ts.isoformat() if a.ts else None,
                "username": a.username, "action": a.action, "resource": a.resource,
                "resource_id": a.resource_id, "detail": a.detail,
                "ip": a.ip, "status": a.status,
            } for a in rows],
        }


@app.get("/api/v2/status-page")
def get_status_page():
    """聚合状态页数据：服务器健康、服务状态、活跃告警、7 天可用性。"""
    from datetime import date as _date
    from collections import defaultdict
    with get_db() as db:
        servers = db.query(Server).filter(Server.enabled == True).all()  # noqa: E712
        srv_rows = []
        total_up = 0
        for s in servers:
            is_up = s.agent_status == "running" and s.status == "online"
            if is_up:
                total_up += 1
            srv_rows.append({
                "name": s.name,
                "status": "up" if is_up else ("degraded" if s.status == "online" else "down"),
                "agent": s.agent_status,
            })
        # 服务状态聚合（按 server）
        svc_rows = []
        for s in servers:
            services = db.query(Service).filter(Service.server_id == s.id, Service.hidden == False).all()  # noqa: E712
            up = sum(1 for x in services if x.status == "up")
            total = len(services)
            if total:
                svc_rows.append({"server_name": s.name, "up": up, "total": total,
                                 "status": "up" if up == total and total > 0 else ("degraded" if up > 0 else "down")})
        # 活跃告警
        firing = db.query(AlertEvent).filter(AlertEvent.status == "firing").count()
        # 7 天可用性（基于 agent_status 历史：metric_history 无该指标时用当前状态近似）
        since = datetime.utcnow() - timedelta(days=7)
        avail = []
        for s in servers:
            # 尝试从 metric_history 统计 agent 在线比例（metric=agent_status value='running'）
            samples = db.query(MetricHistory).filter(
                MetricHistory.server_id == s.id,
                MetricHistory.metric == "agent_status",
                MetricHistory.timestamp >= since,
            ).all()
            if samples:
                online = sum(1 for m in samples if m.value == "running")
                pct = round(online / len(samples) * 100)
            else:
                # 无历史数据：用当前状态近似
                pct = 100 if s.agent_status == "running" else 0
            avail.append({"name": s.name, "availability_7d": pct, "samples": len(samples)})
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "summary": {
                "servers_total": len(servers),
                "servers_up": total_up,
                "services_up": sum(x["up"] for x in svc_rows),
                "services_total": sum(x["total"] for x in svc_rows),
                "alerts_firing": firing,
            },
            "servers": srv_rows,
            "services": svc_rows,
            "availability": avail,
        }


def _ensure_new_columns():
    """轻量迁移（v3.29）：为已有表补充新增列；新表由 create_all 自动创建。"""
    stmts = [
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS deploy_type VARCHAR(20)",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS started_at TIMESTAMP",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS version VARCHAR(60)",
    ]
    try:
        with engine.connect() as conn:
            for s in stmts:
                conn.execute(text(s))
            conn.commit()
    except Exception as e:
        # 迁移失败不阻断启动，仅记录日志（旧库缺失列时相关查询会暴露，可人工处理）
        print(f"[migrate] ensure columns failed: {e}", flush=True)


@app.on_event("startup")
async def startup():
    # Start Agent health check background task
    import asyncio
    asyncio.create_task(_agent_health_check_loop())
    # v3.29 T4: 服务健康检查后台循环（间隔/阈值走环境变量）
    asyncio.create_task(service_health_loop())
    # Wait for DB and create tables
    import time
    for i in range(30):
        try:
            Base.metadata.create_all(bind=engine)
            _ensure_new_columns()
            break
        except Exception:
            time.sleep(2)
    
    # Auto-register local server and discover services
    with get_db() as db:
        local = db.query(Server).filter((Server.is_local == True) | (Server.agent_type == "local")).first()
        if not local:
            local = Server(
                name="MFA Server",
                host=LOCAL_HOST,
                ssh_port=22,
                ssh_user="root",
                status=ServerStatus.online.value,
                docker_available=True,
                is_local=True,
                agent_type='local',
            )
            db.add(local)
            db.commit()
            db.refresh(local)
        
        # Run initial discovery
        discover_docker_services(local, db, LOCAL_HOST)
        # Auto-detect local Agent status on startup
        try:
            # F1: 先通过 check_agent_status 解析本机 Agent token（.agent_config），
            # 否则空 token 会被新 Agent（v2.2.0 强制 token）拒绝，导致本机 Agent 短暂不可达
            if not local.agent_token:
                _st = check_agent_status(local)
                if _st.get("agent_token"):
                    local.agent_token = _st["agent_token"]
                    db.commit()
            local_agent = fetch_agent_metrics("127.0.0.1", local.agent_port or 19100, local.agent_token or "")
            if local_agent:
                local.agent_status = "running"
                local.agent_version = local_agent.get("agent_version", "2.2.0")
                local.last_seen = datetime.utcnow()
                print(f"[Startup] Local Agent detected: v{local.agent_version}")
            else:
                local.agent_status = "stopped"
                print("[Startup] Local Agent not reachable")
        except Exception as e:
            print(f"[Startup] Local Agent check error: {e}")


        
        # Parse nginx config
        nginx_routes = parse_nginx_config(host=LOCAL_HOST)
        for route in nginx_routes:
            existing = db.query(Service).filter(
                Service.server_id == local.id,
                Service.url == route["url"],
                Service.source != ServiceSource.docker_label.value,
                Service.source != ServiceSource.docker_auto.value,
            ).first()
            if not existing:
                svc = Service(
                    server_id=local.id,
                    name=route["name"],
                    url=route["url"],
                    source=ServiceSource.nginx.value,
                    category="未分类",
                    icon="fa-globe",
                    status=ServiceStatus.unknown.value,
                )
                db.add(svc)
        db.commit()

    # Start background health check
    asyncio.create_task(background_health_check())
    asyncio.create_task(daily_network_aggregation())  # v3.25.1 每日流量归集
    # Start background agent metrics collector
    asyncio.create_task(background_agent_collector())
    # v3.26: 告警引擎 + 数据保留后台任务
    asyncio.create_task(alerting_loop())    # 每 60s 一轮评估（ALERTING_ENABLED=false 关闭）
    asyncio.create_task(cert_scan_loop())    # v3.27 D1 证书扫描（CERT_SCAN_ENABLED=false 关闭）
    asyncio.create_task(log_scan_loop())    # v3.27 D2 日志扫描（LOG_SCAN_ENABLED=false 关闭）
    asyncio.create_task(backup_check_loop())    # v3.27 D3 备份验证（BACKUP_CHECK_ENABLED=false 关闭）
    asyncio.create_task(image_check_loop())    # v3.27 D4 镜像检查（IMAGE_CHECK_ENABLED=false 关闭）
    asyncio.create_task(report_loop())    # v3.28 R2 日报（REPORT_ENABLED=false 关闭）
    asyncio.create_task(retention_loop())   # 每天 01:00 清理过期数据
    seed_default_rules()
    seed_cert_rule()
    seed_backup_rule()                    # 幂等 seed 默认规则（仅空表时写入）
    # Migrate groups.json to per-server format if needed
    _migrate_groups_json()
    # Auto-assign groups on startup
    _auto_assign_all_groups()


# === Server APIs ===
@app.get("/api/v2/servers")
def list_servers():
    with get_db() as db:
        servers = db.query(Server).all()
        result = []
        for s in servers:
            svc_count = db.query(Service).filter(Service.server_id == s.id).count()
            has_creds = bool(s.ssh_key and (s.ssh_key.startswith("__password__") or "BEGIN" in s.ssh_key))
            result.append({
                "id": str(s.id),
                "name": s.name,
                "host": s.host,
                "ssh_port": s.ssh_port,
                "ssh_user": s.ssh_user,
                "tags": s.tags or [],
                "status": s.status,
                "docker_available": s.docker_available,
                "is_local": s.is_local,  # deprecated, use agent_type
                "agent_type": s.agent_type or "remote",
                "last_seen": s.last_seen.isoformat() if s.last_seen else None,
                "service_count": svc_count,
                "has_credentials": has_creds,
                "agent_status": s.agent_status or "not_deployed",
                "agent_port": s.agent_port or 19100,
                "agent_version": s.agent_version or "",
            })
        return result

@app.post("/api/v2/servers", status_code=201)
def create_server(data: ServerCreate):
    with get_db() as db:
        ssh_key_val = data.ssh_key
        if data.ssh_password and not data.ssh_key:
            ssh_key_val = f"__password__{data.ssh_password}"
        srv = Server(
            name=data.name, host=data.host, ssh_port=data.ssh_port,
            ssh_user=data.ssh_user, ssh_key=ssh_key_val,
            tags=data.tags, is_local=data.is_local,
        )
        db.add(srv)
        db.commit()
        db.refresh(srv)
        result = {"id": str(srv.id), "name": srv.name, "host": srv.host}
    
    # Auto-deploy agent for remote servers with SSH credentials
    if not data.is_local and (data.ssh_password or data.ssh_key):
        try:
            deploy_result = deploy_agent(srv, password=data.ssh_password)
            if deploy_result.get("success"):
                with get_db() as db2:
                    s = db2.query(Server).filter(Server.id == srv.id).first()
                    if s:
                        s.agent_status = "running"
                        s.agent_port = deploy_result.get("agent_port", 19100)
                        s.agent_token = deploy_result.get("agent_token", "")
                        s.agent_version = deploy_result.get("agent_version", "2.0.0")
                        db2.commit()
                result["agent_deployed"] = True
                result["agent_message"] = deploy_result.get("message", "")
            else:
                with get_db() as db2:
                    s = db2.query(Server).filter(Server.id == srv.id).first()
                    if s:
                        s.agent_status = "error"
                        db2.commit()
                result["agent_deployed"] = False
                result["agent_message"] = deploy_result.get("message", "")
        except Exception as e:
            result["agent_deployed"] = False
            result["agent_message"] = f"Agent部署异常: {str(e)}"
    
    return result

@app.get("/api/v2/servers/{server_id}")
def get_server(server_id: str):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        return {
            "id": str(srv.id), "name": srv.name, "host": srv.host,
            "ssh_port": srv.ssh_port, "ssh_user": srv.ssh_user,
            "tags": srv.tags or [], "status": srv.status,
            "docker_available": srv.docker_available, "is_local": srv.is_local, "agent_type": srv.agent_type,
            "last_seen": srv.last_seen.isoformat() if srv.last_seen else None,
        }

@app.put("/api/v2/servers/{server_id}")
def update_server(server_id: str, data: ServerUpdate):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        for field, val in data.model_dump(exclude_unset=True).items():
            if val is not None:
                setattr(srv, field, val)
        db.commit()
        return {"ok": True}

@app.delete("/api/v2/servers/{server_id}")
def delete_server(server_id: str):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        # Uninstall Agent if running (both local and remote)
        agent_info = ""
        if srv.agent_status in ("running", "error"):
            try:
                from app.agent_manager import uninstall_agent
                result = uninstall_agent(srv)
                agent_info = f" Agent已卸载" if result.get("success") else f" Agent卸载失败: {result.get('message','')}"
            except Exception as e:
                agent_info = f" Agent卸载异常: {e}"
        server_name = srv.name
        db.delete(srv)
        db.commit()
        # Clean up groups.json - remove deleted server's services
        try:
            groups_path = "/opt/opscenter/frontend/groups.json"
            import json
            with open(groups_path, 'r') as gf:
                groups_data = json.load(gf)
            # E1 fix: check inside servers dict, not top-level
            if server_id in groups_data.get("servers", {}):
                del groups_data["servers"][server_id]
                with open(groups_path, 'w') as gf:
                    json.dump(groups_data, gf, indent=2, ensure_ascii=False)
                agent_info += " groups.json已清理"
        except Exception as e:
            print(f"[WARN] groups.json cleanup failed: {e}")
        return {"ok": True, "message": f"服务器'{server_name}'已删除{agent_info}"}

@app.post("/api/v2/servers/{server_id}/scan")
def scan_server(server_id: str, password: Optional[str] = None):
    """Scan services on a server using Agent (preferred) or SSH fallback. Unified for all servers."""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        
        # Unified: All servers use Agent-first approach
        agent_host = "127.0.0.1" if srv.agent_type == "local" else srv.host
        
        # Try Agent if deployed and running
        if srv.agent_status == "running" and srv.agent_port:
            scan_data = trigger_agent_scan(agent_host, srv.agent_port or 19100, srv.agent_token or "")
            if scan_data:
                result = _sync_agent_scan_to_db(srv, db, scan_data)
                _sync_port_driven_scan(srv, db, scan_data)
                srv.status = ServerStatus.online.value
                srv.last_seen = datetime.utcnow()
                srv.docker_available = True
                db.commit()
                # Auto-assign groups for newly discovered services
                for svc in db.query(Service).filter(Service.server_id == srv.id).all():
                    _auto_assign_group(str(srv.id), str(svc.id), svc.category or "")
                # Also discover nginx services
                nginx_result = _sync_nginx_routes(srv, db)
                nginx_count = nginx_result["added"] + nginx_result["updated"]
                return {"discovered": result["added"] + result["updated"] + result.get("port_added", 0) + nginx_count, "source": "agent", "nginx_added": nginx_count}
        
        # Fallback: Docker SDK (local) or SSH (remote)
        if srv.agent_type == "local":
            discovered = discover_docker_services(srv, db, srv.host)
            for d in discovered:
                _auto_assign_group(str(srv.id), str(d.id), d.category or "")
            nginx_result = _sync_nginx_routes(srv, db)
            nginx_count = nginx_result["added"] + nginx_result["updated"]
            srv.last_seen = datetime.utcnow()
            db.commit()
            return {"discovered": len(discovered) + nginx_count, "source": "docker_local", "nginx_added": nginx_count}
        
        # Remote SSH fallback
        client = get_ssh_client(srv, password=password)
        if not client:
            raise HTTPException(400, "Agent不可用且SSH连接失败，请检查凭证")
        try:
            result = _sync_ssh_containers_to_db(srv, db, client)
            srv.status = ServerStatus.online.value
            srv.last_seen = datetime.utcnow()
            srv.docker_available = True
            db.commit()
            return {"discovered": result["added"] + result["updated"], "source": "ssh"}
        except Exception as e:
            raise HTTPException(500, f"Scan failed: {e}")
        finally:
            try:
                client.close()
            except:
                pass

@app.post("/api/v2/servers/{server_id}/test")
def test_server(server_id: str, password: Optional[str] = None):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.agent_type == "local":
            # Local server: try Agent health check
            try:
                agent_data = fetch_agent_metrics("127.0.0.1", srv.agent_port or 19100, srv.agent_token or "")
                if agent_data:
                    srv.status = ServerStatus.online.value
                    srv.last_seen = datetime.utcnow()
                    db.commit()
                    return {"status": "online", "message": "本机Agent响应正常"}
            except Exception:
                pass
            srv.status = ServerStatus.online.value
            srv.last_seen = datetime.utcnow()
            db.commit()
            return {"status": "online", "message": "本机服务器在线"}
        
        # Remote server: test SSH connection
        client = get_ssh_client(srv, password=password)
        if client:
            srv.status = ServerStatus.online.value
            srv.last_seen = datetime.utcnow()
            # Store password if provided (for future auto-scans)
            if password:
                srv.ssh_key = f"__password__{password}"
            db.commit()
            client.close()
            return {"status": "online", "message": "SSH connection successful"}
        return {"status": "offline", "message": "Cannot connect via SSH. Check credentials."}


@app.post("/api/v2/test-ssh")
def test_ssh_connection_api(data: SshTestRequest):
    """Test SSH connection with provided credentials (before creating server)."""
    from app.ssh_manager import test_ssh_connection
    if not data.password and not data.ssh_key:
        return {"success": False, "message": "请提供密码或SSH密钥"}
    success, message = test_ssh_connection(
        host=data.host, port=data.port,
        username=data.username,
        password=data.password,
        ssh_key=data.ssh_key,
    )
    return {"success": success, "message": message}


# === Agent Service Scan APIs ===

@app.get("/api/v2/servers/{server_id}/agent/services")
def get_agent_services(server_id: str):
    """Preview Agent-discovered services without syncing to DB."""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")

        if srv.agent_status != "running":
            raise HTTPException(400, f"Agent未运行 (status={srv.agent_status})")
    
    # Try Agent scan
    data = trigger_agent_scan(srv.host, srv.agent_port or 19100, srv.agent_token or "")
    if not data:
        # Fallback: try cached services
        data = fetch_agent_services(srv.host, srv.agent_port or 19100, srv.agent_token or "")
    if not data:
        raise HTTPException(502, "Agent连接失败，请检查Agent是否正常运行")
    
    return {
        "server_id": server_id,
        "containers": data.get("containers", []),
        "ports": data.get("ports", []),
        "systemd_services": data.get("systemd_services", []),
        "scan_time_ms": data.get("scan_time_ms", 0),
        "source": "agent",
    }


def _extract_public_ports(container):
    """Extract unique public host ports from Agent container data.
    
    Supports two formats from Agent v2.0:
    - Legacy: port_summary string like "0.0.0.0:3000->3000/tcp"
    - Agent v2.0: ports as integer list like [8000] or dict list like [{"port": 8000, "bind": "0.0.0.0"}]
    """
    ports_raw = container.get("ports", [])
    port_summary = container.get("port_summary", "")
    
    # Try port_summary first (Docker standard format)
    if port_summary:
        source = port_summary
        matches = re.findall(r'0\.0\.0\.0:(\d+)->|:::(\d+)->', source)
        if matches:
            return list(dict.fromkeys(m[0] or m[1] for m in matches if m[0] or m[1]))
    
    # Agent v2.0: ports is a list of integers or dicts
    result = []
    for p in ports_raw:
        if isinstance(p, int):
            # Integer port, include all (Agent only returns bound ports)
            result.append(str(p))
        elif isinstance(p, dict):
            # Dict format: {"port": 8000, "bind": "0.0.0.0"}
            bind = p.get("bind", "")
            port = p.get("port")
            if port and (bind == "0.0.0.0" or bind == "[::]" or bind.startswith("0.0.0.0") or bind.startswith("[::]")):
                result.append(str(port))
    
    return list(dict.fromkeys(result))


def _build_svc_url_for_remote(name, host, container):
    """Build service URL for remote server: get_url first, then port-based fallback.
    
    get_url returns relative paths like /gitea/ for CI/CD Nginx setup.
    For remote servers without such Nginx, we need http://host:port instead.
    """
    from app.discovery import get_url
    svc_url = get_url(name, host) or ''
    
    # If get_url returned a relative path, replace with host:port URL
    if svc_url.startswith('/'):
        public_ports = _extract_public_ports(container)
        if public_ports:
            # Skip SSH-like ports (22, 2222) for web URL
            web_ports = [p for p in public_ports if p not in ('22', '2222')]
            port = web_ports[0] if web_ports else (public_ports[0] if public_ports else None)
            if port:
                scheme = 'https' if port in ('443', '8443') else 'http'
                svc_url = f"{scheme}://{host}:{port}"
            else:
                svc_url = ''
        else:
            svc_url = ''
    elif not svc_url:
        # get_url returned nothing, try port-based URL
        public_ports = _extract_public_ports(container)
        if public_ports:
            web_ports = [p for p in public_ports if p not in ('22', '2222')]
            port = web_ports[0] if web_ports else (public_ports[0] if public_ports else None)
            if port:
                scheme = 'https' if port in ('443', '8443') else 'http'
                svc_url = f"{scheme}://{host}:{port}"
    
    return svc_url



def _extract_port_from_url(url):
    """Extract port number from a URL like http://127.0.0.1:9091/api/"""
    m = re.search(r':(\d+)(?:/|$)', url)
    if m:
        return int(m.group(1))
    if url.startswith('https://'):
        return 443
    if url.startswith('http://'):
        return 80
    return None


def _sync_nginx_routes(srv, db):
    """Discover and sync nginx routes with port-aware dedup.
    Returns dict {"added": int, "updated": int}.
    """
    added = 0
    updated = 0
    try:
        nginx_routes = parse_nginx_config(host=srv.host)
        for route in nginx_routes:
            ng_name = route.get("name", "")
            ng_url = route.get("url", "")
            proxy_pass = route.get("proxy_pass", "")
            if not ng_url:
                continue
            # Extract backend port from proxy_pass URL
            backend_port = _extract_port_from_url(proxy_pass) if proxy_pass else None
            # Extract domain from nginx URL
            ng_domain = ""
            dm = re.match(r'https?://([^/:]+)', ng_url)
            if dm:
                ng_domain = dm.group(1)
            # Dedup check 1: same port (cross-path dedup with port scan)
            existing = None
            if backend_port:
                existing = db.query(Service).filter(
                    Service.server_id == srv.id,
                    Service.port == backend_port,
                ).first()
            # Dedup check 2: same URL
            if not existing:
                existing = db.query(Service).filter(
                    Service.server_id == srv.id,
                    Service.url == ng_url,
                ).first()
            # Dedup check 3: same container_name (backward compat)
            cname = f"nginx:{ng_name}"
            if not existing:
                existing = db.query(Service).filter(
                    Service.server_id == srv.id,
                    Service.container_name == cname,
                ).first()
            if existing:
                # Already exists - enrich with nginx domain info
                changed = False
                if ng_domain and not existing.host_domain:
                    existing.host_domain = ng_domain
                    changed = True
                # Upgrade IP:port URL to domain URL (always, no url_overridden guard)
                if ng_url and ng_domain:
                    if srv.host in (existing.url or "") or "localhost" in (existing.url or "") or "127.0.0.1" in (existing.url or ""):
                        existing.url = ng_url
                        changed = True
                if backend_port and not existing.port:
                    existing.port = backend_port
                    existing.proto = "tcp"
                    changed = True
                if changed:
                    updated += 1
                continue
            # Create new service
            svc = Service(
                server_id=srv.id,
                name=ng_name,
                url=ng_url,
                source=ServiceSource.nginx.value,
                category="网络与代理",
                icon="fa-globe",
                container_name=cname,
                port=backend_port,
                proto="tcp" if backend_port else None,
                status=ServiceStatus.unknown.value,
                last_scanned_at=datetime.utcnow(),
                host_ip=srv.host,
                host_domain=ng_domain or getattr(srv, 'host_domain', None),
            )
            db.add(svc)
            added += 1
            _auto_assign_group(str(srv.id), str(svc.id), svc.category or "")
        db.commit()
    except Exception as e:
        print(f"[WARN] Nginx route sync failed: {e}")
    return {"added": added, "updated": updated}



# E2 fix: removed first duplicate _sync_port_driven_scan definition (was lines 1001-1278)
# The second definition at line ~1457 (now earlier) is the active one that calls _do_sync_ports_systemd


def _sync_agent_scan_to_db(srv, db, scan_data):
    """Sync Agent scan results (containers) into services table with source='agent'."""
    from app.discovery import classify_image, get_icon, get_desc
    containers = scan_data.get("containers", [])
    synced = 0
    updated = 0
    errors = 0
    for c in containers:
        try:
            name = c.get("name", "")
            image = c.get("image", "")
            is_running = c.get("status") == "running" or "Up" in c.get("status", "")
            # Use port_summary (compact) instead of str(ports) (verbose dict list)
            ports_display = c.get("port_summary", "") or ", ".join(_extract_public_ports(c))
            
            short_image = image.split(':')[0].split('/')[-1] if image else ''
            svc_name = name.replace('-', ' ').replace('_', ' ').title()
            svc_url = _build_svc_url_for_remote(name, srv.host, c)
            
            svc_category = classify_image(short_image)
            svc_icon = get_icon(short_image)
            svc_desc = get_desc(short_image, name)
            
            if not svc_url:
                continue
            
            existing = db.query(Service).filter(
                Service.server_id == srv.id,
                Service.container_name == name,
            ).first()
            if existing:
                changed = False
                for field, val in [("name", svc_name), ("url", svc_url), ("category", svc_category),
                                   ("icon", svc_icon), ("description", svc_desc), ("image", image),
                                   ("ports", ports_display), ("source", ServiceSource.agent.value)]:
                    if val and getattr(existing, field) != val:
                        setattr(existing, field, val)
                        changed = True
                existing.status = ServiceStatus.up.value if is_running else ServiceStatus.down.value
                existing.last_scanned_at = datetime.utcnow()
                if changed:
                    updated += 1
            else:
                # Get primary port for port-driven dedup
                _primary_port = None
                for _p in c.get("ports", []):
                    _hp = _p.get("host_port")
                    if _hp and str(_hp).isdigit():
                        _primary_port = int(_hp)
                        break
                svc = Service(
                    server_id=srv.id, name=svc_name, url=svc_url,
                    category=svc_category, icon=svc_icon, description=svc_desc,
                    source=ServiceSource.agent.value,
                    status=ServiceStatus.up.value if is_running else ServiceStatus.down.value,
                    container_name=name, image=image, ports=ports_display,
                    port=_primary_port, proto="tcp",
                    host_ip=srv.host,
                    host_domain=getattr(srv, 'host_domain', None),
                    last_scanned_at=datetime.utcnow(),
                )
                db.add(svc)
                synced += 1
                _auto_assign_group(str(srv.id), str(svc.id), svc_category)
        except Exception as e:
            print(f"[WARN] _sync_agent_scan_to_db skip container {c.get('name','?')}: {e}")
            errors += 1
    
    # Sync stopped containers
    stopped_data = scan_data.get('stopped_containers') or scan_data.get('stopped', [])
    if stopped_data:
        for cont in stopped_data:
            cname = cont.get('name', '')
            if not cname:
                continue
            existing = db.query(Service).filter(
                Service.server_id == srv.id,
                Service.container_name == cname
            ).first()
            if not existing:
                short_img = cont.get('image', '').split(':')[0].split('/')[-1] if cont.get('image') else ''
                new_svc = Service(
                    server_id=srv.id,
                    name=f"{cname.replace('-', ' ').replace('_', ' ').title()} [已停止]",
                    url="#none",
                    category=cont.get('category', '') or classify_image(short_img),
                    source='docker_auto',
                    container_name=cname,
                    image=cont.get('image', ''),
                    status='down',
                    ports=cont.get('ports', ''),
                    last_scanned_at=datetime.utcnow(),
                )
                db.add(new_svc)
                synced += 1
                _auto_assign_group(str(srv.id), str(new_svc.id), new_svc.category)

    # Sync Nginx-discovered services with port-aware dedup
    nginx_data = scan_data.get('nginx_services') or scan_data.get('nginx', [])
    if nginx_data:
        for ng in nginx_data:
            ng_name = ng.get('name', '')
            ng_url = ng.get('url', '')
            proxy_pass = ng.get('proxy_pass', '')
            if not ng_url:
                continue
            # Extract backend port from proxy_pass
            backend_port = _extract_port_from_url(proxy_pass) if proxy_pass else None
            # Extract domain
            ng_domain = ''
            dm = re.match(r'https?://([^/:]+)', ng_url)
            if dm:
                ng_domain = dm.group(1)
            cname = ng.get('container_name', f"nginx:{ng_name}")
            # Dedup check 1: same port
            existing = None
            if backend_port:
                existing = db.query(Service).filter(
                    Service.server_id == srv.id,
                    Service.port == backend_port,
                ).first()
            # Dedup check 2: same URL
            if not existing:
                existing = db.query(Service).filter(
                    Service.server_id == srv.id,
                    Service.url == ng_url,
                ).first()
            # Dedup check 3: same container_name
            if not existing:
                existing = db.query(Service).filter(
                    Service.server_id == srv.id,
                    Service.container_name == cname,
                ).first()
            if existing:
                changed = False
                if ng_domain and not existing.host_domain:
                    existing.host_domain = ng_domain
                    changed = True
                if ng_url and srv.host and srv.host in (existing.url or '') and ng_domain:
                    existing.url = ng_url
                    changed = True
                if backend_port and not existing.port:
                    existing.port = backend_port
                    existing.proto = "tcp"
                    changed = True
                for field, val in [('name', ng_name), ('category', ng.get('category'))]:
                    if val and getattr(existing, field) != val:
                        setattr(existing, field, val)
                        changed = True
                if changed:
                    updated += 1
            else:
                new_svc = Service(
                    server_id=srv.id,
                    name=ng_name,
                    url=ng_url,
                    category=ng.get('category', '网络与代理'),
                    source=ServiceSource.nginx.value,
                    container_name=cname,
                    port=backend_port,
                    proto="tcp" if backend_port else None,
                    host_ip=srv.host,
                    host_domain=ng_domain or None,
                    last_scanned_at=datetime.utcnow(),
                )
                db.add(new_svc)
                synced += 1
                _auto_assign_group(str(srv.id), str(new_svc.id), new_svc.category)

    return {"added": synced, "updated": updated, "errors": errors}


def _sync_port_driven_scan(srv, db, scan_data):
    """Sync Agent ports and systemd_services into services table."""
    try:
        return _do_sync_ports_systemd(srv, db, scan_data)
    except Exception as e:
        import traceback
        print(f"[ERROR] _sync_agent_ports_and_systemd failed: {e}")
        traceback.print_exc()
        return {"added": 0, "updated": 0}


def _do_sync_ports_systemd(srv, db, scan_data):
    """Impl: process ports and systemd_services from Agent scan."""
    from app.discovery import classify_image, get_icon, get_desc
    print(f"[DEBUG] _sync_agent_ports_and_systemd: containers={len(scan_data.get('containers',[]))} ports={len(scan_data.get('ports',[]))} systemd={len(scan_data.get('systemd_services',[]))}")
    
    containers = scan_data.get("containers", [])
    ports = scan_data.get("ports", [])
    systemd_services = scan_data.get("systemd_services", [])

    # Build nginx port -> domain mapping for URL enrichment
    nginx_port_map = {}  # port -> {'domain': str, 'url': str, 'name': str}
    try:
        nginx_svcs = scan_data.get('nginx_services', [])
        if not nginx_svcs:
            nginx_svcs = parse_nginx_config(host=srv.host)
        for ng in nginx_svcs:
            ng_url = ng.get('url', '')
            proxy_pass = ng.get('proxy_pass', '')
            bp = _extract_port_from_url(proxy_pass) if proxy_pass else _extract_port_from_url(ng_url)
            if bp:
                dm = re.match(r'https?://([^/:]+)', ng_url)
                nginx_port_map[bp] = {
                    'domain': dm.group(1) if dm else '',
                    'url': ng_url,
                    'name': ng.get('name', ''),
                }
    except Exception as e:
        print(f"[DEBUG] nginx_port_map build failed: {e}")

    # Collect ports already covered by known containers
    container_ports = set()
    for c in containers:
        for p in c.get("ports", []):
            hp = p.get("host_port")
            if hp:
                container_ports.add(int(hp) if str(hp).isdigit() else 0)
    
    # Collect existing container_name entries to avoid duplicates
    existing_container_names = set()
    for svc in db.query(Service).filter(Service.server_id == srv.id,
                                         Service.container_name.isnot(None)).all():
        existing_container_names.add(svc.container_name)
    
    added = 0
    updated = 0
    
    # --- Process systemd services (non-system, user-facing) ---
    valid_systemd = []
    for svc_info in systemd_services:
        name = svc_info.get("name", "")
        # Skip system-level services by prefix matching
        if any(name.startswith(prefix) for prefix in _SKIP_SYSTEMD_PREFIXES):
            continue
        desc = svc_info.get("description", "")
        # Also skip if already tracked as a container
        # Convert service name to possible container name (e.g. opscenter-backend -> opscenter-backend)
        if name.replace(".service", "") in existing_container_names:
            continue
        status_str = svc_info.get("status", "unknown")
        valid_systemd.append(svc_info)
    
    # --- Process listening ports ---
    # Group ports by process to avoid duplicates
    port_services = {}  # process -> list of port info
    for p in ports:
        port_num = p.get("port", 0)
        proto = p.get("proto", "tcp")
        bind_ip = p.get("bind_ip", "")
        process = p.get("process", "unknown")
        
        # Process both TCP and UDP ports
        if process in _SKIP_PROCESSES:
            continue
        if proto not in ('tcp', 'udp'):
            continue
        # Skip system/ephemeral ports
        if port_num in _SKIP_PORTS or port_num > 60000:
            continue
        # Skip ports already covered by containers
        if port_num in container_ports:
            continue
        # Skip localhost-only ports from docker-proxy (those are container mappings)
        if bind_ip.startswith("127.0.0.1") and process == "docker-proxy":
            continue
        
        if process not in port_services:
            port_services[process] = []
        port_services[process].append(p)
    
    # --- Create/update service entries from port_services ---
    # Build a map of container_name -> Service for dedup
    existing_by_source = {}
    for svc in db.query(Service).filter(Service.server_id == srv.id).all():
        key = (svc.source, svc.container_name or svc.name)
        existing_by_source[key] = svc
    
    for process, port_list in port_services.items():
        # Pick the most interesting port (lowest public port, prefer TCP over UDP)
        public_ports = [p for p in port_list if p.get("bind_ip", "") == "0.0.0.0"]
        if not public_ports:
            # Use localhost ports only if they look like real services
            local_ports = [p for p in port_list if p.get("bind_ip", "").startswith("127.0.0.1")]
            if not local_ports:
                continue
            chosen = min(local_ports, key=lambda x: x.get("port", 99999))
        else:
            tcp_ports = [p for p in public_ports if p.get("proto", "tcp") == "tcp"]
            chosen = min(tcp_ports if tcp_ports else public_ports, key=lambda x: x.get("port", 99999))

        port_num = chosen.get("port", 0)
        bind_ip = chosen.get("bind_ip", "0.0.0.0")
        proto = chosen.get("proto", "tcp")

        # UDP ports: use special hints
        if proto == "udp":
            udp_hint = _UDP_SERVICE_HINTS.get(port_num, {})
            svc_name = udp_hint.get("name", f"{process.replace('-', ' ').replace('_', ' ').title()} (UDP)")
            svc_category = udp_hint.get("category", "网络与代理")
            svc_icon = "fa-network-wired"
            svc_desc = f"UDP {port_num}/{process}"
            svc_url = udp_hint.get("url", "")
        else:
            # Use port hints if available
            hint = _PORT_SERVICE_HINTS.get(port_num, {})
            svc_name = hint.get("name", process.replace("-", " ").replace("_", " ").title())
            svc_category = hint.get("category", classify_image(process))
            svc_icon = hint.get("icon", get_icon(process))
            svc_desc = hint.get("desc", get_desc(process, process))

            # v3.21.0: Protocol-aware URL generation (C3 fix)
            if hint:
                svc_url = hint["url_tpl"].format(host=srv.host)
            else:
                host = srv.host
                # Determine protocol by port
                proto_scheme = "https" if port_num in (443, 8443) else "http"
                svc_url = f"{proto_scheme}://{host}:{port_num}/"

        # Upgrade localhost/127.0.0.1 URLs to nginx domain URLs (C4 fix)
        if port_num in nginx_port_map:
            ng_info = nginx_port_map[port_num]
            if ng_info['domain'] and ng_info['domain'] != srv.host:
                ng_url = ng_info['url']
                if ng_url.startswith('http://'):
                    ng_url = ng_url.replace('http://', 'https://', 1)
                # Replace IP:port or localhost URLs with domain URL
                if not hint:
                    svc_url = ng_url

        # Skip if no meaningful URL
        if not svc_url:
            continue
        
        # Dedup key: source=agent, container_name=process name or port-based key (include proto for UDP)
        dedup_key = f"port:{process}:{port_num}" + (f"/{proto}" if proto == "udp" else "")
        existing = db.query(Service).filter(
            Service.server_id == srv.id,
            Service.container_name == dedup_key,
        ).first()
        
        if existing:
            changed = False
            for field, val in [("name", svc_name), ("url", svc_url), ("category", svc_category),
                               ("icon", svc_icon), ("description", svc_desc),
                               ("ports", str(port_num)), ("source", ServiceSource.agent.value)]:
                if val and getattr(existing, field) != val:
                    setattr(existing, field, val)
                    changed = True
            existing.status = ServiceStatus.up.value
            existing.last_scanned_at = datetime.utcnow()
            if changed:
                updated += 1
        else:
            svc = Service(
                server_id=srv.id, name=svc_name, url=svc_url,
                category=svc_category, icon=svc_icon, description=svc_desc,
                source=ServiceSource.agent.value,
                status=ServiceStatus.up.value,
                container_name=dedup_key, ports=str(port_num),
                last_scanned_at=datetime.utcnow(),
            )
            db.add(svc)
            added += 1
            _auto_assign_group(str(srv.id), str(svc.id), svc_category)
    
    # --- Process systemd services without ports ---
    for svc_info in valid_systemd:
        name = svc_info.get("name", "").replace(".service", "")
        desc = svc_info.get("description", "")
        status_str = svc_info.get("status", "unknown")
        
        # Skip if already covered by a port-based or container entry
        # Check port-based dedup key
        dedup_key = f"systemd:{name}"
        existing = db.query(Service).filter(
            Service.server_id == srv.id,
            Service.container_name == dedup_key,
        ).first()
        if existing:
            existing.status = ServiceStatus.up.value if status_str == "active" else ServiceStatus.down.value
            existing.last_scanned_at = datetime.utcnow()
            continue
        # Also skip if a matching service already exists by name or keyword
        name_lower = name.replace("-", " ").replace("_", " ").lower()
        skip_keywords = ["nginx", "opsagent", "opscenter", "2fauth", "docker", "1panel"]
        similar = None
        for kw in skip_keywords:
            if kw in name_lower:
                similar = db.query(Service).filter(
                    Service.server_id == srv.id,
                    Service.name.ilike(f"%{kw}%"),
                ).first()
                if similar:
                    break
        if similar:
            continue
        
        # Create entry (no URL for systemd-only services)
        svc_name = name.replace("-", " ").replace("_", " ").title()
        svc_category = classify_image(name)
        svc_icon = get_icon(name)
        
        svc = Service(
            server_id=srv.id, name=svc_name, url=f"#systemd:{name}",
            category=svc_category, icon=svc_icon, description=desc,
            source=ServiceSource.agent.value,
            status=ServiceStatus.up.value if status_str == "active" else ServiceStatus.down.value,
            container_name=dedup_key,
            last_scanned_at=datetime.utcnow(),
        )
        db.add(svc)
        added += 1
        _auto_assign_group(str(srv.id), str(svc.id), svc_category)
    
    return {"added": added, "updated": updated}


def _sync_ssh_containers_to_db(srv, db, client):
    """Fallback: SSH-based Docker container discovery, sync to DB."""
    from app.discovery import classify_image, get_icon, get_desc, get_url
    containers = discover_remote_docker_services(client, host=srv.host)
    synced = 0
    updated = 0
    errors = 0
    for c in containers:
        try:
            name = c.get('name', '')
            image = c.get('image', '')
            status_str = c.get('status', '')
            ports = c.get('ports', '')
            is_running = 'Up' in status_str
            
            short_image = image.split(':')[0].split('/')[-1] if image else ''
            svc_name = name.replace('-', ' ').replace('_', ' ').title()
            svc_url = c.get('auto_url', '') or get_url(name, srv.host) or ''
            # If get_url returns relative path, try auto_url port-based fallback
            if svc_url.startswith('/') and c.get('auto_url', ''):
                svc_url = c.get('auto_url', '')
            elif svc_url.startswith('/'):
                svc_url = ''  # No valid remote URL for relative path without Nginx
            svc_category = classify_image(short_image)
            svc_icon = get_icon(short_image)
            svc_desc = get_desc(short_image, name)
            
            if not svc_url:
                continue
            
            existing = db.query(Service).filter(
                Service.server_id == srv.id,
                Service.container_name == name,
            ).first()
            if existing:
                for field, val in [("name", svc_name), ("url", svc_url), ("category", svc_category),
                                   ("icon", svc_icon), ("description", svc_desc), ("image", image), ("ports", ports)]:
                    if val and getattr(existing, field) != val:
                        setattr(existing, field, val)
                existing.status = ServiceStatus.up.value if is_running else ServiceStatus.down.value
                existing.last_scanned_at = datetime.utcnow()
                updated += 1
            else:
                svc = Service(
                    server_id=srv.id, name=svc_name, url=svc_url,
                    category=svc_category, icon=svc_icon, description=svc_desc,
                    source=ServiceSource.docker_auto.value,
                    status=ServiceStatus.up.value if is_running else ServiceStatus.down.value,
                    container_name=name, image=image, ports=ports,
                    last_scanned_at=datetime.utcnow(),
                )
                db.add(svc)
                synced += 1
                _auto_assign_group(str(srv.id), str(svc.id), svc_category)
        except Exception as e:
            print(f"[WARN] _sync_ssh_containers_to_db skip container {c.get('name','?')}: {e}")
            errors += 1
    
    return {"added": synced, "updated": updated, "errors": errors}


@app.post("/api/v2/servers/{server_id}/scan-services")
def scan_server_services(server_id: str, password: Optional[str] = None):
    """Scan services on a server using Agent (preferred) or SSH fallback, and sync to DB. Unified for all servers."""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        
        agent_host = "127.0.0.1" if srv.agent_type == "local" else srv.host
        
        # Unified: all servers use Agent-first approach
        if srv.agent_status == "running" and srv.agent_port:
            scan_data = trigger_agent_scan(agent_host, srv.agent_port or 19100, srv.agent_token or "")
            if scan_data:
                result = _sync_agent_scan_to_db(srv, db, scan_data)
                port_result = _sync_port_driven_scan(srv, db, scan_data)
                srv.status = ServerStatus.online.value
                srv.last_seen = datetime.utcnow()
                srv.docker_available = True
                db.commit()
                return {
                    "discovered": result["added"] + result["updated"] + port_result["added"] + port_result["updated"],
                    "added": result["added"] + port_result["added"],
                    "updated": result["updated"] + port_result["updated"],
                    "source": "agent",
                    "containers": len(scan_data.get("containers", [])),
                }
        
        # Fallback: Docker SDK (local) or SSH (remote)
        if srv.agent_type == "local":
            discovered = discover_docker_services(srv, db, srv.host)
            for d in discovered:
                _auto_assign_group(str(srv.id), str(d.id), d.category or "")
            nginx_result = _sync_nginx_routes(srv, db)
            nginx_added = nginx_result["added"] + nginx_result["updated"]
            srv.last_seen = datetime.utcnow()
            db.commit()
            result_count = len(discovered) + nginx_added
            return {"discovered": result_count, "source": "docker_local", "added": result_count, "updated": 0, "nginx_added": nginx_added}
        
        # Remote SSH fallback
        client = get_ssh_client(srv, password=password)
        if not client:
            raise HTTPException(400, "Agent不可用且SSH连接失败，请检查凭证")
        try:
            result = _sync_ssh_containers_to_db(srv, db, client)
            srv.status = ServerStatus.online.value
            srv.last_seen = datetime.utcnow()
            srv.docker_available = True
            db.commit()
            return {
                "discovered": result["added"] + result["updated"],
                "added": result["added"],
                "updated": result["updated"],
                "source": "ssh",
            }
        except Exception as e:
            raise HTTPException(500, f"SSH扫描失败: {e}")
        finally:
            try:
                client.close()
            except:
                pass


# === Service APIs ===
@app.get("/api/v2/services")
def list_services(server_id: Optional[str] = None, category: Optional[str] = None, pinned: Optional[bool] = None, search: Optional[str] = None):
    with get_db() as db:
        q = db.query(Service)
        if server_id:
            q = q.filter(Service.server_id == uuid.UUID(server_id))
        if category:
            q = q.filter(Service.category == category)
        if pinned is not None:
            q = q.filter(Service.pinned == pinned)
        if search:
            q = q.filter(Service.name.ilike(f"%{search}%"))
        # Only show services that have a web-accessible URL and are not hidden
        q = q.filter(Service.url != None, Service.url != '', Service.hidden != True)
        q = q.order_by(Service.sort_order, Service.category, Service.name)
        services = q.all()
        result = []
        for s in services:
            result.append({
                "id": str(s.id), "server_id": str(s.server_id),
                "name": s.name, "url": s.url, "category": s.category,
                "icon": s.icon, "description": s.description,
                "source": s.source, "status": s.status, "pinned": s.pinned,
                "health_path": s.health_path, "container_name": s.container_name,
                "image": s.image, "ports": s.ports,
                "url_overridden": False,  # deprecated, always False
                "port": s.port,
                "proto": s.proto or "tcp",
                "host_ip": s.host_ip,
                "host_domain": s.host_domain,
            })
        return result

@app.post("/api/v2/services", status_code=201)
def create_service(data: ServiceCreate, server_id: str = Query(...)):
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        svc = Service(
            server_id=srv.id, name=data.name, url=data.url,
            category=data.category, icon=data.icon, description=data.description,
            health_path=data.health_path, pinned=data.pinned,
            source=ServiceSource.manual.value, status=ServiceStatus.unknown.value,
        )
        db.add(svc)
        db.commit()
        db.refresh(svc)
        return {"id": str(svc.id), "name": svc.name}

@app.put("/api/v2/services/{service_id}")
def update_service(service_id: str, data: ServiceUpdate):
    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
        if not svc:
            raise HTTPException(404, "Service not found")
        # Save original url BEFORE setattr modifies it
        original_url = svc.url
        for field, val in data.model_dump(exclude_unset=True).items():
            if val is not None:
                setattr(svc, field, val)
        # Handle account/password explicitly (allow empty string to clear)
        if 'account' in data.model_dump(exclude_unset=True):
            acct = data.model_dump(exclude_unset=True).get('account')
            svc.account = acct if acct else ''
        if 'password' in data.model_dump(exclude_unset=True):
            pwd = data.model_dump(exclude_unset=True).get('password')
            svc.password = pwd if pwd else ''
        db.commit()
        return {"ok": True}

@app.delete("/api/v2/services/{service_id}")
def delete_service(service_id: str):
    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
        if not svc:
            raise HTTPException(404, "Service not found")
        if svc.source == ServiceSource.docker_label.value:
            raise HTTPException(400, "Cannot delete auto-discovered service (disable label instead)")
        db.delete(svc)
        db.commit()
        return {"ok": True}

@app.patch("/api/v2/services/{service_id}/pin")
def toggle_pin(service_id: str):
    """Toggle service pin status."""
    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uuid.UUID(service_id)).first()
        if not svc:
            raise HTTPException(404, "Service not found")
        svc.pinned = not svc.pinned
        db.commit()
        return {"ok": True, "pinned": svc.pinned}




# === Network Monitoring API (v3.25.1 Phase 2.1) ===
def _agent_request(server, path, timeout=5):
    """向 Agent 发起 HTTP 请求，返回 JSON 或 None。"""
    import requests as req
    host = "127.0.0.1" if server.agent_type == "local" else server.host
    port = server.agent_port or 19100
    token = server.agent_token or ""
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = req.get(f"http://{host}:{port}{path}", headers=headers, timeout=timeout)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


@app.get("/api/v2/monitor/{server_id}/network")
def monitor_network(server_id: str):
    """实时带宽 + 今日累计流量（来自 Agent /api/v1/network）。

    today_daily 为各网卡今日累计字节（daily_rx_bytes/daily_tx_bytes 汇总），
    兼容 network.html 的契约；interfaces 保留供 index.html 与实时速率使用。
    """
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
    data = _agent_request(srv, "/api/v1/network")
    if data is None:
        return {"server_id": server_id, "connected": False, "interfaces": {}, "today_daily": {}}
    ifaces = data.get("interfaces") or {}
    today_daily = {
        name: {
            "rx_bytes": st.get("daily_rx_bytes", 0),
            "tx_bytes": st.get("daily_tx_bytes", 0),
        }
        for name, st in ifaces.items()
    }
    return {"server_id": server_id, "connected": True, **data, "today_daily": today_daily}


@app.get("/api/v2/monitor/{server_id}/network/history")
def monitor_network_history(server_id: str, days: int = 7):
    """N 天流量趋势（network_stats 表按日聚合）。"""
    from datetime import timedelta
    days = min(max(days, 1), 90)
    since = datetime.utcnow().date() - timedelta(days=days - 1)
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        rows = db.query(NetworkStats).filter(
            NetworkStats.server_id == srv.id,
            NetworkStats.date >= since,
        ).all()
    by_date = {}
    for r in rows:
        d = str(r.date)
        if d not in by_date:
            by_date[d] = {"rx_bytes": 0, "tx_bytes": 0, "peak_rx_mbps": 0.0, "peak_tx_mbps": 0.0}
        by_date[d]["rx_bytes"] += r.rx_bytes or 0
        by_date[d]["tx_bytes"] += r.tx_bytes or 0
        by_date[d]["peak_rx_mbps"] = max(by_date[d]["peak_rx_mbps"], r.peak_rx_mbps or 0)
        by_date[d]["peak_tx_mbps"] = max(by_date[d]["peak_tx_mbps"], r.peak_tx_mbps or 0)
    result = [{"date": d, **v} for d, v in sorted(by_date.items())]
    # series 为 history 的别名（network.html 读 d.series，index.html 读 d.history，双兼容）
    return {"server_id": server_id, "days": days, "history": result, "series": result}


@app.get("/api/v2/monitor/{server_id}/network/latency")
def monitor_network_latency(server_id: str, target: str = "8.8.8.8", days: int = 7):
    """延迟/丢包探测：按 days 返回历史 points（network_latency 表），300s 限频防重复探测。

    - points: 近 N 天全部探测点（network.html 契约）
    - latency_ms/loss_pct/jitter_ms/timestamp: 最新一个点（index.html 旧契约，双兼容）
    """
    from datetime import timedelta
    days = min(max(days, 1), 30)
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        since = datetime.utcnow() - timedelta(days=days)
        rows = db.query(NetworkLatency).filter(
            NetworkLatency.server_id == srv.id,
            NetworkLatency.target == target,
            NetworkLatency.timestamp >= since,
        ).order_by(NetworkLatency.timestamp.asc()).all()
        points = [{
            "timestamp": r.timestamp.isoformat(),
            "latency_ms": r.latency_ms,
            "loss_pct": r.loss_pct or 0,
            "jitter_ms": r.jitter_ms,
        } for r in rows]
        # 限频：300s 内已有探测记录则直接返回，不重复探测
        cutoff = datetime.utcnow() - timedelta(seconds=300)
        recent = db.query(NetworkLatency).filter(
            NetworkLatency.server_id == srv.id,
            NetworkLatency.target == target,
            NetworkLatency.timestamp >= cutoff,
        ).order_by(NetworkLatency.timestamp.desc()).first()
        if recent:
            return {
                "server_id": server_id, "cached": True,
                "target": target,
                "latency_ms": recent.latency_ms,
                "loss_pct": recent.loss_pct or 0,
                "jitter_ms": recent.jitter_ms,
                "timestamp": recent.timestamp.timestamp(),
                "points": points,
            }
    data = _agent_request(srv, f"/api/v1/network/ping?target={target}", timeout=15)
    if data is None:
        latest = points[-1] if points else None
        return {
            "server_id": server_id, "connected": False,
            "target": target,
            "latency_ms": latest["latency_ms"] if latest else None,
            "loss_pct": latest["loss_pct"] if latest else 0,
            "jitter_ms": latest["jitter_ms"] if latest else None,
            "timestamp": (latest["timestamp"] if latest else None),
            "points": points,
        }
    with get_db() as db:
        db.add(NetworkLatency(
            server_id=srv.id,
            target=target,
            latency_ms=data.get("latency_ms"),
            loss_pct=data.get("loss_pct", 0),
            jitter_ms=data.get("jitter_ms"),
        ))
        db.commit()
    points.append({
        "timestamp": datetime.utcnow().isoformat(),
        "latency_ms": data.get("latency_ms"),
        "loss_pct": data.get("loss_pct", 0),
        "jitter_ms": data.get("jitter_ms"),
    })
    return {
        "server_id": server_id, "cached": False,
        "target": target,
        "latency_ms": data.get("latency_ms"),
        "loss_pct": data.get("loss_pct", 0),
        "jitter_ms": data.get("jitter_ms"),
        "timestamp": datetime.utcnow().timestamp(),
        "points": points,
    }


async def daily_network_aggregation():
    """每日 00:05 从各 Agent 拉取当日累计流量，upsert 进 network_stats。"""
    from datetime import timedelta
    import requests as req
    while True:
        now = datetime.utcnow()
        # 距下一个 00:05 的秒数
        nxt = now.replace(hour=0, minute=5, second=0, microsecond=0)
        if now >= nxt:
            nxt = nxt + timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            with get_db() as db:
                servers = db.query(Server).filter(Server.enabled == True).all()
                for srv in servers:
                    data = _agent_request(srv, "/api/v1/network")
                    if not data:
                        continue
                    today = datetime.utcnow().date()
                    for iface, st in (data.get("interfaces") or {}).items():
                        existing = db.query(NetworkStats).filter(
                            NetworkStats.server_id == srv.id,
                            NetworkStats.date == today,
                            NetworkStats.interface == iface,
                        ).first()
                        if existing:
                            existing.rx_bytes = st.get("daily_rx_bytes", 0)
                            existing.tx_bytes = st.get("daily_tx_bytes", 0)
                            existing.peak_rx_mbps = max(existing.peak_rx_mbps or 0, st.get("rx_rate_mbps", 0))
                            existing.peak_tx_mbps = max(existing.peak_tx_mbps or 0, st.get("tx_rate_mbps", 0))
                        else:
                            db.add(NetworkStats(
                                server_id=srv.id, date=today, interface=iface,
                                rx_bytes=st.get("daily_rx_bytes", 0),
                                tx_bytes=st.get("daily_tx_bytes", 0),
                                rx_packets=st.get("rx_packets", 0),
                                tx_packets=st.get("tx_packets", 0),
                                rx_errors=st.get("rx_errors", 0),
                                tx_errors=st.get("tx_errors", 0),
                                peak_rx_mbps=st.get("rx_rate_mbps", 0),
                                peak_tx_mbps=st.get("tx_rate_mbps", 0),
                            ))
                db.commit()
        except Exception as e:
            print(f"[network-aggregation] error: {e}", flush=True)


# === Per-Server Health Check (v3.25 Phase 2.2) ===
def _service_probe_allowed(url: str, srv) -> bool:
    """SSRF 防护：仅允许探测目标服务器登记的 host（白名单）。"""
    if url.startswith("#"):
        return False
    if not url.startswith(("http://", "https://")):
        return False
    from urllib.parse import urlparse
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    allow_hosts = {h for h in (srv.host, getattr(srv, "host_ip", None), getattr(srv, "host_domain", None)) if h}
    if srv.agent_type == "local":
        allow_hosts.update({"127.0.0.1", "localhost", LOCAL_HOST})
    return host in allow_hosts


@app.get("/api/v2/monitor/{server_id}/health-check")
def monitor_health_check(server_id: str):
    """对指定服务器的全部服务做 HTTP 探活，返回每服务实际状态码与耗时。

    SSRF 防护：仅探测该服务器登记的 host（白名单），其余服务标记 skipped。
    """
    import requests as req
    import time
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        services = db.query(Service).filter(Service.server_id == srv.id, Service.hidden != True).all()
        results = []
        for svc in services:
            url = svc.url or ""
            entry = {"id": str(svc.id), "name": svc.name, "url": url,
                     "status": "skipped", "status_code": None, "latency_ms": None}
            if url.startswith("#") or not url:
                results.append(entry)
                continue
            # 相对路径：拼接服务器 host（host 来自白名单内的 server 记录）
            if not url.startswith(("http://", "https://")):
                if url.startswith("/"):
                    base = f"http://{srv.host}"
                    url = f"{base.rstrip('/')}{url}"
                else:
                    results.append(entry)  # 非 HTTP 协议跳过（TCP 探活走 /health-check 全局端点）
                    continue
            if not _service_probe_allowed(url, srv):
                entry["status"] = "blocked"  # SSRF 白名单拦截
                results.append(entry)
                continue
            t0 = time.time()
            try:
                resp = req.get(url, timeout=5, stream=True, verify=False, allow_redirects=True,
                               headers={"User-Agent": "OpsCenter-HealthCheck/3.25"})
                entry["status_code"] = resp.status_code
                entry["status"] = "up" if resp.status_code < 500 else "down"
            except Exception:
                entry["status"] = "down"
            finally:
                entry["latency_ms"] = round((time.time() - t0) * 1000, 1)
            results.append(entry)
        return {"server_id": server_id, "server_name": srv.name, "services": results}


# === Health Check Trigger ===
@app.post("/api/v2/health-check")
def trigger_health_check():
    """Manually trigger the same coordinator used by the background loop."""
    checked = run_service_health_cycle()
    return {"checked": checked, "message": f"Health check completed for {checked} services"}


@app.post("/api/v2/servers/{server_id}/ssh-test")
def ssh_test(server_id: str, password: Optional[str] = None):
    """Test SSH connection and auto-scan if successful."""
    from app.discovery import classify_image, get_icon, get_desc, get_url
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.agent_type == "local":
            return {"success": True, "message": "本机服务器无需SSH"}
        
        client = get_ssh_client(srv, password=password)
        if not client:
            return {"success": False, "message": "SSH连接失败，请检查账号密码"}
        
        # Save credentials
        if password:
            srv.ssh_key = f"__password__{password}"
        srv.status = ServerStatus.online.value
        srv.last_seen = datetime.utcnow()
        
        # Auto-discover
        containers = discover_remote_docker_services(client, host=srv.host)
        count = 0
        for c in containers:
            name = c.get('name', '')
            image = c.get('image', '')
            status_str = c.get('status', '')
            ports = c.get('ports', '')
            is_running = c.get('is_running', 'Up' in status_str)
            
            short_image = image.split(':')[0].split('/')[-1] if image else ''
            svc_name = name.replace('-', ' ').replace('_', ' ').title()
            svc_url = c.get('auto_url', '') or get_url(name, srv.host) or ''
            svc_category = classify_image(short_image)
            svc_icon = get_icon(short_image)
            svc_desc = get_desc(short_image, name)
            
            if not svc_url:
                continue
            
            existing = db.query(Service).filter(
                Service.server_id == srv.id,
                Service.container_name == name,
            ).first()
            if existing:
                for field, val in [("name", svc_name), ("url", svc_url), ("category", svc_category), ("icon", svc_icon), ("description", svc_desc), ("image", image), ("ports", ports)]:
                    if val and getattr(existing, field) != val:
                        setattr(existing, field, val)
                existing.status = ServiceStatus.up.value if is_running else ServiceStatus.down.value
            else:
                svc = Service(
                    server_id=srv.id, name=svc_name, url=svc_url,
                    category=svc_category, icon=svc_icon, description=svc_desc,
                    source=ServiceSource.docker_auto.value,
                    status=ServiceStatus.up.value if is_running else ServiceStatus.down.value,
                    container_name=name, image=image, ports=ports,
                )
                db.add(svc)
            count += 1
        
        srv.docker_available = True
        db.commit()
        client.close()
        return {"success": True, "message": f"SSH连接成功，发现 {count} 个服务", "discovered": count}

# === Scan & Discovery ===
@app.post("/api/v2/scan")
def scan_all():
    with get_db() as db:
        total_added = 0
        total_updated = 0
        server_results = []
        # Local servers
        servers = db.query(Server).filter(Server.agent_type == "local").all()
        for srv in servers:
            sr_detail = {"server_id": str(srv.id), "name": srv.name, "host": srv.host, "source": "docker_local", "added": 0, "updated": 0, "status": "ok"}
            if srv.docker_available:
                discovered = discover_docker_services(srv, db, srv.host)
                for d in discovered:
                    _auto_assign_group(str(srv.id), str(d.id), d.category or "")
                nginx_cnt = 0
                try:
                    nginx_routes = parse_nginx_config(host=srv.host)
                    for route in nginx_routes:
                        existing = db.query(Service).filter(
                            Service.server_id == srv.id,
                            Service.url == route["url"],
                        ).first()
                        if not existing:
                            ns = Service(
                                server_id=srv.id,
                                name=route["name"],
                                url=route["url"],
                                source=ServiceSource.nginx.value,
                                category="网络与代理",
                                icon="fa-globe",
                                status=ServiceStatus.unknown.value,
                                last_scanned_at=datetime.utcnow(),
                            )
                            db.add(ns)
                            nginx_cnt += 1
                            _auto_assign_group(str(srv.id), str(ns.id), ns.category)
                except Exception as e:
                    print(f"[WARN] scan_all nginx parse: {e}")
                srv.last_seen = datetime.utcnow()
                sr_detail["added"] = len(discovered) + nginx_cnt
                sr_detail["nginx_added"] = nginx_cnt
                total_added += len(discovered) + nginx_cnt
            server_results.append(sr_detail)
        # Remote servers: try Agent first, then SSH fallback
            # Check local Agent status too
            local_srv = db.query(Server).filter(Server.agent_type == "local").first()
            if local_srv:
                try:
                    local_data = fetch_agent_metrics("127.0.0.1", local_srv.agent_port or 19100, local_srv.agent_token or "")
                    if local_data:
                        local_srv.agent_status = "running"
                        local_srv.agent_version = local_data.get("agent_version", local_srv.agent_version or "")
                        local_srv.last_seen = datetime.utcnow()
                    else:
                        local_srv.agent_status = "stopped"
                except Exception as e:
                    print(f"[AgentHealthCheck] Local Agent error: {e}")
                    local_srv.agent_status = "stopped"

        remote_servers = db.query(Server).filter(Server.agent_type != "local").all()
        for srv in remote_servers:
            sr_detail = {"server_id": str(srv.id), "name": srv.name, "host": srv.host, "source": "", "added": 0, "updated": 0, "status": "ok"}
            try:
                # Try Agent if running
                if srv.agent_status == "running" and srv.agent_port:
                    scan_data = trigger_agent_scan(srv.host, srv.agent_port or 19100, srv.agent_token or "")
                    if scan_data:
                        result = _sync_agent_scan_to_db(srv, db, scan_data)
                        _sync_port_driven_scan(srv, db, scan_data)
                        srv.status = ServerStatus.online.value
                        srv.last_seen = datetime.utcnow()
                        srv.docker_available = True
                        total_added += result["added"]
                        total_updated += result["updated"]
                        sr_detail["source"] = "agent"
                        sr_detail["added"] = result["added"]
                        sr_detail["updated"] = result["updated"]
                        db.commit()
                        server_results.append(sr_detail)
                        continue
                # Agent not available, fallback to SSH
                client = get_ssh_client(srv)
                if not client:
                    sr_detail["status"] = "skipped"
                    sr_detail["source"] = "ssh_unavailable"
                    server_results.append(sr_detail)
                    continue
                try:
                    result = _sync_ssh_containers_to_db(srv, db, client)
                    srv.status = ServerStatus.online.value
                    srv.last_seen = datetime.utcnow()
                    srv.docker_available = True
                    total_added += result["added"]
                    total_updated += result["updated"]
                    sr_detail["source"] = "ssh"
                    sr_detail["added"] = result["added"]
                    sr_detail["updated"] = result["updated"]
                    db.commit()
                finally:
                    try:
                        client.close()
                    except:
                        pass
            except Exception as e:
                print(f"Remote scan error for {srv.host}: {e}")
                sr_detail["status"] = "error"
                sr_detail["source"] = sr_detail["source"] or "unknown"
                sr_detail["error"] = str(e)
            server_results.append(sr_detail)
        return {"discovered": total_added + total_updated, "added": total_added, "updated": total_updated, "servers": server_results, "message": f"Scan complete, {total_added} added, {total_updated} updated"}


# === Monitor (Prometheus proxy) ===
@app.get("/api/v2/servers/{server_id}/monitor")
@app.get("/api/v2/monitor/{server_id}")
def get_monitor(server_id: str):
    """Get real monitoring data for a server via Agent (all servers)."""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
    
    # All servers: try Agent first
    if srv.agent_status == "running" and srv.agent_port:
        agent_host = "127.0.0.1" if srv.agent_type == "local" else srv.host
        agent_data = fetch_agent_metrics(agent_host, srv.agent_port or 19100, srv.agent_token or "")
        if agent_data:
            containers = agent_data.get("containers", [])
            # Calculate rate metrics from cumulative Agent values
            _cur_net_rx = agent_data.get("net_rx_bytes", 0) or 0
            _cur_net_tx = agent_data.get("net_tx_bytes", 0) or 0
            _cur_disk_read = agent_data.get("disk_read_bytes", 0) or 0
            _cur_disk_write = agent_data.get("disk_write_bytes", 0) or 0
            _net_rx_rate = 0.0
            _net_tx_rate = 0.0
            _disk_read_rate = 0.0
            _disk_write_rate = 0.0
            with get_db() as db:
                from sqlalchemy import func as _sa_func
                for _mn, _cv in [("net_rx_raw", _cur_net_rx), ("net_tx_raw", _cur_net_tx), ("disk_read_raw", _cur_disk_read), ("disk_write_raw", _cur_disk_write)]:
                    _last = db.query(MetricHistory).filter(
                        MetricHistory.server_id == srv.id,
                        MetricHistory.metric == _mn,
                    ).order_by(MetricHistory.timestamp.desc()).first()
                    if _last and _last.value:
                        _elapsed = (datetime.utcnow() - _last.timestamp).total_seconds()
                        if _elapsed > 0:
                            _rv = max(0, (_cv - _last.value) / _elapsed)
                        else:
                            _rv = 0
                    else:
                        _rv = 0
                    if _mn == "net_rx_raw": _net_rx_rate = _rv
                    elif _mn == "net_tx_raw": _net_tx_rate = _rv
                    elif _mn == "disk_read_raw": _disk_read_rate = _rv
                    elif _mn == "disk_write_raw": _disk_write_rate = _rv
            
            normalized = {
                "cpu": agent_data.get("cpu_percent", 0),
                "cpu_count": agent_data.get("cpu_count", 0),
                "memory": agent_data.get("memory_percent", 0),
                "memory_total": agent_data.get("memory_total", 0),
                "memory_used": agent_data.get("memory_used", 0),
                "memory_avail": agent_data.get("memory_available", 0),
                "disk": agent_data.get("disk_percent", 0),
                "disk_total": agent_data.get("disk_total", 0),
                "disk_used": agent_data.get("disk_used", 0),
                "disk_avail": agent_data.get("disk_avail", 0),
                "disk_read": _disk_read_rate,
                "disk_write": _disk_write_rate,
                "load1": agent_data.get("load1", 0),
                "load5": agent_data.get("load5", 0),
                "load15": agent_data.get("load15", 0),
                "net_rx": _net_rx_rate,
                "net_tx": _net_tx_rate,
                "uptime": agent_data.get("uptime", 0),
                "container_running": agent_data.get("container_running", 0),
                "container_stopped": agent_data.get("container_stopped", 0),
            }
            return {
                "server_id": server_id,
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": normalized,
                "containers": containers,
                "source": "agent",
            }
    
    # Agent unreachable or not deployed: try SSH fallback for remote servers
    if srv.agent_type != 'local':
        password = None
        if srv.ssh_key and srv.ssh_key.startswith("__password__"):
            password = srv.ssh_key[len("__password__"):]
        client = get_ssh_client(srv, password=password)
        if not client:
            return {
                "server_id": server_id,
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": {"cpu": 0, "memory": 0, "disk": 0, "cpu_count": 0,
                           "memory_total": 0, "memory_used": 0, "memory_avail": 0,
                           "disk_total": 0, "disk_used": 0, "disk_avail": 0,
                           "disk_read": 0, "disk_write": 0,
                           "load1": 0, "load5": 0, "load15": 0,
                           "net_rx": 0, "net_tx": 0, "uptime": 0,
                           "container_running": 0, "container_stopped": 0},
                "containers": [],
                "error": "Agent不可达且SSH连接失败",
            }
        try:
            m = collect_remote_metrics(client)
            containers = get_remote_containers(client)
            client.close()
            normalized = {
                "cpu": m.get("cpu_percent", 0),
                "cpu_count": m.get("cpu_count", 0),
                "memory": m.get("memory_percent", 0),
                "memory_total": m.get("memory_total", 0),
                "memory_used": m.get("memory_used", 0),
                "memory_avail": m.get("memory_total", 0) - m.get("memory_used", 0),
                "disk": m.get("disk_percent", 0),
                "disk_total": m.get("disk_total", 0),
                "disk_used": m.get("disk_used", 0),
                "disk_avail": m.get("disk_avail", 0),
                "disk_read": 0,
                "disk_write": 0,
                "load1": m.get("load1", 0),
                "load5": m.get("load5", 0),
                "load15": m.get("load15", 0),
                "net_rx": m.get("net_rx_bytes", 0),
                "net_tx": m.get("net_tx_bytes", 0),
                "uptime": m.get("uptime", 0),
                "container_running": len([c for c in containers if c.get("status") == "running"]),
                "container_stopped": len([c for c in containers if c.get("status") != "running"]),
            }
            return {
                "server_id": server_id,
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": normalized,
                "containers": containers,
                "source": "ssh",
            }
        except Exception as e:
            try: client.close()
            except: pass
            return {
                "server_id": server_id,
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": {},
                "containers": [],
                "error": str(e),
            }
    
    # Local server without Agent: return empty with hint
    return {
        "server_id": server_id,
        "timestamp": datetime.utcnow().isoformat(),
        "metrics": {"cpu": 0, "memory": 0, "disk": 0, "cpu_count": 0,
                   "memory_total": 0, "memory_used": 0, "memory_avail": 0,
                   "disk_total": 0, "disk_used": 0, "disk_avail": 0,
                   "disk_read": 0, "disk_write": 0,
                   "load1": 0, "load5": 0, "load15": 0,
                   "net_rx": 0, "net_tx": 0, "uptime": 0,
                   "container_running": 0, "container_stopped": 0},
        "containers": [],
        "error": "本机Agent未部署，请部署Agent以启用监控",
    }


def _downsample_history(values, hours):
    """Smart downsampling: return aggregated data covering the full time range.
    
    For short ranges (<=1h): return raw data (no downsampling).
    For longer ranges: average values within time buckets to cover the full range
    while keeping the result count reasonable (~100-200 points).
    """
    if not values or hours <= 1:
        return values
    # Target: ~120 data points covering the full range
    # bucket_size = total_seconds / target_points
    total_span = values[-1][0] - values[0][0]
    if total_span <= 0:
        return values
    bucket_size = total_span / 120
    # Minimum bucket: 60 seconds (don't over-subdivide)
    bucket_size = max(bucket_size, 60)
    
    result = []
    bucket_start = values[0][0]
    bucket_vals = []
    
    for ts, val in values:
        if ts >= bucket_start + bucket_size:
            if bucket_vals:
                avg_ts = bucket_vals[len(bucket_vals)//2][0]  # midpoint timestamp
                avg_val = sum(v for _, v in bucket_vals) / len(bucket_vals)
                result.append([avg_ts, round(avg_val, 2)])
            bucket_start = ts
            bucket_vals = [(ts, val)]
        else:
            bucket_vals.append((ts, val))
    
    # Don't forget the last bucket
    if bucket_vals:
        avg_ts = bucket_vals[len(bucket_vals)//2][0]
        avg_val = sum(v for _, v in bucket_vals) / len(bucket_vals)
        result.append([avg_ts, round(avg_val, 2)])
    
    return result

# === Monitor History ===
@app.get("/api/v2/servers/{server_id}/history")
@app.get("/api/v2/monitor/{server_id}/history")
def get_monitor_history(server_id: str, metric: str = "cpu", hours: int = 24):
    """Get historical monitoring data from Prometheus (local server only)."""
    import requests as req

    # Remote servers have no Prometheus history data
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.agent_type != 'local':
            # Try Agent-collected history
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            records = db.query(MetricHistory).filter(
                MetricHistory.server_id == srv.id,
                MetricHistory.metric == metric,
                MetricHistory.timestamp >= cutoff,
            ).order_by(MetricHistory.timestamp).all()
            values = [[calendar.timegm(r.timestamp.timetuple()), r.value] for r in records]
            if values:
                return {"metric": metric, "values": _downsample_history(values, hours), "source": "agent"}
            return {"metric": metric, "values": [], "note": "Agent未部署或无历史数据"}

    # Local server: use Agent-collected history (same as remote)
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    records = db.query(MetricHistory).filter(
        MetricHistory.server_id == srv.id,
        MetricHistory.metric == metric,
        MetricHistory.timestamp >= cutoff,
    ).order_by(MetricHistory.timestamp).all()
    values = [[calendar.timegm(r.timestamp.timetuple()), r.value] for r in records]
    if values:
        return {"metric": metric, "values": _downsample_history(values, hours), "source": "agent"}
    return {"metric": metric, "values": [], "note": "本机Agent未部署或无历史数据"}


# === Categories ===
@app.get("/api/v2/categories")
def list_categories(server_id: Optional[str] = None):
    with get_db() as db:
        q = db.query(Service.category).distinct()
        if server_id:
            q = q.filter(Service.server_id == uuid.UUID(server_id))
        cats = [r[0] for r in q.all()]
        result = []
        for name in cats:
            meta = CATEGORY_META.get(name, CATEGORY_META["未分类"])
            q2 = db.query(Service).filter(Service.category == name)
            if server_id:
                q2 = q2.filter(Service.server_id == uuid.UUID(server_id))
            svc_count = q2.count()
            result.append({
                "name": name,
                "icon": meta["icon"],
                "color": meta["color"],
                "order": meta["order"],
                "count": svc_count,
            })
        result.sort(key=lambda x: x["order"])
        return result


# === Agent Management APIs ===

@app.post("/api/v2/servers/{server_id}/deploy-agent")
def deploy_agent_api(server_id: str):
    """Deploy or re-deploy OpsAgent on a remote server."""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.agent_type == "local":
            # 本机Agent升级：复制源码 + systemctl restart
            from app.agent_manager import upgrade_local_agent
            result = upgrade_local_agent()
            if result["success"]:
                srv.agent_version = result.get("version", srv.agent_version)
                srv.agent_status = "running"
                db.commit()
            return result
    
    # Mark as deploying
    with get_db() as db:
        s = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        s.agent_status = "deploying"
        db.commit()
    
    result = deploy_agent(srv)
    
    # Update status
    with get_db() as db:
        s = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if result.get("success"):
            s.agent_status = "running"
            s.agent_port = result.get("agent_port", 19100)
            s.agent_token = result.get("agent_token", "")
            s.agent_version = result.get("agent_version", "2.0.0")
        else:
            s.agent_status = "error"
        db.commit()
    
    # Auto-trigger first scan after successful deploy
    scan_info = ""
    if result.get("success"):
        try:
            scan_data = trigger_agent_scan(srv.host, result.get("agent_port", 19100), result.get("agent_token", ""))
            if scan_data:
                with get_db() as db2:
                    s2 = db2.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
                    if s2:
                        sr = _sync_agent_scan_to_db(s2, db2, scan_data)
                        s2.status = ServerStatus.online.value
                        s2.last_seen = datetime.utcnow()
                        s2.docker_available = True
                        db2.commit()
                        scan_info = f" 发现{sr['added']}个服务"
                        result["scan_added"] = sr["added"]
                        result["scan_updated"] = sr["updated"]
        except Exception as e:
            scan_info = f" 自动扫描失败: {e}"
        if scan_info:
            result["message"] = (result.get("message", "") + scan_info).strip()
    
    result.pop("agent_token", None)
    return result


@app.get("/api/v2/servers/{server_id}/agent-status")
def agent_status_api(server_id: str):
    """Check OpsAgent status on a remote server."""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
    
    if srv.agent_type == "local":
        import subprocess
        try:
            result = subprocess.run(['systemctl', 'is-active', 'opsagent'], capture_output=True, text=True, timeout=5)
            active = result.stdout.strip() == 'active'
            srv.agent_status = 'running' if active else 'stopped'
            with get_db() as _db:
                _s = _db.query(Server).filter(Server.id == srv.id).first()
                if _s:
                    _s.agent_status = srv.agent_status
                    _db.commit()
        except:
            pass
        return {"status": "running" if srv.agent_status == "running" else "not_deployed", "agent_port": srv.agent_port, "agent_version": srv.agent_version, "message": "Agent运行中" if srv.agent_status == "running" else "本机Agent未部署"}
    
    result = check_agent_status(srv)
    # Update DB
    with get_db() as db:
        s = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if result.get("status") == "running":
            s.agent_status = "running"
            if result.get("agent_port"):
                s.agent_port = result["agent_port"]
            if result.get("agent_token"):
                s.agent_token = result["agent_token"]
            if result.get("agent_version"):
                s.agent_version = result["agent_version"]
        elif result.get("status") in ("stopped", "installed_stopped"):
            s.agent_status = "stopped"
        elif result.get("status") == "not_deployed":
            s.agent_status = "not_deployed"
        db.commit()
    return {key: value for key, value in result.items() if key != "agent_token"}


@app.delete("/api/v2/servers/{server_id}/agent")
def uninstall_agent_api(server_id: str):
    """Uninstall OpsAgent from a remote server."""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.agent_type == "local":
            return {"success": False, "message": "本机内置Agent无需卸载，但可重启"}
    
    result = uninstall_agent(srv)
    if result.get("success"):
        with get_db() as db:
            s = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
            s.agent_status = "not_deployed"
            s.agent_port = 19100
            s.agent_token = None
            s.agent_version = None
            db.commit()
    return result


# === Agent Metrics Collection ===

def _collect_agent_metrics():
    """Collect Agent metrics without holding DB sessions during network I/O."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from types import SimpleNamespace

    try:
        with get_db() as db:
            targets = [
                SimpleNamespace(
                    id=srv.id, host=srv.host, agent_type=srv.agent_type,
                    agent_port=srv.agent_port or 19100, agent_token=srv.agent_token or "",
                )
                for srv in db.query(Server).filter(Server.agent_status == "running").all()
            ]

        # Agent HTTP calls run concurrently after the read session has closed.
        samples = {}
        workers = max(1, min(8, len(targets) or 1))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent-metrics") as executor:
            future_map = {
                executor.submit(
                    fetch_agent_metrics,
                    "127.0.0.1" if target.agent_type == "local" else target.host,
                    target.agent_port,
                    target.agent_token,
                ): target
                for target in targets
            }
            for future in as_completed(future_map):
                target = future_map[future]
                try:
                    samples[target.id] = future.result()
                except Exception as exc:
                    print(f"Agent metrics collection error for {target.host}: {exc}")
                    samples[target.id] = None

        # One short transaction per host avoids holding multiple server row locks.
        for target in sorted(targets, key=lambda item: str(item.id)):
            data = samples.get(target.id)
            with get_db() as db:
                srv = db.query(Server).filter(Server.id == target.id).first()
                if not srv:
                    continue
                if not data:
                    srv.agent_status = "stopped"
                    db.commit()
                    continue

                now = datetime.utcnow()
                raw_values = {
                    "net_rx_raw": data.get("net_rx_bytes", 0) or 0,
                    "net_tx_raw": data.get("net_tx_bytes", 0) or 0,
                    "disk_read_raw": data.get("disk_read_bytes", 0) or 0,
                    "disk_write_raw": data.get("disk_write_bytes", 0) or 0,
                }
                rates = {}
                for metric_name, raw_value in raw_values.items():
                    last_rec = db.query(MetricHistory).filter(
                        MetricHistory.server_id == target.id,
                        MetricHistory.metric == metric_name,
                    ).order_by(MetricHistory.timestamp.desc()).first()
                    elapsed = (now - last_rec.timestamp).total_seconds() if last_rec else 0
                    rates[metric_name.removesuffix("_raw")] = (
                        max(0, (raw_value - last_rec.value) / elapsed)
                        if last_rec and last_rec.value is not None and elapsed > 0 else 0
                    )

                metrics_to_store = {
                    "cpu": data.get("cpu_percent", 0),
                    "memory": data.get("memory_percent", 0),
                    "disk": data.get("disk_percent", 0),
                    "load1": data.get("load1", 0),
                    "load5": data.get("load5", 0),
                    "load15": data.get("load15", 0),
                    **rates,
                    **raw_values,
                }
                for metric_name, value in metrics_to_store.items():
                    db.add(MetricHistory(
                        server_id=target.id, timestamp=now, metric=metric_name,
                        value=float(value) if value else 0,
                    ))
                srv.status = ServerStatus.online.value
                srv.last_seen = now
                srv.agent_status = "running"
                db.commit()

        # Retention cleanup is isolated from metric ingestion transactions.
        with get_db() as db:
            cutoff = datetime.utcnow() - timedelta(days=RETENTION_METRIC_DAYS)
            db.query(MetricHistory).filter(MetricHistory.timestamp < cutoff).delete()
            raw_cutoff = datetime.utcnow() - timedelta(hours=1)
            db.query(MetricHistory).filter(
                MetricHistory.metric.in_(["net_rx_raw", "net_tx_raw", "disk_read_raw", "disk_write_raw"]),
                MetricHistory.timestamp < raw_cutoff,
            ).delete(synchronize_session=False)
            db.commit()
    except Exception as exc:
        print(f"Agent metrics collection error: {exc}")


async def background_agent_collector():
    """Periodically collect metrics from all running agents."""
    while True:
        await asyncio.to_thread(_collect_agent_metrics)
        await asyncio.sleep(30)


@app.get("/api/v2/servers/{server_id}/agent-metrics")
def get_agent_metrics_api(server_id: str):
    """Get real-time metrics from a running OpsAgent."""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
    
    if srv.agent_type == "local":
        # Local agent metrics are available like any other agent
        pass  # Fall through to the normal metrics collection below
    
    if srv.agent_status != "running":
        return {"error": "Agent未运行", "agent_status": srv.agent_status}
    
    data = fetch_agent_metrics("127.0.0.1" if srv.agent_type == "local" else srv.host, srv.agent_port or 19100, srv.agent_token or "")
    if not data:
        # Update status
        with get_db() as db:
            s = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
            s.agent_status = "stopped"
            db.commit()
        return {"error": "Agent连接失败", "agent_status": "stopped"}
    
    # Calculate rate metrics from cumulative Agent values
    _cur_net_rx2 = data.get("net_rx_bytes", 0) or 0
    _cur_net_tx2 = data.get("net_tx_bytes", 0) or 0
    _cur_disk_read2 = data.get("disk_read_bytes", 0) or 0
    _cur_disk_write2 = data.get("disk_write_bytes", 0) or 0
    _net_rx_rate2 = 0.0
    _net_tx_rate2 = 0.0
    _disk_read_rate2 = 0.0
    _disk_write_rate2 = 0.0
    from sqlalchemy import func as _sa_func2
    with get_db() as _db2:
        for _mn2, _cv2 in [("net_rx_raw", _cur_net_rx2), ("net_tx_raw", _cur_net_tx2), ("disk_read_raw", _cur_disk_read2), ("disk_write_raw", _cur_disk_write2)]:
            _last2 = _db2.query(MetricHistory).filter(
                MetricHistory.server_id == uuid.UUID(server_id),
                MetricHistory.metric == _mn2,
            ).order_by(MetricHistory.timestamp.desc()).first()
            if _last2 and _last2.value:
                _elapsed2 = (datetime.utcnow() - _last2.timestamp).total_seconds()
                if _elapsed2 > 0:
                    _rv2 = max(0, (_cv2 - _last2.value) / _elapsed2)
                else:
                    _rv2 = 0
            else:
                _rv2 = 0
            if _mn2 == "net_rx_raw": _net_rx_rate2 = _rv2
            elif _mn2 == "net_tx_raw": _net_tx_rate2 = _rv2
            elif _mn2 == "disk_read_raw": _disk_read_rate2 = _rv2
            elif _mn2 == "disk_write_raw": _disk_write_rate2 = _rv2

    # Normalize metrics to match Prometheus format
    normalized = {
        "cpu": data.get("cpu_percent", 0),
        "cpu_count": data.get("cpu_count", 0),
        "memory": data.get("memory_percent", 0),
        "memory_total": data.get("memory_total", 0),
        "memory_used": data.get("memory_used", 0),
        "memory_avail": data.get("memory_available", 0),
        "disk": data.get("disk_percent", 0),
        "disk_total": data.get("disk_total", 0),
        "disk_used": data.get("disk_used", 0),
        "disk_avail": data.get("disk_avail", 0),
        "disk_read": _disk_read_rate2,
        "disk_write": _disk_write_rate2,
        "load1": data.get("load1", 0),
        "load5": data.get("load5", 0),
        "load15": data.get("load15", 0),
        "net_rx": _net_rx_rate2,
        "net_tx": _net_tx_rate2,
        "uptime": data.get("uptime", 0),
        "container_running": data.get("container_running", 0),
        "container_stopped": data.get("container_stopped", 0),
    }
    return {
        "server_id": server_id,
        "timestamp": datetime.utcnow().isoformat(),
        "metrics": normalized,
        "containers": data.get("containers", []),
    }


@app.get("/api/v2/servers/{server_id}/agent-history")
def get_agent_history_api(server_id: str, metric: str = "cpu", hours: int = 24):
    """Get historical metrics for a remote server from Agent-collected data."""
    from sqlalchemy import func as sa_func
    
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        if srv.agent_type == "local":
            pass  # Local agent history is available like any other server
        
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        records = db.query(MetricHistory).filter(
            MetricHistory.server_id == srv.id,
            MetricHistory.metric == metric,
            MetricHistory.timestamp >= cutoff,
        ).order_by(MetricHistory.timestamp).all()
        
        values = []
        for r in records:
            # Metric timestamps are stored as naive UTC. datetime.timestamp()
            # would reinterpret them in the server's Asia/Shanghai timezone.
            values.append([calendar.timegm(r.timestamp.utctimetuple()), r.value])
        
        return {"metric": metric, "values": _downsample_history(values, hours)}

# === Health ===
@app.get("/api/v2/health")
def health_check():
    with get_db() as db:
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        except:
            db_ok = False
    
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "timestamp": datetime.utcnow().isoformat(),
    }


# === Stats ===
@app.get("/api/v2/stats")
def get_stats():
    with get_db() as db:
        server_count = db.query(Server).count()
        service_count = db.query(Service).count()
        up_count = db.query(Service).filter(Service.status == ServiceStatus.up.value).count()
        down_count = db.query(Service).filter(Service.status == ServiceStatus.down.value).count()
        return {
            "servers": server_count,
            "services": service_count,
            "up": up_count,
            "down": down_count,
        }
# === Patch: add health-check-url and group-config endpoints ===
# This code should be appended before the last line of main.py

# === URL-based Health Check (for manual services) ===
@app.get("/api/v2/health-check-url")
def health_check_url(url: str = Query(..., description="URL to check")):
    """Check if a URL is reachable from server side (handles HTTPS/self-signed certs)."""
    import requests as req
    import urllib.parse
    # Validate URL
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        # Try as relative path
        if url.startswith("/"):
            url = f"http://{LOCAL_HOST}{url}"
        else:
            return {"status": "error", "message": "Invalid URL"}
    try:
        resp = req.head(url, timeout=5, allow_redirects=True, verify=False)
        if resp.status_code < 500:
            return {"status": "online", "code": resp.status_code, "url": url}
        else:
            return {"status": "offline", "code": resp.status_code, "url": url}
    except req.exceptions.SSLError:
        # Self-signed cert: try HTTP fallback
        http_url = url.replace("https://", "http://", 1)
        try:
            resp2 = req.head(http_url, timeout=5, allow_redirects=True, verify=False)
            if resp2.status_code < 500:
                return {"status": "online", "code": resp2.status_code, "url": http_url, "note": "HTTPS cert error, HTTP fallback"}
            else:
                return {"status": "offline", "code": resp2.status_code, "url": http_url}
        except Exception:
            return {"status": "offline", "url": http_url, "note": "HTTPS cert error, HTTP also failed"}
    except req.exceptions.Timeout:
        return {"status": "offline", "url": url, "note": "timeout"}
    except Exception as e:
        return {"status": "offline", "url": url, "note": str(e)[:100]}


# === Group Config API (read/write groups.json) ===
GROUPS_JSON_PATH = "/opt/opscenter/frontend/groups.json"

def _read_groups_json():
    import json as _json
    try:
        with open(GROUPS_JSON_PATH, "r", encoding="utf-8") as f:
            data = _json.load(f)
        if "servers" not in data and "groups" in data:
            _migrate_groups_json()
            with open(GROUPS_JSON_PATH, "r", encoding="utf-8") as f:
                data = _json.load(f)
        if "defaultGroups" not in data or "servers" not in data:
            return {"defaultGroups": list(DEFAULT_GROUPS), "servers": {}}
        return data
    except Exception:
        return {"defaultGroups": list(DEFAULT_GROUPS), "servers": {}}

def _write_groups_json(data):
    import json as _json
    with open(GROUPS_JSON_PATH, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)
    return True

class GroupItem(BaseModel):
    id: str
    name: str
    order: int = 100
    color: str = "#64748b"
    icon: str = "box"

class GroupConfigUpdate(BaseModel):
    groups: Optional[List[GroupItem]] = None
    serviceGroupMap: Optional[dict] = None

@app.get("/api/v2/group-config")
def get_group_config(server_id: Optional[str] = Query(None)):
    """Read groups.json configuration. If server_id given, return that server's groups+serviceGroupMap (or defaultGroups if not configured)."""
    config = _read_groups_json()
    if not server_id:
        return config
    srv = config.get("servers", {}).get(server_id)
    if srv:
        return {"groups": srv.get("groups", []), "serviceGroupMap": srv.get("serviceGroupMap", {})}
    return {"groups": config.get("defaultGroups", []), "serviceGroupMap": {}}

@app.put("/api/v2/group-config")
def update_group_config(data: GroupConfigUpdate, server_id: Optional[str] = Query(None)):
    """Update groups.json configuration (full replace). If server_id given, update that server's section only."""
    current = _read_groups_json()
    if not server_id:
        if data.groups is not None:
            current["defaultGroups"] = [g.model_dump() for g in data.groups]
        if data.serviceGroupMap is not None:
            if "servers" not in current:
                current["servers"] = {}
        _write_groups_json(current)
        return {"ok": True, "config": current}
    if "servers" not in current:
        current["servers"] = {}
    if server_id not in current["servers"]:
        current["servers"][server_id] = {
            "groups": [g.model_dump() for g in data.groups] if data.groups else list(current.get("defaultGroups", [])),
            "serviceGroupMap": data.serviceGroupMap if data.serviceGroupMap else {}
        }
    else:
        if data.groups is not None:
            current["servers"][server_id]["groups"] = [g.model_dump() for g in data.groups]
        if data.serviceGroupMap is not None:
            current["servers"][server_id]["serviceGroupMap"] = data.serviceGroupMap
    _write_groups_json(current)
    return {"ok": True, "server_id": server_id, "groups": current["servers"][server_id]["groups"], "serviceGroupMap": current["servers"][server_id]["serviceGroupMap"]}

@app.patch("/api/v2/group-config/service-map")
def update_service_group_map(serviceKey: str = Query(...), groupId: str = Query(...)):
    """Move a service to a different group. serviceKey format: auto:{server_id}:{service_id}. Parses server_id from key."""
    parts = serviceKey.split(":")
    server_id = parts[1] if len(parts) >= 3 and parts[0] == "auto" else None
    current = _read_groups_json()
    if "servers" not in current:
        current["servers"] = {}
    if server_id:
        if server_id not in current["servers"]:
            current["servers"][server_id] = {
                "groups": list(current.get("defaultGroups", [])),
                "serviceGroupMap": {}
            }
        smap = current["servers"][server_id]["serviceGroupMap"]
    else:
        smap = current.setdefault("serviceGroupMap", {})
    if groupId == "ungrouped":
        smap.pop(serviceKey, None)
    else:
        smap[serviceKey] = groupId
    _write_groups_json(current)
    return {"ok": True, "serviceKey": serviceKey, "groupId": groupId, "server_id": server_id}

@app.post("/api/v2/group-config/groups")
def add_group(item: GroupItem, server_id: Optional[str] = Query(None)):
    """Add a new group to a specific server's groups (or defaultGroups if no server_id)."""
    current = _read_groups_json()
    if "servers" not in current:
        current["servers"] = {}
    if server_id:
        if server_id not in current["servers"]:
            current["servers"][server_id] = {
                "groups": list(current.get("defaultGroups", [])),
                "serviceGroupMap": {}
            }
        groups = current["servers"][server_id]["groups"]
    else:
        groups = current.setdefault("defaultGroups", [])
    if any(g["id"] == item.id for g in groups):
        raise HTTPException(400, f"Group id '{item.id}' already exists")
    groups.append(item.model_dump())
    groups.sort(key=lambda g: g.get("order", 100))
    _write_groups_json(current)
    return {"ok": True, "group": item.model_dump(), "server_id": server_id, "groups": groups}

@app.put("/api/v2/group-config/groups/{group_id}")
def update_group(group_id: str, item: GroupItem, server_id: Optional[str] = Query(None)):
    """Update a group's name, color, icon, or order for a specific server."""
    current = _read_groups_json()
    if "servers" not in current:
        current["servers"] = {}
    if server_id:
        if server_id not in current["servers"]:
            raise HTTPException(404, f"Server '{server_id}' not configured")
        groups = current["servers"][server_id]["groups"]
    else:
        groups = current.setdefault("defaultGroups", [])
    found = False
    for i, g in enumerate(groups):
        if g["id"] == group_id:
            groups[i] = item.model_dump()
            found = True
            break
    if not found:
        raise HTTPException(404, f"Group '{group_id}' not found")
    groups.sort(key=lambda g: g.get("order", 100))
    _write_groups_json(current)
    return {"ok": True, "group": item.model_dump(), "server_id": server_id}


@app.delete("/api/v2/group-config/groups/{group_id}")
def delete_group(group_id: str, server_id: Optional[str] = Query(None)):
    """Delete a group for a specific server. Also cleans serviceGroupMap mappings pointing to it."""
    if group_id == "ungrouped":
        raise HTTPException(400, "Cannot delete the 'ungrouped' group")
    current = _read_groups_json()
    if "servers" not in current:
        current["servers"] = {}
    if server_id:
        if server_id not in current["servers"]:
            raise HTTPException(404, f"Server '{server_id}' not configured")
        groups = current["servers"][server_id]["groups"]
        smap = current["servers"][server_id]["serviceGroupMap"]
    else:
        groups = current.setdefault("defaultGroups", [])
        smap = current.setdefault("serviceGroupMap", {})
    current_groups_len = len(groups)
    groups[:] = [g for g in groups if g["id"] != group_id]
    if len(groups) == current_groups_len:
        raise HTTPException(404, f"Group '{group_id}' not found")
    to_remove = [k for k, v in smap.items() if v == group_id]
    for k in to_remove:
        del smap[k]
    _write_groups_json(current)
    return {"ok": True, "server_id": server_id, "removed_mappings": to_remove}


@app.get("/api/v2/group-config/merged")
def get_merged_groups():
    """Aggregate groups from all servers, merge by name, count services per group, sorted by order."""
    config = _read_groups_json()
    merged = {}
    for sid, srv in config.get("servers", {}).items():
        for g in srv.get("groups", []):
            key = g.get("name", "")
            if key in merged:
                merged[key]["serviceCount"] += len([
                    k for k, v in srv.get("serviceGroupMap", {}).items() if v == g["id"]
                ])
            else:
                sc = len([k for k, v in srv.get("serviceGroupMap", {}).items() if v == g["id"]])
                merged[key] = {
                    "id": g.get("id", ""),
                    "name": g.get("name", ""),
                    "order": g.get("order", 100),
                    "color": g.get("color", "#64748b"),
                    "icon": g.get("icon", "box"),
                    "serviceCount": sc
                }
    result = sorted(merged.values(), key=lambda x: x.get("order", 100))
    return result


@app.post("/api/v2/group-config/apply-default")
def apply_default_groups(server_id: str = Query(..., description="Server ID to apply default groups to")):
    """Copy defaultGroups to a specific server's groups. Does not affect existing serviceGroupMap."""
    config = _read_groups_json()
    if "servers" not in config:
        config["servers"] = {}
    defaults = config.get("defaultGroups", [])
    if server_id not in config["servers"]:
        config["servers"][server_id] = {"groups": [], "serviceGroupMap": {}}
    config["servers"][server_id]["groups"] = [dict(g) for g in defaults]
    _write_groups_json(config)
    return {"ok": True, "server_id": server_id, "groups": config["servers"][server_id]["groups"]}


# === Services with server status (enhanced) ===
@app.get("/api/v2/services-with-status")
def list_services_with_status(server_id: Optional[str] = None):
    """List services with their server status included for frontend display."""
    with get_db() as db:
        q = db.query(Service)
        if server_id:
            q = q.filter(Service.server_id == uuid.UUID(server_id))
        # Only show services that have a web-accessible URL and are not hidden
        q = q.filter(Service.url != None, Service.url != '', Service.hidden != True)
        q = q.order_by(Service.sort_order, Service.category, Service.name)
        services = q.all()
        
        # Get server statuses
        server_ids = set(s.server_id for s in services)
        servers_map = {}
        for sid in server_ids:
            srv = db.query(Server).filter(Server.id == sid).first()
            if srv:
                servers_map[str(sid)] = {"name": srv.name, "host": srv.host, "status": srv.status, "is_local": srv.is_local, "agent_type": srv.agent_type}
        
        result = []
        for s in services:
            srv_info = servers_map.get(str(s.server_id), {})
            result.append({
                "id": str(s.id), "server_id": str(s.server_id),
                "name": s.name, "url": s.url, "category": s.category,
                "icon": s.icon, "description": s.description,
                "source": s.source, "status": s.status, "pinned": s.pinned,
                "health_path": s.health_path, "container_name": s.container_name,
                "image": s.image, "ports": s.ports,
                "server_name": srv_info.get("name", ""),
                "server_host": srv_info.get("host", ""),
                "server_status": srv_info.get("status", "unknown"),
                "server_is_local": srv_info.get("is_local", False),
                "url_overridden": False,  # deprecated, always False
                "port": s.port,
                "proto": s.proto or "tcp",
                "host_ip": s.host_ip,
                "host_domain": s.host_domain,
            })
        return result


@app.get("/api/v2/services/all")
def list_all_services(server_id: Optional[str] = None):
    """List all services including hidden ones. For admin/resource management only."""
    with get_db() as db:
        q = db.query(Service)
        if server_id:
            q = q.filter(Service.server_id == uuid.UUID(server_id))
        q = q.order_by(Service.category, Service.name)
        services = q.all()
        server_ids = set(s.server_id for s in services)
        servers_map = {}
        for sid in server_ids:
            srv = db.query(Server).filter(Server.id == sid).first()
            if srv:
                servers_map[str(sid)] = {"name": srv.name, "host": srv.host, "status": srv.status, "is_local": srv.is_local, "agent_type": srv.agent_type}
        result = []
        for s in services:
            si = servers_map.get(str(s.server_id), {})
            result.append({
                "id": str(s.id), "server_id": str(s.server_id),
                "name": s.name, "url": s.url, "category": s.category,
                "icon": s.icon, "description": s.description,
                "source": s.source, "status": s.status, "pinned": s.pinned,
                "health_path": s.health_path, "container_name": s.container_name,
                "image": s.image, "ports": s.ports,
                "hidden": s.hidden or False,
                "server_name": si.get("name", ""),
                "server_host": si.get("host", ""),
                "server_status": si.get("status", "unknown"),
                "server_is_local": si.get("is_local", False),
                "url_overridden": False,  # deprecated, always False
                "port": s.port,
                "proto": s.proto or "tcp",
                "host_ip": s.host_ip,
                "host_domain": s.host_domain,
            })
        return result


# === SSH Terminal Endpoints ===

@app.post("/api/v2/terminal/sessions")
async def api_create_terminal_session(req: TerminalCreateRequest):
    """Create a new SSH terminal session"""
    with get_db() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(req.server_id)).first()
        if not srv:
            raise HTTPException(404, "Server not found")
        # Extract fields while session is active to avoid DetachedInstanceError
        srv_id = str(srv.id)
        srv_name = srv.name
        # Local server: use 127.0.0.1 instead of public IP (cannot loopback via public IP on cloud)
        srv_host = "127.0.0.1" if srv.agent_type == "local" else srv.host
        srv_port = srv.ssh_port or 22
        srv_user = srv.ssh_user or "root"
        # ssh_key field stores either a real key or __password__<password>
        srv_password = None
        srv_key = None
        if srv.ssh_key:
            if srv.ssh_key.startswith("__password__"):
                srv_password = srv.ssh_key[len("__password__"):]
            else:
                srv_key = srv.ssh_key
    sid, err = create_session(
        server_id=srv_id, server_name=srv_name,
        host=srv_host, port=srv_port,
        user=srv_user,
        password=srv_password,
        key_content=srv_key,
    )
    if not srv_password and not srv_key:
        raise HTTPException(400, f"服务器 {srv_name} 未配置SSH密码或密钥，请先在资源管理中添加")
    if err:
        raise HTTPException(400, err)
    session = get_session(sid)
    if not session:
        raise HTTPException(500, "Failed to create session")
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, lambda: session.connect(cols=req.cols, rows=req.rows))
    if not ok:
        remove_session(sid)
        raise HTTPException(500, f"SSH connection to {srv_host} failed")
    return {"session_id": sid, "server_name": srv_name, "server_host": srv_host, "user": srv_user}


@app.websocket("/ws/terminal/{session_id}")
async def ws_terminal(websocket: WebSocket, session_id: str):
    """WebSocket proxy for SSH terminal, supports reconnect within grace period"""
    session = get_session(session_id)
    if not session:
        await websocket.close(code=4004, reason="Invalid or expired session")
        return
    # Support reconnect: if session is in pending_reconnect state, resume it
    if session.pending_reconnect:
        if not session.connected or not session.channel:
            await websocket.close(code=4004, reason="SSH connection lost during grace")
            remove_session(session_id)
            return
        session.cancel_pending_reconnect()
    elif not session.connected:
        await websocket.close(code=4004, reason="Invalid or expired session")
        return
    await websocket.accept()
    loop = asyncio.get_event_loop()

    async def recv_from_ssh():
        """Read from SSH and send to WebSocket"""
        while session.is_alive:
            try:
                data = await loop.run_in_executor(None, session.recv, 4096)
                if data:
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
                else:
                    await asyncio.sleep(0.05)
            except Exception:
                break
        try:
            await websocket.close()
        except Exception:
            pass

    async def send_to_ssh():
        """Read from WebSocket and send to SSH"""
        try:
            while session.is_alive and not session.pending_reconnect:
                msg = await websocket.receive_text()
                import json
                try:
                    obj = json.loads(msg)
                    if obj.get("type") == "resize":
                        session.resize(obj.get("cols", 80), obj.get("rows", 24))
                    elif obj.get("type") == "input":
                        session.send(obj.get("data", ""))
                except json.JSONDecodeError:
                    # Plain text - send directly
                    session.send(msg)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    # Run both directions concurrently
    recv_task = asyncio.create_task(recv_from_ssh())
    send_task = asyncio.create_task(send_to_ssh())
    done, pending = await asyncio.wait(
        [recv_task, send_task], return_when=asyncio.FIRST_COMPLETED
    )
    for t in pending:
        t.cancel()
    # On WebSocket disconnect: mark pending reconnect instead of destroy
    if session.is_alive and session.connected:
        session.mark_pending_reconnect()
    else:
        remove_session(session_id)


@app.get("/api/v2/terminal/sessions/{session_id}/files")
async def api_sftp_list(session_id: str, path: str = "."):
    """List directory contents via SFTP"""
    session = get_session(session_id)
    if not session or not session.is_alive:
        raise HTTPException(404, "Session not found or expired")
    entries, err = session.sftp_list(path)
    if err:
        raise HTTPException(400, err)
    return {"path": path, "entries": entries}


@app.get("/api/v2/terminal/sessions/{session_id}/files/download")
async def api_sftp_download(session_id: str, path: str):
    """Download a file via SFTP"""
    session = get_session(session_id)
    if not session or not session.is_alive:
        raise HTTPException(404, "Session not found or expired")
    data, err = session.sftp_download(path)
    if err:
        raise HTTPException(400, err)
    import os
    filename = os.path.basename(path)
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/v2/terminal/sessions/{session_id}/files/upload")
async def api_sftp_upload(session_id: str, path: str = "", file: UploadFile = FastAPIFile(...)):
    """Upload a file via SFTP"""
    session = get_session(session_id)
    if not session or not session.is_alive:
        raise HTTPException(404, "Session not found or expired")
    content = await file.read()
    remote_path = path.rstrip("/") + "/" + file.filename if path else file.filename
    ok, err = session.sftp_upload(remote_path, content)
    if not ok:
        raise HTTPException(400, err)
    return {"ok": True, "path": remote_path, "size": len(content)}


class SftpMkdirRequest(BaseModel):
    path: str


class SftpRenameRequest(BaseModel):
    old_path: str
    new_path: str


class SftpDeleteRequest(BaseModel):
    path: str


@app.post("/api/v2/terminal/sessions/{session_id}/files/mkdir")
async def api_sftp_mkdir(session_id: str, req: SftpMkdirRequest):
    """Create directory via SFTP"""
    session = get_session(session_id)
    if not session or not session.is_alive:
        raise HTTPException(404, "Session not found or expired")
    ok, err = session.sftp_mkdir(req.path)
    if not ok:
        raise HTTPException(400, err)
    return {"ok": True}


@app.post("/api/v2/terminal/sessions/{session_id}/files/rename")
async def api_sftp_rename(session_id: str, req: SftpRenameRequest):
    """Rename file or directory via SFTP"""
    session = get_session(session_id)
    if not session or not session.is_alive:
        raise HTTPException(404, "Session not found or expired")
    ok, err = session.sftp_rename(req.old_path, req.new_path)
    if not ok:
        raise HTTPException(400, err)
    return {"ok": True}


@app.post("/api/v2/terminal/sessions/{session_id}/files/delete")
async def api_sftp_delete(session_id: str, req: SftpDeleteRequest):
    """Delete file or directory via SFTP"""
    session = get_session(session_id)
    if not session or not session.is_alive:
        raise HTTPException(404, "Session not found or expired")
    ok, err = session.sftp_remove(req.path)
    if not ok:
        raise HTTPException(400, err)
    return {"ok": True}


@app.get("/api/v2/terminal/sessions/{session_id}/status")
async def api_terminal_session_status(session_id: str):
    """Check if a terminal session can be reconnected"""
    session = get_session(session_id)
    if not session:
        return {"alive": False, "reconnectable": False}
    if session.pending_reconnect:
        return {"alive": True, "reconnectable": True, "server_name": session.server_name,
                "server_host": session.host, "user": session.user, "server_id": session.server_id}
    if session.is_alive:
        return {"alive": True, "reconnectable": True, "server_name": session.server_name,
                "server_host": session.host, "user": session.user, "server_id": session.server_id}
    return {"alive": False, "reconnectable": False}


@app.get("/api/v2/terminal/stats")
async def api_terminal_stats():
    """Get active terminal session stats"""
    return {"active_sessions": get_active_count()}
