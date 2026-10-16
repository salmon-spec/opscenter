# -*- coding: utf-8 -*-
"""服务详情聚合 / 监控大屏聚合 / 服务健康 / 历史指标查询。

- /services/{id}/detail     服务详情（部署位置/版本/运行时长/依赖）
- /screen/summary           监控大屏一屏聚合数据
- /services/health          全量服务健康（优先复用 service_health 模块快照）
- /monitor/history          历史指标查询（时间范围）
"""
from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, text

from app.agent_manager import (
    AGENT_DEFAULT_PORT,
    fetch_agent_wireguard,
    resolve_agent_host,
)
from app.auth import get_current_user
from app.config import CONTAINERIZED, LOCAL_AGENT_HOST
from app.database import get_db
from app.models import AlertEvent, AlertRule, DatabaseInstance, MetricHistory, PlazaHealthState, Server, Service, ServiceRelation

router = APIRouter(prefix="/api/v2", tags=["system-health"])

# WireGuard 汇总：按主机缓存 30 秒，避免大屏每次请求都并发访问 Agent
_WG_CACHE_TTL = 30
_WG_CACHE: dict = {}
_WG_CACHE_LOCK = threading.Lock()
_WG_MAX_WORKERS = 6
_WG_AGENT_TIMEOUT = 5
_WG_SUMMARY_TTL = 30
_METRIC_STALE_SECONDS = 90
_WG_SUMMARY_SNAPSHOT: Optional[dict] = None
_WG_SUMMARY_SNAPSHOT_AT = 0.0
_WG_SUMMARY_REFRESHING = False
_WG_SUMMARY_LOCK = threading.Lock()

# 健康规则：最近握手 <=180s 健康；181-600s 警告；>600s 或从未握手 离线
_WG_HEALTH_NOW_TOL = 180
_WG_HEALTH_WARN_TOL = 600

# 健康大屏：保留最近一次 Agent 采集摘要（容器计数等），避免为聚合触发容器列表/SSH 探测
_AGENT_SNAPSHOT_LOCK = threading.Lock()
_LAST_AGENT_SNAPSHOT: dict = {}  # server_id -> {container_running, container_stopped, ts}


def _metric_is_stale(online: bool, metric_ts: Optional[datetime], now: datetime) -> bool:
    return bool(online and (metric_ts is None or (now - metric_ts).total_seconds() > _METRIC_STALE_SECONDS))


def record_agent_snapshot(server_id, data: dict) -> None:
    """采集循环每轮写入最近一次 Agent 摘要（仅容器计数等轻量字段）。"""
    if data is None:
        return
    try:
        with _AGENT_SNAPSHOT_LOCK:
            _LAST_AGENT_SNAPSHOT[str(server_id)] = {
                "container_running": int(data.get("container_running") or 0),
                "container_stopped": int(data.get("container_stopped") or 0),
                "ts": time.time(),
            }
    except Exception:
        pass

def _service_relations(db, service_id) -> dict:
    """查询服务的出向/入向依赖关系。"""
    outgoing = (
        db.query(ServiceRelation, Service)
        .join(Service, ServiceRelation.target_service_id == Service.id)
        .filter(ServiceRelation.source_service_id == service_id)
        .all()
    )
    incoming = (
        db.query(ServiceRelation, Service)
        .join(Service, ServiceRelation.source_service_id == Service.id)
        .filter(ServiceRelation.target_service_id == service_id)
        .all()
    )
    return {
        "outgoing": [
            {
                "target_id": str(s.id),
                "target_name": s.name,
                "relation_type": r.relation_type,
                "label": r.label,
                "scenario": r.scenario,
            }
            for r, s in outgoing
        ],
        "incoming": [
            {
                "source_id": str(s.id),
                "source_name": s.name,
                "relation_type": r.relation_type,
                "label": r.label,
                "scenario": r.scenario,
            }
            for r, s in incoming
        ],
    }


@router.get("/services/{service_id}/detail")
def get_service_detail(
    service_id: str,
    _: object = Depends(get_current_user),
):
    """服务详情聚合：部署位置/版本/运行时长/端口/依赖关系。"""
    try:
        uid = uuid.UUID(service_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="服务不存在")
    with get_db() as db:
        svc = db.query(Service).filter(Service.id == uid).first()
        if not svc:
            raise HTTPException(status_code=404, detail="服务不存在")
        server = db.query(Server).filter(Server.id == svc.server_id).first()
        running_seconds = None
        if svc.started_at:
            running_seconds = max(0, int((datetime.utcnow() - svc.started_at).total_seconds()))
        return {
            "id": str(svc.id),
            "server_id": str(svc.server_id),
            "name": svc.name,
            "url": svc.url,
            "category": svc.category,
            "icon": svc.icon,
            "description": svc.description,
            "status": svc.status,
            "source": svc.source,
            "deploy_type": svc.deploy_type,
            "version": svc.version,
            "started_at": svc.started_at.isoformat() if svc.started_at else None,
            "running_seconds": running_seconds,
            "health_path": svc.health_path,
            "container_name": svc.container_name,
            "image": svc.image,
            "ports": svc.ports,
            "port": svc.port,
            "host_ip": svc.host_ip,
            "host_domain": svc.host_domain,
            "server": {
                "name": server.name if server else None,
                "host": server.host if server else None,
                "ssh_port": server.ssh_port if server else None,
                "agent_type": server.agent_type if server else None,
                "status": server.status if server else None,
            } if server else None,
            "relations": _service_relations(db, uid),
        }


def _wg_fetch_cached(server_id: str, host: str, port: int, token: str) -> Optional[dict]:
    """按主机读取 30 秒缓存的 Agent WireGuard 状态；未命中时并发采集并回填。"""
    now = time.time()
    with _WG_CACHE_LOCK:
        hit = _WG_CACHE.get(server_id)
        if hit and now - hit[0] < _WG_CACHE_TTL:
            return hit[1]
    data = fetch_agent_wireguard(host, port, token)
    with _WG_CACHE_LOCK:
        _WG_CACHE[server_id] = (time.time(), data)
    # 只保留最近 200 条缓存，避免长期运行内存膨胀
    if len(_WG_CACHE) > 200:
        for key in sorted(_WG_CACHE, key=lambda k: _WG_CACHE[k][0])[:len(_WG_CACHE) - 200]:
            _WG_CACHE.pop(key, None)
    return data


def _wg_health(age_seconds):
    """健康规则：<=180s 健康；181-600s 警告；>600s 或从未握手 离线。"""
    if age_seconds is None:
        return "offline"
    if age_seconds <= _WG_HEALTH_NOW_TOL:
        return "healthy"
    if age_seconds <= _WG_HEALTH_WARN_TOL:
        return "warning"
    return "offline"


def _wg_host_ip(ips) -> Optional[str]:
    """从 allowed_ips 提取精确 /32 IPv4（Hub 的 Allowed IP 是权威 IP 分布）。"""
    for ip in ips or []:
        if "/" in ip:
            addr, prefix = ip.rsplit("/", 1)
            if prefix == "32":
                return addr
        elif "." in ip and ":" not in ip:
            return ip
    return None


def _build_wireguard_summary() -> dict:
    """构建健康大屏需要的 WireGuard 主机与 Peer 状态摘要。

    - 从资产表读取主机快照后关闭 DB 会话，再用 ThreadPoolExecutor 并发请求各主机
      Agent（最多 6 个 worker），单主机超时 5 秒（fetch_agent_wireguard 内部超时）。
    - 按主机分别缓存 30 秒；某台超时只标记该主机数据未知，不阻塞整体摘要。
    - 以 Hub 的 AllowedIPs 为权威 IP 分布：精确 /32 IP 与 Server.host 匹配。
    """
    started = time.time()
    with get_db() as db:
        servers = db.query(Server).all()
        hosts = [
            {
                "id": str(s.id),
                "name": s.name,
                "host": s.host,
                "lan_ip": s.lan_ip or "",
                "agent_port": s.agent_port or AGENT_DEFAULT_PORT,
                "agent_token": s.agent_token or "",
                "is_local": s.agent_type == "local",
                "status": s.status,
            }
            for s in servers
        ]

    partial_errors = []
    unsupported_hosts = set()
    managed_found: dict = {}   # host_ip -> server dict
    wg_interfaces: dict = {}   # server_id -> agent wireguard payload

    # 1) 并发拉取各主机 Agent WireGuard 状态
    def _pull(h):
        try:
            return h, _wg_fetch_cached(h["id"], resolve_agent_host_for(h), h["agent_port"], h["agent_token"])
        except Exception as e:
            return h, {"error": f"{type(e).__name__}: {e}"}

    def resolve_agent_host_for(h):
        # WireGuard 状态也属于主机监控：远程主机固定 LAN 优先，无 LAN 再使用主地址。
        return LOCAL_AGENT_HOST if (h["is_local"] and CONTAINERIZED) else (h["lan_ip"] or h["host"])

    with ThreadPoolExecutor(max_workers=_WG_MAX_WORKERS) as pool:
        futures = {pool.submit(_pull, h): h for h in hosts}
        for fut in as_completed(futures, timeout=_WG_AGENT_TIMEOUT * 2):
            h = futures[fut]
            try:
                h, data = fut.result()
            except Exception as e:
                partial_errors.append(f"{h.get('name', h.get('id'))}: 采集超时({type(e).__name__})")
                continue
            if not data:
                partial_errors.append(f"{h['name']}: Agent 不支持或不可达（需升级到 v2.6+）")
                continue
            if data.get("supported") is False:
                partial_errors.append(f"{h['name']}: {data.get('reason') or '未检测到 WireGuard'}")
                unsupported_hosts.add(h["id"])
                continue
            if data.get("error"):
                partial_errors.append(f"{h['name']}: {data['error']}")
                continue
            wg_interfaces[h["id"]] = data

    # 2) 识别 Hub：拥有最多 Peer 的主机（通常是中心节点 L1）
    hub_candidates = []
    for h in hosts:
        data = wg_interfaces.get(h["id"])
        if not data:
            continue
        peers = sum(len(i.get("peers") or []) for i in data.get("interfaces") or [])
        if peers:
            hub_candidates.append((peers, h, data))
    hub = None
    if hub_candidates:
        hub_candidates.sort(key=lambda x: x[0], reverse=True)
        hub = hub_candidates[0][1]
        hub_data = hub_candidates[0][2]

    hub_peers = []
    if hub:
        for iface in hub_data.get("interfaces") or []:
            hub_peers.extend(iface.get("peers") or [])
    # Hub 的 Peer 计数是权威视角；不要再叠加客户端的反向计数。
    total_rx = sum(peer.get("rx_bytes") or 0 for peer in hub_peers)
    total_tx = sum(peer.get("tx_bytes") or 0 for peer in hub_peers)

    host_by_ip = {h["host"]: h for h in hosts}
    seen_managed: set[str] = set()
    seen_unmanaged: set[str] = set()
    health_counts = {"healthy": int(hub is not None), "warning": 0, "offline": 0}
    known_managed_health = int(hub is not None)
    for peer in hub_peers:
        ip = _wg_host_ip(peer.get("allowed_ips"))
        health = _wg_health(peer.get("latest_handshake_age_seconds"))
        managed = host_by_ip.get(ip) if ip else None
        if managed:
            peer_key = managed["id"]
            if hub and peer_key == hub["id"]:
                continue
            if peer_key not in seen_managed:
                seen_managed.add(peer_key)
                health_counts[health] = health_counts.get(health, 0) + 1
                known_managed_health += 1
        else:
            peer_key = peer.get("public_key_fingerprint") or f"peer-{len(seen_unmanaged)}"
            if peer_key not in seen_unmanaged:
                seen_unmanaged.add(peer_key)
                health_counts[health] = health_counts.get(health, 0) + 1

    # 4) Hub 未列出的受支持主机仍纳入管理数；它们没有可用的 Peer 健康状态。
    for h in hosts:
        if h["id"] not in unsupported_hosts and (not hub or h["id"] != hub["id"]):
            seen_managed.add(h["id"])

    summary = {
        "peer_total": len(hub_peers) if hub else 0,
        "managed": len(seen_managed) + int(hub is not None),
        "healthy": health_counts["healthy"],
        "warning": health_counts["warning"],
        "offline": health_counts["offline"],
        "unknown": max(0, len(seen_managed) + int(hub is not None) - known_managed_health),
        "unmanaged": len(seen_unmanaged),
        "wg_rx_bytes": total_rx,
        "wg_tx_bytes": total_tx,
    }
    return {
        "summary": summary,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "partial_errors": partial_errors,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _refresh_wireguard_snapshot() -> dict:
    global _WG_SUMMARY_SNAPSHOT, _WG_SUMMARY_SNAPSHOT_AT, _WG_SUMMARY_REFRESHING
    try:
        result = _build_wireguard_summary()
        with _WG_SUMMARY_LOCK:
            _WG_SUMMARY_SNAPSHOT = result
            _WG_SUMMARY_SNAPSHOT_AT = time.time()
        return result
    finally:
        with _WG_SUMMARY_LOCK:
            _WG_SUMMARY_REFRESHING = False


def _cached_wireguard_snapshot() -> Optional[dict]:
    """Return immediately and refresh the stale summary outside the screen request."""
    global _WG_SUMMARY_REFRESHING
    with _WG_SUMMARY_LOCK:
        snapshot = _WG_SUMMARY_SNAPSHOT
        stale = time.time() - _WG_SUMMARY_SNAPSHOT_AT >= _WG_SUMMARY_TTL
        if stale and not _WG_SUMMARY_REFRESHING:
            _WG_SUMMARY_REFRESHING = True
            threading.Thread(target=_refresh_wireguard_snapshot, daemon=True, name="wireguard-refresh").start()
        return snapshot


# ============ v4.8.5 大屏增强：K3s 优先聚合 + 双层服务健康 + ETag/响应缓存（需求基线 §7.2/§8/§10） ============

# 响应级缓存：5 秒 TTL 防击穿（并发请求共享同一次构建结果）；现有子缓存（Agent 快照 120s、WG 30s）不动
_SCREEN_CACHE_TTL = 5.0
_SCREEN_MAX_WORKERS = 6
_SCREEN_CACHE_LOCK = threading.Lock()
_SCREEN_CACHE: dict = {"payload": None, "etag": "", "stored_at": 0.0, "data_timestamp": None}
# ETag 只对业务数据做哈希：剔除时间戳类易变字段，数据未变时 ETag 跨请求稳定（否则 304 永远不命中）
_SCREEN_VOLATILE_KEYS = ("generated_at", "data_timestamp", "cached", "cache_age_seconds", "elapsed_ms")
_TCP_PROBE_TIMEOUT = 2.0
_WARNING_EVENT_LIMIT = 20
_RESTART_TOP_LIMIT = 5


def reset_screen_cache() -> None:
    """清空大屏响应缓存（测试与运维用）。"""
    with _SCREEN_CACHE_LOCK:
        _SCREEN_CACHE.update(payload=None, etag="", stored_at=0.0, data_timestamp=None)


def _screen_etag(payload: dict) -> str:
    """业务数据哈希（剔除易变字段、键排序），作为大屏响应 ETag。"""
    stable = {k: v for k, v in payload.items() if k not in _SCREEN_VOLATILE_KEYS}
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _match_if_none_match(header_value: Optional[str], etag: str) -> bool:
    """If-None-Match 匹配（支持 *、W/ 弱校验前缀与逗号分隔多值）。"""
    if not header_value:
        return False
    if header_value.strip() == "*":
        return True
    for candidate in header_value.split(","):
        token = candidate.strip()
        if token.startswith("W/"):
            token = token[2:]
        if token.strip('"') == etag:
            return True
    return False


def _k8s_client_or_none() -> tuple[object, Optional[str]]:
    """BE-1 契约接缝：延迟导入 app.k8s_client.get_client()。

    返回 (client|None, 脱敏错误|None)。模块缺失/构造失败一律降级为 (None, 错误)，
    绝不让 ImportError 阻断大屏（k8s_client 由并行 agent 开发，可能尚未落地）。
    """
    try:
        from app.k8s_client import get_client
    except Exception as e:
        return None, f"{type(e).__name__}"
    try:
        return get_client(), None
    except Exception as e:
        return None, f"{type(e).__name__}"


def _k8s_status_mode(client) -> str:
    """读取 client.status()['mode']，失败返回空串（调用方按不可用处理）。"""
    try:
        return str((client.status() or {}).get("mode") or "")
    except Exception:
        return ""


def _k3s_nodes(client) -> dict:
    """§8.1 第 1 层：节点 Ready 数与角色集合。无角色标签的节点按 K8s 惯例视为 worker。"""
    items = (client.list_nodes() or {}).get("items") or []
    roles: set = set()
    ready = 0
    for node in items:
        conditions = (node.get("status") or {}).get("conditions") or []
        if any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions):
            ready += 1
        node_roles = [
            k.split("/", 1)[1]
            for k in ((node.get("metadata") or {}).get("labels") or {})
            if k.startswith("node-role.kubernetes.io/")
        ]
        if node_roles:
            roles.update(node_roles)
        else:
            roles.add("worker")
    return {"total": len(items), "ready": ready, "roles": sorted(roles)}


def _k3s_workloads(client) -> dict:
    """§8.1 第 3 层：Deployment/StatefulSet/DaemonSet 期望副本与就绪副本（全 namespace 求和）。"""
    out = {}
    for kind in ("deployments", "statefulsets", "daemonsets"):
        items = (client.list_workloads(kind, None) or {}).get("items") or []
        desired = ready = 0
        for wl in items:
            spec = wl.get("spec") or {}
            status = wl.get("status") or {}
            if kind == "daemonsets":
                desired += int(status.get("desiredNumberScheduled") or 0)
                ready += int(status.get("numberReady") or 0)
            else:
                spec_replicas = spec.get("replicas")
                desired += int(spec_replicas if spec_replicas is not None else (status.get("replicas") or 0))
                ready += int(status.get("readyReplicas") or 0)
        out[kind] = {"desired": desired, "ready": ready}
    return out


def _k3s_pods(client) -> dict:
    """§8.1 第 3 层：Pod 相位计数与重启 Top5（Succeeded 属正常终态，不单列）。"""
    items = (client.list_pods(None, None, None) or {}).get("items") or []
    counts = {"total": len(items), "running": 0, "pending": 0, "failed": 0, "unknown": 0, "restart_top": []}
    restarts = []
    for pod in items:
        phase = (pod.get("status") or {}).get("phase") or "Unknown"
        if phase == "Running":
            counts["running"] += 1
        elif phase == "Pending":
            counts["pending"] += 1
        elif phase == "Failed":
            counts["failed"] += 1
        elif phase == "Unknown":
            counts["unknown"] += 1
        total_restarts = sum(
            int(cs.get("restartCount") or 0)
            for cs in (pod.get("status") or {}).get("containerStatuses") or []
        )
        if total_restarts:
            meta = pod.get("metadata") or {}
            restarts.append((total_restarts, meta.get("name") or "", meta.get("namespace") or ""))
    restarts.sort(key=lambda x: -x[0])
    counts["restart_top"] = [
        {"name": name, "namespace": ns, "restarts": n}
        for n, name, ns in restarts[:_RESTART_TOP_LIMIT]
    ]
    return counts


def _k3s_storage(client) -> dict:
    """§8.1 第 3 层：PVC 绑定状态计数。"""
    items = (client.list_pvcs(None) or {}).get("items") or []
    pvc = {"bound": 0, "pending": 0, "lost": 0}
    for item in items:
        phase = ((item.get("status") or {}).get("phase") or "").lower()
        if phase in pvc:
            pvc[phase] += 1
    return {"pvc": pvc}


def _k3s_jobs_section(client) -> tuple[dict, list]:
    """§8.1 第 3/5 层：Job 成败计数 + CronJob 列表（备份任务最后成功时间由前端从 cronjobs 渲染）。"""
    job_items = (client.list_jobs(None) or {}).get("items") or []
    jobs = {
        "recent_success": sum(1 for j in job_items if ((j.get("status") or {}).get("succeeded") or 0) > 0),
        "recent_failed": sum(1 for j in job_items if ((j.get("status") or {}).get("failed") or 0) > 0),
    }
    cron_items = (client.list_cronjobs(None) or {}).get("items") or []
    cronjobs = []
    for cj in cron_items:
        meta = cj.get("metadata") or {}
        spec = cj.get("spec") or {}
        cronjobs.append({
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "schedule": spec.get("schedule"),
            "suspend": bool(spec.get("suspend")),
            "last_schedule_time": (cj.get("status") or {}).get("lastScheduleTime"),
        })
    return jobs, cronjobs


def _k3s_warning_events(client) -> list:
    """§8.1 第 5 层：Warning 事件（按 lastTimestamp 倒序，限流 20 条，message 截断防内存放大）。"""
    items = (client.list_events(None, None) or {}).get("items") or []
    warnings = []
    for ev in items:
        if (ev.get("type") or "") != "Warning":
            continue
        meta = ev.get("metadata") or {}
        involved = ev.get("involvedObject") or {}
        last = ev.get("lastTimestamp") or ev.get("eventTime") or meta.get("creationTimestamp")
        warnings.append({
            "namespace": involved.get("namespace") or meta.get("namespace"),
            "object": f"{involved.get('kind') or ''}/{involved.get('name') or ''}".strip("/"),
            "reason": ev.get("reason"),
            "message": (ev.get("message") or "")[:300],
            "count": int(ev.get("count") or 0),
            "last_timestamp": last,
        })
    warnings.sort(key=lambda w: str(w.get("last_timestamp") or ""), reverse=True)
    return warnings[:_WARNING_EVENT_LIMIT]


def _k3s_netpol(client) -> dict:
    """§8.1 第 3 层：NetworkPolicy 覆盖率 = 有 netpol 的 namespace 数 / namespace 总数。"""
    ns_items = (client.list_namespaces() or {}).get("items") or []
    pol_items = (client.list_network_policies(None) or {}).get("items") or []
    covered = {(p.get("metadata") or {}).get("namespace") for p in pol_items}
    covered.discard(None)
    return {"covered": len(covered), "total": len(ns_items)}


def _build_k3s_section(client, partial_errors: list) -> dict:
    """逐子源采集 K3s 分节；单个子源失败只置 None 并记录脱敏错误（§8.2 局部失败）。"""
    def _safe(label, fn):
        try:
            return fn()
        except Exception as e:
            partial_errors.append(f"K3s {label}: {type(e).__name__}")
            return None

    nodes = _safe("nodes", lambda: _k3s_nodes(client))
    workloads = _safe("workloads", lambda: _k3s_workloads(client))
    pods = _safe("pods", lambda: _k3s_pods(client))
    storage = _safe("storage", lambda: _k3s_storage(client))
    jobs_res = _safe("jobs", lambda: _k3s_jobs_section(client))
    jobs, cronjobs = jobs_res if jobs_res else (None, None)
    warning_events = _safe("events", lambda: _k3s_warning_events(client))
    netpol = _safe("netpol", lambda: _k3s_netpol(client))
    return {
        "nodes": nodes,
        "workloads": workloads,
        "pods": pods,
        "storage": storage,
        "jobs": jobs,
        "cronjobs": cronjobs,
        "warning_events": warning_events,
        "netpol": netpol,
    }


def _k3s_section(partial_errors: list) -> tuple[Optional[dict], str]:
    """K3s 大屏分节（§8.1 第 1/3/5 层）。绝不因 K8s 故障阻断大屏主体。

    返回 (section|None, 状态)：
    - ok：客户端可用且至少一个子源采集成功；
    - unavailable：客户端缺失/构造失败/全部子源失败；
    - disabled：客户端显式关闭（status().mode ∈ disabled/off/none）。
    """
    client, err = _k8s_client_or_none()
    if client is None:
        partial_errors.append(f"K3s 客户端不可用: {err or '未知原因'}")
        return None, "unavailable"
    if _k8s_status_mode(client) in ("disabled", "off", "none"):
        return None, "disabled"
    try:
        section = _build_k3s_section(client, partial_errors)
    except Exception as e:
        partial_errors.append(f"K3s 采集失败: {type(e).__name__}")
        return None, "unavailable"
    if all(v is None for v in section.values()):
        return None, "unavailable"
    return section, "ok"


def _probe_tcp(host: str, port: int, timeout: float = _TCP_PROBE_TIMEOUT) -> tuple[bool, Optional[float], Optional[str]]:
    """有界 TCP 连通探测（ClusterIP 只能在集群内访问）。返回 (ok, latency_ms, 脱敏 error)。"""
    started = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, round((time.perf_counter() - started) * 1000, 1), None
    except Exception as e:
        return False, None, f"{type(e).__name__}"


def _k8s_service_endpoints(services_payload) -> dict:
    """k8s services JSON -> {小写服务名: (ClusterIP, 端口)}。跳过 headless/无 ClusterIP/无端口。"""
    endpoints = {}
    for svc in (services_payload or {}).get("items") or []:
        meta = svc.get("metadata") or {}
        spec = svc.get("spec") or {}
        name = (meta.get("name") or "").strip().lower()
        ip = spec.get("clusterIP")
        if not name or not ip or ip == "None":
            continue
        port = next((p.get("port") for p in spec.get("ports") or [] if p.get("port")), None)
        if port:
            endpoints.setdefault(name, (ip, int(port)))
    return endpoints


def _build_services_dual(enabled: list, state_by_key: dict, partial_errors: list) -> list:
    """双层服务健康（§7.2）：external=广场探针（用户访问通道）；internal=尽力而为。

    名称匹配是启发式：plaza 服务名 == k8s Service 名（忽略大小写与首尾空白）。
    仅当后端运行在集群内（client.status().mode == 'in_cluster'）且按名匹配到
    ClusterIP Service 时，对 ClusterIP:port 做一次 2s 有界 TCP 探测。
    匹配不到或不在集群内时 internal=None —— null 表示“不可探测”，不是 false。
    """
    dual = []
    endpoints: dict = {}
    in_cluster = False
    client, _err = _k8s_client_or_none()
    if client is not None and _k8s_status_mode(client) == "in_cluster":
        try:
            endpoints = _k8s_service_endpoints(client.list_services(None))
            in_cluster = True
        except Exception as e:
            partial_errors.append(f"K3s 内部健康探测不可用: {type(e).__name__}")
    for item in enabled:
        state = state_by_key.get(item["key"])
        status = "disabled" if item.get("probe_enabled") is False else (
            state.stable_status if state else "unknown"
        )
        external = None
        if state is not None and status in ("up", "degraded", "down"):
            external = {
                "ok": status in ("up", "degraded"),
                "latency_ms": round(state.last_latency_ms, 1) if state.last_latency_ms is not None else None,
                "url": item.get("entry_url") or item.get("url"),
                "error": (state.last_error or None) and str(state.last_error)[:200],
            }
        internal = None
        ep = endpoints.get((item.get("name") or "").strip().lower())
        if in_cluster and ep:
            ip, port = ep
            ok, latency, error = _probe_tcp(ip, port)
            internal = {"ok": ok, "latency_ms": latency, "via": f"clusterip:{ip}:{port}", "error": error}
        dual.append({"name": item.get("name"), "status": status, "external": external, "internal": internal})
    return dual


def _services_sections(partial_errors: list) -> tuple[dict, list, list, Optional[str], bool]:
    """服务广场健康 + services_dual（§7.2）。独立 DB 会话，可在工作线程并行执行。

    返回 (services_summary, service_list, services_dual, services_at, ok)。
    """
    empty_summary = {"total": 0, "up": 0, "down": 0, "incidents": 0}
    try:
        with get_db() as db:
            servers = db.query(Server).all()
            from app.plaza import _load_plaza_items
            catalog, plaza_servers = _load_plaza_items()
            # With no managed hosts, keep the legacy empty-screen contract. Static
            # catalog entries are not deployed services until a host exists.
            enabled = [item for item in catalog if item.get("enabled")] if servers else []
            keys = [item["key"] for item in enabled]
            state_by_key = {
                state.plaza_key: state for state in db.query(PlazaHealthState).filter(
                    PlazaHealthState.plaza_key.in_(keys)
                ).all()
            } if keys else {}
            service_list = []
            status_counts = {"up": 0, "down": 0}
            incidents = 0
            for item in enabled:
                state = state_by_key.get(item["key"])
                status = "disabled" if item.get("probe_enabled") is False else (
                    state.stable_status if state else "unknown"
                )
                if status in status_counts:
                    status_counts[status] += 1
                incidents += bool(state and state.active_incident_id)
                server = plaza_servers.get(item.get("server_host"))
                service_list.append({
                    "id": f"plaza:{item['key']}", "name": item["name"],
                    "category": item.get("category"), "status": status,
                    "server_name": server.name if server else item.get("server_host", ""),
                    "last_checked": state.last_checked_at.isoformat() if state and state.last_checked_at else None,
                })
            services_summary = {
                "total": len(service_list), "up": status_counts["up"],
                "down": status_counts["down"], "incidents": incidents,
            }
            checked_times = [item["last_checked"] for item in service_list if item.get("last_checked")]
            services_at = max(checked_times) if checked_times else None
            services_dual = _build_services_dual(enabled, state_by_key, partial_errors)
        return services_summary, service_list, services_dual, services_at, True
    except Exception as e:
        partial_errors.append(f"服务健康快照失败: {type(e).__name__}")
        return empty_summary, [], [], None, False


def _logs_section(partial_errors: list) -> dict:
    """日志采集器概览（复用 alloy_manager DB 缓存，独立会话，可并行）。"""
    try:
        from app.alloy_manager import alloy_overview
        logs = alloy_overview(probe=False)
        return {
            "total": logs.get("total", 0),
            "fresh": None,
            "stale": None,
            "abnormal": logs.get("abnormal", 0),
            "running": logs.get("running", 0),
        }
    except Exception as e:
        partial_errors.append(f"日志汇总失败: {type(e).__name__}")
        return {"total": 0, "fresh": 0, "stale": 0, "abnormal": 0}


def _build_screen_business() -> tuple[dict, str]:
    """构造大屏业务载荷（不含 data_timestamp/cached/cache_age_seconds 等响应级字段）。

    - 主线程完成主机/容器/数据库/告警/趋势的 DB 聚合（单一会话，逻辑与 v4.8 一致）。
    - 服务健康（含 services_dual）/日志/K3s 三个子源提交线程池并行采集（≤6）。
    - 任何子源失败只降级该子源并写入 partial_errors，不阻断整体响应（§8.2）。
    返回 (payload, data_timestamp)。
    """
    started = time.time()
    partial_errors: list = []
    generated_at = datetime.utcnow().isoformat() + "Z"
    now = datetime.utcnow()
    freshness = {"metrics_at": None, "services_at": None, "wireguard_at": None}
    source_status = {"k3s": "unavailable", "hosts": "ok", "services": "ok", "wireguard": "unknown"}

    with ThreadPoolExecutor(max_workers=_SCREEN_MAX_WORKERS) as pool:
        fut_services = pool.submit(_services_sections, partial_errors)
        fut_logs = pool.submit(_logs_section, partial_errors)
        fut_k3s = pool.submit(_k3s_section, partial_errors)

        with get_db() as db:
            # --- 主机：每台主机/指标走组合索引取最新值，避免扫描三天的全部历史数据 ---
            server_list = []
            hosts_summary = {"total": 0, "online": 0, "offline": 0, "stale": 0}
            servers = db.query(Server).all()
            latest = {}
            try:
                rows = db.execute(
                    text(
                        "SELECT s.id AS server_id, wanted.metric, latest.value, latest.timestamp "
                        "FROM servers AS s "
                        "CROSS JOIN (VALUES ('cpu'), ('memory'), ('disk')) AS wanted(metric) "
                        "LEFT JOIN LATERAL ("
                        "  SELECT value, timestamp FROM metric_history "
                        "  WHERE server_id = s.id AND metric = wanted.metric AND timestamp >= :cutoff "
                        "  ORDER BY timestamp DESC LIMIT 1"
                        ") AS latest ON TRUE "
                        "WHERE latest.timestamp IS NOT NULL"
                    ),
                    {"cutoff": now - timedelta(days=3)},
                ).fetchall()
                for sid, metric, value, ts in rows:
                    latest.setdefault(str(sid), {})[metric] = {
                        "value": round(float(value), 1) if value is not None else None,
                        "ts": ts,
                    }
                fts = [row[3] for row in rows if row[3]]
                if fts:
                    freshness["metrics_at"] = min(fts).isoformat() + "Z"
            except Exception as e:
                source_status["hosts"] = "error"
                partial_errors.append(f"主机指标聚合失败: {type(e).__name__}")
            for srv in servers:
                rec = latest.get(str(srv.id), {})
                online = srv.status == "online"
                metric_ts = (rec.get("cpu") or {}).get("ts")
                stale = _metric_is_stale(online, metric_ts, now)
                if online:
                    hosts_summary["online"] += 1
                elif srv.status == "offline":
                    hosts_summary["offline"] += 1
                if stale:
                    hosts_summary["stale"] += 1
                server_list.append({
                    "id": str(srv.id), "name": srv.name, "host": srv.host,
                    "status": srv.status, "last_seen": srv.last_seen.isoformat() + "Z" if srv.last_seen else None,
                    "cpu": (rec.get("cpu") or {}).get("value"),
                    "memory": (rec.get("memory") or {}).get("value"),
                    "disk": (rec.get("disk") or {}).get("value"),
                    "metrics_at": metric_ts.isoformat() + "Z" if metric_ts else None,
                    "stale": stale,
                })
            hosts_summary["total"] = len(servers)

            # --- 容器：复用最近 Agent 采集摘要缓存，不触发容器列表/SSH/docker stats ---
            containers_summary = {"running": 0, "stopped": 0, "unknown_hosts": 0}
            now_ts = time.time()
            for srv in servers:
                snap = _LAST_AGENT_SNAPSHOT.get(str(srv.id))
                if snap and (now_ts - snap["ts"]) < 120:
                    containers_summary["running"] += snap["container_running"]
                    containers_summary["stopped"] += snap["container_stopped"]
                elif srv.status == "online":
                    containers_summary["unknown_hosts"] += 1

            # --- 数据库：实例元数据状态聚合（无实例时 total=0 是正常状态，不是错误） ---
            db_rows = db.query(DatabaseInstance).all()
            databases_summary = {"total": len(db_rows), "connected": 0, "pending": 0, "error": 0}
            for inst in db_rows:
                if inst.status == "online":
                    databases_summary["connected"] += 1
                elif inst.status == "error":
                    databases_summary["error"] += 1
                else:
                    databases_summary["pending"] += 1

            # --- Docker 独立主机数（§8.1 兼容资源区渲染依据，后端只保证数值正确） ---
            docker_hosts_count = sum(1 for srv in servers if srv.docker_available)

            # --- 告警 ---
            alert_counts = dict(
                db.query(AlertEvent.status, func.count(AlertEvent.id))
                .filter(AlertEvent.status.in_(["pending", "firing", "acked"]))
                .group_by(AlertEvent.status)
                .all()
            )
            alerts_summary = {
                "firing": alert_counts.get("pending", 0) + alert_counts.get("firing", 0),
                "acknowledged": alert_counts.get("acked", 0),
            }
            active_alerts = (
                db.query(AlertEvent)
                .filter(AlertEvent.status.in_(["pending", "firing"]))
                .order_by(AlertEvent.created_at.desc())
                .limit(10)
                .all()
            )
            alert_rule_map = {
                row.id: row.name for row in db.query(AlertRule).filter(
                    AlertRule.id.in_({a.rule_id for a in active_alerts})
                ).all()
            } if active_alerts else {}
            alert_server_map = {
                row.id: row.name for row in db.query(Server).filter(
                    Server.id.in_({a.server_id for a in active_alerts})
                ).all()
            } if active_alerts else {}
            alert_list = [
                {
                    "id": str(a.id), "status": a.status,
                    "rule_name": alert_rule_map.get(a.rule_id),
                    "server_name": alert_server_map.get(a.server_id),
                    "current_value": a.current_value,
                    "fired_at": a.fired_at.isoformat() if a.fired_at else None,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in active_alerts
            ]

            # --- 趋势（旧字段保留兼容） ---
            trends = {"cpu": [], "memory": [], "net_rx": [], "net_tx": []}
            for metric, key in (
                ("cpu", "cpu"), ("memory", "memory"),
                ("net_rx", "net_rx"), ("net_tx", "net_tx"),
            ):
                rows = (
                    db.query(MetricHistory)
                    .filter(MetricHistory.metric == metric)
                    .order_by(MetricHistory.timestamp.desc())
                    .limit(60)
                    .all()
                )
                trends[key] = [
                    {"ts": r.timestamp.isoformat() if r.timestamp else None, "value": r.value}
                    for r in reversed(rows)
                ]

        try:
            services_summary, service_list, services_dual, services_at, services_ok = fut_services.result()
            source_status["services"] = "ok" if services_ok else "error"
        except Exception as e:  # 线程池意外异常兜底（正常由 _services_sections 内部降级）
            partial_errors.append(f"服务健康快照失败: {type(e).__name__}")
            source_status["services"] = "error"
            services_summary, service_list, services_dual, services_at = (
                {"total": 0, "up": 0, "down": 0, "incidents": 0}, [], [], None,
            )
        freshness["services_at"] = services_at
        try:
            logs_summary = fut_logs.result()
        except Exception as e:
            partial_errors.append(f"日志汇总失败: {type(e).__name__}")
            logs_summary = {"total": 0, "fresh": 0, "stale": 0, "abnormal": 0}
        try:
            k3s_section, k3s_status = fut_k3s.result()
        except Exception as e:
            partial_errors.append(f"K3s 采集失败: {type(e).__name__}")
            k3s_section, k3s_status = None, "unavailable"
        source_status["k3s"] = k3s_status

    # --- WireGuard：复用 30 秒状态缓存，不重复访问 Agent ---
    wireguard_summary = {"managed": 0, "healthy": 0, "warning": 0, "offline": 0, "unmanaged": 0}
    try:
        wg = _cached_wireguard_snapshot()
        if wg:
            wireguard_summary = wg.get("summary", wireguard_summary)
            freshness["wireguard_at"] = wg.get("generated_at")
            if wg.get("partial_errors"):
                partial_errors.extend(wg["partial_errors"][:5])
            source_status["wireguard"] = "ok"
    except Exception as e:
        partial_errors.append(f"WG 汇总失败: {type(e).__name__}")
        source_status["wireguard"] = "error"

    payload = {
        "generated_at": generated_at,
        "freshness": freshness,
        "partial_errors": partial_errors,
        "source_status": source_status,
        "hosts_summary": hosts_summary,
        "containers_summary": containers_summary,
        "databases_summary": databases_summary,
        "services_summary": services_summary,
        "logs_summary": logs_summary,
        "wireguard_summary": wireguard_summary,
        "alerts_summary": alerts_summary,
        "k3s": k3s_section,
        "services_dual": services_dual,
        "docker_hosts_count": docker_hosts_count,
        "elapsed_ms": int((time.time() - started) * 1000),
        "servers": server_list,
        "services": service_list,
        "active_alerts": alert_list,
        "trends": trends,
    }
    return payload, datetime.utcnow().isoformat() + "Z"


@router.get("/screen/summary")
def get_screen_summary(
    request: Request,
    _: object = Depends(get_current_user),
):
    """监控大屏聚合（v4.8.5 增强）：K3s 优先 + 双层服务健康 + ETag/304 + 5s 响应缓存。

    v4.8 字段全保留：generated_at/freshness/partial_errors/hosts_summary/containers_summary/
    databases_summary/services_summary/logs_summary/wireguard_summary/alerts_summary/
    servers/services/active_alerts/trends；数据缺失返回 null/unknown 而非 0；
    慢子模块通过 partial_errors 降级。

    v4.8.5 新增（需求基线 §8.1/§10）：
    - data_timestamp（聚合完成时刻）/ cached / cache_age_seconds / source_status；
    - k3s：节点/工作负载/Pod/存储/任务/Warning 事件/NetworkPolicy 覆盖率
      （k8s 不可用时为 null，source_status.k3s=unavailable，不阻断大屏主体）；
    - services_dual：双层服务健康（external=广场探针；internal=集群内 ClusterIP 尽力探测，
      null=不可探测而非离线）；
    - docker_hosts_count：docker_available=True 的独立 Docker 主机数；
    - ETag/If-None-Match：业务数据哈希（不含时间戳类字段），命中返回 304 空体；
    - 5s 响应缓存（防击穿），命中时 cached=true 并带 cache_age_seconds。
    """
    now_ts = time.monotonic()
    with _SCREEN_CACHE_LOCK:
        # TTL 内命中：直接返回内存载荷，不碰数据库
        fresh = (
            _SCREEN_CACHE.get("payload") is not None
            and now_ts - _SCREEN_CACHE["stored_at"] < _SCREEN_CACHE_TTL
        )
        if fresh:
            business = _SCREEN_CACHE["payload"]
            data_timestamp = _SCREEN_CACHE["data_timestamp"]
            etag = _SCREEN_CACHE["etag"]
            cache_age = round(now_ts - _SCREEN_CACHE["stored_at"], 3)
        else:
            business = data_timestamp = etag = None
    cached = fresh

    if not fresh:
        # ponytail: TTL 到期就重建。综合载荷含 K3s/WG/日志等非 DB 数据，不能只凭 DB 指纹续期。
        # 构建保持在锁外；若并发重复构建成为可测瓶颈，再增加 single-flight。
        business, data_timestamp = _build_screen_business()
        etag = _screen_etag(business)
        with _SCREEN_CACHE_LOCK:
            _SCREEN_CACHE.update(
                payload=business, etag=etag, stored_at=time.monotonic(),
                data_timestamp=data_timestamp,
            )
        cache_age = 0.0

    if _match_if_none_match(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": f'"{etag}"'})
    body = dict(business)
    body["data_timestamp"] = data_timestamp
    body["cached"] = cached
    body["cache_age_seconds"] = cache_age
    return JSONResponse(content=jsonable_encoder(body), headers={"ETag": f'"{etag}"'})


@router.get("/services/health")
def get_services_health(
    _: object = Depends(get_current_user),
):
    """全量服务健康 + 汇总（优先复用 service_health 模块的运行态快照）。"""
    with get_db() as db:
        services = db.query(Service).filter(Service.hidden != True).all()  # noqa: E712
        try:
            from app.service_health import get_health_snapshot
            snapshot = {r["service_id"]: r for r in get_health_snapshot()}
        except Exception:
            # service_health 未接线时降级为 services 表状态
            snapshot = {}

        items = []
        for s in services:
            st = snapshot.get(str(s.id), {})
            status = st.get("status") or (s.status if s.status in ("up", "down") else "unknown")
            items.append(
                {
                    "id": str(s.id),
                    "name": s.name,
                    "status": status,
                    "health_path": s.health_path,
                    "fail_count": st.get("fail_count", 0),
                    "last_error": st.get("last_error", ""),
                    "last_updated_at": st.get("last_ok") or st.get("last_fail"),
                }
            )
        total = len(items)
        up = sum(1 for i in items if i["status"] == "up")
        down = sum(1 for i in items if i["status"] == "down")
        degraded = sum(1 for i in items if i["status"] == "degraded")
        return {
            "services": items,
            "summary": {
                "total": total,
                "up": up,
                "down": down,
                "degraded": degraded,
                "unknown": total - up - down - degraded,
            },
        }


@router.get("/monitor/history")
def get_monitor_history(
    host: str = Query(..., description="主机名或 IP"),
    metric: str = Query(..., description="指标名：cpu/memory/disk/net_rx/net_tx 等"),
    start: Optional[str] = Query(None, description="开始时间（ISO 格式），默认最近 24h"),
    end: Optional[str] = Query(None, description="结束时间（ISO 格式），默认当前"),
    _: object = Depends(get_current_user),
):
    """历史指标查询（时间范围）。"""
    try:
        end_dt = datetime.fromisoformat(end) if end else datetime.utcnow()
        start_dt = datetime.fromisoformat(start) if start else end_dt - timedelta(hours=24)
    except ValueError:
        raise HTTPException(status_code=400, detail="时间格式错误，请使用 ISO 格式（如 2026-08-16T00:00:00）")
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start 必须早于 end")

    with get_db() as db:
        server = (
            db.query(Server)
            .filter((Server.name == host) | (Server.host == host))
            .first()
        )
        if not server:
            raise HTTPException(status_code=404, detail="主机不存在")
        rows = (
            db.query(MetricHistory)
            .filter(
                MetricHistory.server_id == server.id,
                MetricHistory.metric == metric,
                MetricHistory.timestamp >= start_dt,
                MetricHistory.timestamp <= end_dt,
            )
            .order_by(MetricHistory.timestamp.asc())
            .all()
        )
        return {
            "host": host,
            "metric": metric,
            "points": [
                {
                    "ts": r.timestamp.isoformat() if r.timestamp else None,
                    "value": r.value,
                }
                for r in rows
            ],
        }
