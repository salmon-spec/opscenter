# -*- coding: utf-8 -*-
"""v3.29 T3：服务详情聚合 / 服务拓扑 / 监控大屏聚合 / 服务健康 / 历史指标查询。

- /services/{id}/detail     服务详情（部署位置/版本/运行时长/依赖）
- /topology                 四场景拓扑（cicd / monitoring / gateway / wireguard），空场景自动播种
- /screen/summary           监控大屏一屏聚合数据
- /services/health          全量服务健康（优先复用 service_health 模块快照）
- /monitor/history          历史指标查询（时间范围）
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.agent_manager import (
    AGENT_DEFAULT_PORT,
    fetch_agent_wireguard,
    resolve_agent_host,
)
from app.api_keys import require_api_key
from app.config import CONTAINERIZED, LOCAL_AGENT_HOST
from app.database import get_db
from app.models import AlertEvent, AlertRule, ApiKey, DatabaseInstance, MetricHistory, PlazaHealthState, Server, Service, ServiceRelation
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v2", tags=["topology"])

_VALID_SCENARIOS = ("cicd", "monitoring", "gateway", "wireguard")

# WireGuard 拓扑：按主机缓存 30 秒，避免每次请求都并发访问 Agent
_WG_CACHE_TTL = 30
_WG_CACHE: dict = {}
_WG_CACHE_LOCK = threading.Lock()
_WG_MAX_WORKERS = 6
_WG_AGENT_TIMEOUT = 5

# 健康规则：最近握手 <=180s 健康；181-600s 警告；>600s 或从未握手 离线
_WG_HEALTH_NOW_TOL = 180
_WG_HEALTH_WARN_TOL = 600

# 健康大屏：保留最近一次 Agent 采集摘要（容器计数等），避免为聚合触发容器列表/SSH 探测
_AGENT_SNAPSHOT_LOCK = threading.Lock()
_LAST_AGENT_SNAPSHOT: dict = {}  # server_id -> {container_running, container_stopped, ts}


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

# 默认关系播种规则：场景 -> [(源关键字, 目标关键字|"*", 关系类型, 连线标签)]
# "*" 表示与场景内所有其他服务建边（如 gateway 的 Caddy 反代全部服务）
_SCENARIO_SEEDS = {
    "cicd": [
        ("GitLab", "Jenkins", "invoke", "代码拉取"),
        ("Gitea", "Jenkins", "invoke", "代码拉取"),
        ("Jenkins", "SonarQube", "data_flow", "质量门禁"),
        ("Jenkins", "Nexus", "data_flow", "制品推送"),
    ],
    "monitoring": [
        ("node_exporter", "Prometheus", "data_flow", "指标采集"),
        ("Prometheus", "Grafana", "data_flow", "指标展示"),
        ("Prometheus", "Loki", "data_flow", "日志聚合"),
    ],
    "gateway": [
        ("Caddy", "*", "proxy", "反代转发"),
    ],
}


def _find_service(db, keyword: str) -> Optional[Service]:
    """按名称关键字匹配服务：精确匹配（忽略大小写）优先，其次包含匹配。"""
    kw = keyword.strip().lower()
    services = db.query(Service).filter(Service.hidden != True).all()  # noqa: E712
    for s in services:
        if (s.name or "").strip().lower() == kw:
            return s
    for s in services:
        if kw in (s.name or "").lower():
            return s
    return None


def _upsert_relation(db, src: Service, tgt: Service, rel_type: str, label: str, scenario: str) -> bool:
    """插入一条关系（已存在则跳过），返回是否新建。"""
    exists = (
        db.query(ServiceRelation)
        .filter(
            ServiceRelation.source_service_id == src.id,
            ServiceRelation.target_service_id == tgt.id,
            ServiceRelation.relation_type == rel_type,
        )
        .first()
    )
    if exists:
        return False
    db.add(
        ServiceRelation(
            source_service_id=src.id,
            target_service_id=tgt.id,
            relation_type=rel_type,
            label=label,
            scenario=scenario,
        )
    )
    return True


def seed_default_relations(db) -> int:
    """按服务名匹配播种三场景默认关系（幂等），返回新建数量。"""
    created = 0
    for scenario, rules in _SCENARIO_SEEDS.items():
        for src_kw, tgt_kw, rel_type, label in rules:
            src = _find_service(db, src_kw)
            if src is None:
                continue
            if tgt_kw == "*":
                # gateway：Caddy 反代所有其他服务
                for svc in db.query(Service).filter(Service.hidden != True).all():  # noqa: E712
                    if svc.id == src.id:
                        continue
                    if _upsert_relation(db, src, svc, rel_type, label, scenario):
                        created += 1
                continue
            tgt = _find_service(db, tgt_kw)
            if tgt is None or tgt.id == src.id:
                continue
            if _upsert_relation(db, src, tgt, rel_type, label, scenario):
                created += 1
    db.commit()
    return created


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
    _: Optional[ApiKey] = Depends(require_api_key("read")),
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


@router.get("/topology")
def get_topology(
    scenario: str = Query("cicd"),
    _: Optional[ApiKey] = Depends(require_api_key("read")),
):
    """服务拓扑：nodes + edges。场景无数据时自动播种默认关系。

    scenario=wireguard 时切换到 WireGuard 内网拓扑（见 _build_wireguard_topology）。
    """
    if scenario not in _VALID_SCENARIOS:
        raise HTTPException(status_code=400, detail="scenario 仅支持 cicd / monitoring / gateway / wireguard")
    if scenario == "wireguard":
        return _build_wireguard_topology()
    with get_db() as db:
        rel_count = (
            db.query(ServiceRelation)
            .filter(ServiceRelation.scenario == scenario)
            .count()
        )
        if rel_count == 0:
            seed_default_relations(db)

        rels = (
            db.query(ServiceRelation)
            .filter(ServiceRelation.scenario == scenario)
            .all()
        )
        service_ids = set()
        for r in rels:
            service_ids.add(r.source_service_id)
            service_ids.add(r.target_service_id)

        nodes = []
        if service_ids:
            services = (
                db.query(Service)
                .filter(Service.id.in_(service_ids))
                .all()
            )
            server_ids = {s.server_id for s in services}
            servers = (
                db.query(Server)
                .filter(Server.id.in_(server_ids))
                .all()
            )
            server_map = {s.id: s for s in servers}
            nodes = [
                {
                    "id": str(s.id),
                    "name": s.name,
                    "category": s.category,
                    "status": s.status,
                    "server_name": server_map.get(s.server_id).name if server_map.get(s.server_id) else None,
                }
                for s in services
            ]
        edges = [
            {
                "source": str(r.source_service_id),
                "target": str(r.target_service_id),
                "relation_type": r.relation_type,
                "label": r.label,
                "relation_id": str(r.id),
            }
            for r in rels
        ]
        return {"scenario": scenario, "nodes": nodes, "edges": edges}


# ============ 拓扑手动编辑（v4.8）：布局保存 + 关系增删 ============
# 仅服务拓扑场景（cicd / monitoring / gateway）可编辑；wireguard 保持只读。

class TopologyLayoutIn(BaseModel):
    scenario: str = Field(..., pattern="^(cicd|monitoring|gateway)$")
    positions: dict = Field(default_factory=dict)  # node_id -> {"x": float, "y": float}


class TopologyRelationIn(BaseModel):
    scenario: str = Field(..., pattern="^(cicd|monitoring|gateway)$")
    source_service_id: str
    target_service_id: str
    relation_type: str = Field(..., min_length=1, max_length=30)
    label: str = Field("", max_length=50)


def _topology_layout_path() -> str:
    default_dir = os.path.dirname(os.getenv("GROUPS_JSON_PATH", "/opt/opscenter/frontend/groups.json"))
    return os.getenv("TOPOLOGY_LAYOUT_PATH", os.path.join(default_dir, "topology_layout.json"))


def _read_layouts() -> dict:
    try:
        with open(_topology_layout_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@router.get("/topology/layout")
def get_topology_layout(
    scenario: str = Query("cicd"),
    _: Optional[ApiKey] = Depends(require_api_key("read")),
):
    """读取服务拓扑手动布局（节点坐标）。"""
    if scenario not in ("cicd", "monitoring", "gateway"):
        raise HTTPException(status_code=400, detail="layout 仅支持服务拓扑场景")
    layouts = _read_layouts()
    entry = layouts.get(scenario, {})
    return {"scenario": scenario, "positions": entry.get("positions", {}), "saved_at": entry.get("saved_at")}


@router.post("/topology/layout")
def save_topology_layout(
    req: TopologyLayoutIn,
    _: Optional[ApiKey] = Depends(require_api_key("write")),
):
    """保存服务拓扑手动布局（节点坐标）。"""
    if len(req.positions) > 500:
        raise HTTPException(status_code=400, detail="布局节点数超限")
    layouts = _read_layouts()
    saved_at = datetime.utcnow().isoformat() + "Z"
    layouts[req.scenario] = {"positions": req.positions, "saved_at": saved_at}
    path = _topology_layout_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(layouts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"布局保存失败: {e}")
    return {"ok": True, "scenario": req.scenario, "saved_at": saved_at}


@router.post("/topology/{scenario}/relations")
def create_topology_relation(
    scenario: str,
    req: TopologyRelationIn,
    _: Optional[ApiKey] = Depends(require_api_key("write")),
):
    """新增（或更新）一条服务拓扑关系连线。"""
    if scenario not in ("cicd", "monitoring", "gateway"):
        raise HTTPException(status_code=400, detail="scenario 仅支持 cicd / monitoring / gateway")
    if req.scenario != scenario:
        raise HTTPException(status_code=400, detail="path 与 body 的 scenario 不一致")
    try:
        src_id = uuid.UUID(req.source_service_id)
        tgt_id = uuid.UUID(req.target_service_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="非法服务 ID")
    if src_id == tgt_id:
        raise HTTPException(status_code=400, detail="不能连接自身")
    with get_db() as db:
        src = db.query(Service).filter(Service.id == src_id).first()
        tgt = db.query(Service).filter(Service.id == tgt_id).first()
        if not src or not tgt:
            raise HTTPException(status_code=404, detail="服务不存在")
        exists = (
            db.query(ServiceRelation)
            .filter(
                ServiceRelation.source_service_id == src_id,
                ServiceRelation.target_service_id == tgt_id,
                ServiceRelation.scenario == scenario,
            )
            .first()
        )
        if exists:
            exists.relation_type = req.relation_type
            exists.label = req.label
            rel = exists
        else:
            rel = ServiceRelation(
                source_service_id=src_id, target_service_id=tgt_id,
                relation_type=req.relation_type, label=req.label or None,
                scenario=scenario,
            )
            db.add(rel)
        db.commit()
        return {
            "ok": True,
            "relation": {
                "id": str(rel.id),
                "source_service_id": str(src_id),
                "target_service_id": str(tgt_id),
                "relation_type": rel.relation_type,
                "label": rel.label,
                "scenario": scenario,
            },
        }


@router.delete("/topology/relations/{relation_id}")
def delete_topology_relation(
    relation_id: str,
    _: Optional[ApiKey] = Depends(require_api_key("write")),
):
    """删除一条服务拓扑关系连线（幂等）。"""
    try:
        rid = uuid.UUID(relation_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="非法关系 ID")
    with get_db() as db:
        rel = db.query(ServiceRelation).filter(ServiceRelation.id == rid).first()
        if not rel:
            return {"ok": True, "deleted": False, "message": "关系不存在"}
        scenario = rel.scenario
        db.delete(rel)
        db.commit()
        return {"ok": True, "deleted": True, "scenario": scenario}


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


def _build_wireguard_topology() -> dict:
    """WireGuard 内网拓扑。

    - 从资产表读取主机快照后关闭 DB 会话，再用 ThreadPoolExecutor 并发请求各主机
      Agent（最多 6 个 worker），单主机超时 5 秒（fetch_agent_wireguard 内部超时）。
    - 按主机分别缓存 30 秒；某台超时只标记该节点数据陈旧，不阻塞/清空整个拓扑。
    - 以 Hub 的 AllowedIPs 为权威 IP 分布：精确 /32 IP 与 Server.host 匹配；
      匹配不到显示“未纳管 Peer”，不根据主机名猜测。
    - 节点类型仅 hub / managed_host / unregistered_peer；节点 ID 使用服务器 UUID
      或安全指纹，刷新前后保持稳定。
    - 未纳管 Peer 可见但默认不生成告警事件。
    """
    started = time.time()
    with get_db() as db:
        servers = db.query(Server).all()
        hosts = [
            {
                "id": str(s.id),
                "name": s.name,
                "host": s.host,
                "agent_port": s.agent_port or AGENT_DEFAULT_PORT,
                "agent_token": s.agent_token or "",
                "is_local": s.agent_type == "local",
                "status": s.status,
            }
            for s in servers
        ]

    nodes = []
    edges = []
    partial_errors = []
    managed_found: dict = {}   # host_ip -> server dict
    wg_interfaces: dict = {}   # server_id -> agent wireguard payload

    # 1) 并发拉取各主机 Agent WireGuard 状态
    def _pull(h):
        try:
            return h, _wg_fetch_cached(h["id"], resolve_agent_host_for(h), h["agent_port"], h["agent_token"])
        except Exception as e:
            return h, {"error": f"{type(e).__name__}: {e}"}

    def resolve_agent_host_for(h):
        # Docker 容器内的本机 Agent 通过 host.docker.internal 访问；远程主机直接连其 IP
        return LOCAL_AGENT_HOST if (h["is_local"] and CONTAINERIZED) else h["host"]

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

    # 3) 建节点：Hub 居中；纳管主机；未纳管 Peer
    hub_node = None
    if hub:
        hub_node = {
            "id": hub["id"],
            "name": hub["name"],
            "host": hub["host"],
            "type": "hub",
            "health": "healthy",
            "data_source": "live",
        }
        nodes.append(hub_node)

    hub_peers = []
    if hub:
        for iface in hub_data.get("interfaces") or []:
            hub_peers.extend(iface.get("peers") or [])
    # Hub 的 Peer 计数是本拓扑的权威视角；不要再叠加客户端的反向计数。
    total_rx = sum(peer.get("rx_bytes") or 0 for peer in hub_peers)
    total_tx = sum(peer.get("tx_bytes") or 0 for peer in hub_peers)

    host_by_ip = {h["host"]: h for h in hosts}
    seen_unmanaged: dict = {}  # fingerprint -> node
    for peer in hub_peers:
        ip = _wg_host_ip(peer.get("allowed_ips"))
        health = _wg_health(peer.get("latest_handshake_age_seconds"))
        managed = host_by_ip.get(ip) if ip else None
        if managed:
            node_id = managed["id"]
            if node_id == hub["id"]:
                continue  # Hub 自身不重复建边
            if not any(n["id"] == node_id for n in nodes):
                nodes.append({
                    "id": node_id,
                    "name": managed["name"],
                    "host": managed["host"],
                    "type": "managed_host",
                    "health": health,
                    "wg_ip": ip,
                    "endpoint": peer.get("endpoint"),
                    "latest_handshake_age_seconds": peer.get("latest_handshake_age_seconds"),
                    "latest_handshake_at": peer.get("latest_handshake_at"),
                    "allowed_ips": peer.get("allowed_ips"),
                    "rx_bytes": peer.get("rx_bytes") or 0,
                    "tx_bytes": peer.get("tx_bytes") or 0,
                    "data_source": "live",
                })
            managed_found[ip] = managed
        else:
            fp = peer.get("public_key_fingerprint") or f"peer-{len(seen_unmanaged)}"
            if fp not in seen_unmanaged:
                node = {
                    "id": fp,
                    "name": f"未纳管 Peer · {ip}" if ip else "未纳管 Peer",
                    "host": None,
                    "type": "unregistered_peer",
                    "health": health,
                    "wg_ip": ip,
                    "endpoint": peer.get("endpoint"),
                    "latest_handshake_age_seconds": peer.get("latest_handshake_age_seconds"),
                    "latest_handshake_at": peer.get("latest_handshake_at"),
                    "allowed_ips": peer.get("allowed_ips"),
                    "rx_bytes": peer.get("rx_bytes") or 0,
                    "tx_bytes": peer.get("tx_bytes") or 0,
                    "data_source": "live",
                }
                nodes.append(node)
                seen_unmanaged[fp] = node
        if hub_node:
            edges.append({
                "source": hub["id"],
                "target": managed["id"] if managed else (peer.get("public_key_fingerprint") or f"peer-{len(seen_unmanaged)}"),
                "relation_type": "wireguard_link",
                "label": "握手" if health != "offline" else "未握手",
                "health": health,
                "rx_bytes": peer.get("rx_bytes") or 0,
                "tx_bytes": peer.get("tx_bytes") or 0,
            })

    # 4) 其他纳管主机（不在 Hub 的 AllowedIPs 中）：显示为 managed_host，状态取 Agent 数据
    for h in hosts:
        if any(n["id"] == h["id"] for n in nodes):
            continue
        data = wg_interfaces.get(h["id"])
        health = "unknown"
        if not data:
            health = "unknown"
        nodes.append({
            "id": h["id"],
            "name": h["name"],
            "host": h["host"],
            "type": "managed_host",
            "health": health,
            "wg_ip": None,
            "data_source": "live" if data else "unknown",
        })
        if hub_node and h["id"] != hub["id"]:
            edges.append({
                "source": hub["id"], "target": h["id"],
                "relation_type": "wireguard_link", "label": "未上报", "health": "unknown",
            })

    # 5) 汇总
    managed_nodes = [n for n in nodes if n["type"] in ("hub", "managed_host")]
    unmanaged_nodes = [n for n in nodes if n["type"] == "unregistered_peer"]
    summary = {
        "peer_total": len(hub_peers) if hub else 0,
        "managed": len(managed_nodes),
        "healthy": sum(1 for n in nodes if n["health"] == "healthy"),
        "warning": sum(1 for n in nodes if n["health"] == "warning"),
        "offline": sum(1 for n in nodes if n["health"] == "offline"),
        "unknown": sum(1 for n in nodes if n["health"] == "unknown"),
        "unmanaged": len(unmanaged_nodes),
        "wg_rx_bytes": total_rx,
        "wg_tx_bytes": total_tx,
    }
    return {
        "scenario": "wireguard",
        "nodes": nodes,
        "edges": edges,
        "summary": summary,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "cached": True,
        "partial_errors": partial_errors,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


@router.get("/screen/summary")
def get_screen_summary(
    _: Optional[ApiKey] = Depends(require_api_key("read")),
):
    """监控大屏聚合（v4.8）：一次返回主机/容器/数据库/服务/日志/WG/告警汇总。

    保留旧字段 servers/services/active_alerts/trends 以兼容既有调用方。
    数据缺失返回 null/unknown 而非 0；慢子模块通过 partial_errors 降级。
    """
    started = time.time()
    partial_errors = []
    generated_at = datetime.utcnow().isoformat() + "Z"
    now = datetime.utcnow()
    freshness = {"metrics_at": None, "services_at": None, "wireguard_at": None}

    # --- 主机：单条 DISTINCT ON 分组查询最新 CPU/MEM/DISK（不在循环内逐指标 SQL） ---
    server_list = []
    hosts_summary = {"total": 0, "online": 0, "offline": 0, "stale": 0}
    with get_db() as db:
        servers = db.query(Server).all()
        latest = {}
        try:
            rows = db.execute(
                text(
                    "SELECT DISTINCT ON (server_id, metric) server_id, metric, value, timestamp "
                    "FROM metric_history WHERE timestamp >= :cutoff "
                    "ORDER BY server_id, metric, timestamp DESC"
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
                freshness["metrics_at"] = max(fts).isoformat() + "Z"
        except Exception as e:
            partial_errors.append(f"主机指标聚合失败: {type(e).__name__}")
        for srv in servers:
            rec = latest.get(str(srv.id), {})
            online = srv.status == "online"
            metric_ts = (rec.get("cpu") or {}).get("ts")
            stale = bool(online and (not metric_ts or (now - metric_ts).total_seconds() > 30))
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

        # --- 服务：复用服务广场持久化健康状态，不主动执行探活 ---
        try:
            from app.plaza import _load_plaza_items
            catalog, plaza_servers = _load_plaza_items()
            enabled = [item for item in catalog if item.get("enabled")]
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
            freshness["services_at"] = max(checked_times) if checked_times else None
        except Exception as e:
            services_summary = {"total": 0, "up": 0, "down": 0, "incidents": 0}
            service_list = []
            partial_errors.append(f"服务健康快照失败: {type(e).__name__}")

        # --- 日志：复用日志 Agent overview（DB 缓存），不在大屏触发探测 ---
        try:
            from app.alloy_manager import alloy_overview
            logs = alloy_overview(probe=False)
            logs_summary = {
                "total": logs.get("total", 0),
                "fresh": logs.get("fresh", 0),
                "stale": max(0, logs.get("total", 0) - logs.get("fresh", 0) - logs.get("abnormal", 0)),
                "abnormal": logs.get("abnormal", 0),
                "running": logs.get("running", 0),
            }
        except Exception as e:
            logs_summary = {"total": 0, "fresh": 0, "stale": 0, "abnormal": 0}
            partial_errors.append(f"日志汇总失败: {type(e).__name__}")

        # --- 告警 ---
        alert_rows = db.query(AlertEvent).filter(AlertEvent.status.in_(["pending", "firing", "acked"])).all()
        alerts_summary = {"firing": 0, "acknowledged": 0}
        for a in alert_rows:
            if a.status == "acked":
                alerts_summary["acknowledged"] += 1
            else:
                alerts_summary["firing"] += 1
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

    # --- WireGuard：复用 30 秒拓扑缓存，不重复访问 Agent ---
    wireguard_summary = {"managed": 0, "healthy": 0, "warning": 0, "offline": 0, "unmanaged": 0}
    try:
        wg = _build_wireguard_topology()
        wireguard_summary = wg.get("summary", wireguard_summary)
        freshness["wireguard_at"] = wg.get("generated_at")
        if wg.get("partial_errors"):
            partial_errors.extend(wg["partial_errors"][:5])
    except Exception as e:
        partial_errors.append(f"WG 汇总失败: {type(e).__name__}")

    return {
        "generated_at": generated_at,
        "freshness": freshness,
        "partial_errors": partial_errors,
        "hosts_summary": hosts_summary,
        "containers_summary": containers_summary,
        "databases_summary": databases_summary,
        "services_summary": services_summary,
        "logs_summary": logs_summary,
        "wireguard_summary": wireguard_summary,
        "alerts_summary": alerts_summary,
        "elapsed_ms": int((time.time() - started) * 1000),
        "servers": server_list,
        "services": service_list,
        "active_alerts": alert_list,
        "trends": trends,
    }


@router.get("/services/health")
def get_services_health(
    _: Optional[ApiKey] = Depends(require_api_key("read")),
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
    _: Optional[ApiKey] = Depends(require_api_key("read")),
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
