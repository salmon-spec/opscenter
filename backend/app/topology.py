# -*- coding: utf-8 -*-
"""v3.29 T3：服务详情聚合 / 服务拓扑 / 监控大屏聚合 / 服务健康 / 历史指标查询。

- /services/{id}/detail     服务详情（部署位置/版本/运行时长/依赖）
- /topology                 三场景拓扑（cicd / monitoring / gateway），空场景自动播种
- /screen/summary           监控大屏一屏聚合数据
- /services/health          全量服务健康（优先复用 service_health 模块快照）
- /monitor/history          历史指标查询（时间范围）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api_keys import require_api_key
from app.database import get_db
from app.models import AlertEvent, ApiKey, MetricHistory, Server, Service, ServiceRelation

router = APIRouter(prefix="/api/v2", tags=["topology"])

_VALID_SCENARIOS = ("cicd", "monitoring", "gateway")

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
    """服务拓扑：nodes + edges。场景无数据时自动播种默认关系。"""
    if scenario not in _VALID_SCENARIOS:
        raise HTTPException(status_code=400, detail="scenario 仅支持 cicd / monitoring / gateway")
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
            }
            for r in rels
        ]
        return {"scenario": scenario, "nodes": nodes, "edges": edges}


@router.get("/screen/summary")
def get_screen_summary(
    _: Optional[ApiKey] = Depends(require_api_key("read")),
):
    """监控大屏聚合：主机水位 + 服务矩阵 + 活跃告警 + 指标趋势。"""
    with get_db() as db:
        servers = db.query(Server).all()
        server_list = []
        for srv in servers:
            latest = {}
            for metric in ("cpu", "memory", "disk"):
                row = (
                    db.query(MetricHistory)
                    .filter(MetricHistory.server_id == srv.id, MetricHistory.metric == metric)
                    .order_by(MetricHistory.timestamp.desc())
                    .first()
                )
                latest[metric] = round(row.value, 1) if row else None
            server_list.append(
                {
                    "id": str(srv.id),
                    "name": srv.name,
                    "host": srv.host,
                    "status": srv.status,
                    "cpu": latest.get("cpu"),
                    "memory": latest.get("memory"),
                    "disk": latest.get("disk"),
                }
            )

        services = db.query(Service).filter(Service.hidden != True).all()  # noqa: E712
        server_name_map = {s.id: s.name for s in servers}
        service_list = [
            {
                "id": str(s.id),
                "name": s.name,
                "category": s.category,
                "status": s.status,
                "server_name": server_name_map.get(s.server_id),
            }
            for s in services
        ]

        active_alerts = (
            db.query(AlertEvent)
            .filter(AlertEvent.status.in_(["pending", "firing"]))
            .order_by(AlertEvent.created_at.desc())
            .limit(10)
            .all()
        )
        alert_list = [
            {
                "id": str(a.id),
                "status": a.status,
                "current_value": a.current_value,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in active_alerts
        ]

        # 指标趋势：各取最近 60 个点（时序升序返回）
        trends = {"cpu": [], "memory": [], "net_rx": [], "net_tx": []}
        for metric, key in (
            ("cpu", "cpu"),
            ("memory", "memory"),
            ("net_rx", "net_rx"),
            ("net_tx", "net_tx"),
        ):
            rows = (
                db.query(MetricHistory)
                .filter(MetricHistory.metric == metric)
                .order_by(MetricHistory.timestamp.desc())
                .limit(60)
                .all()
            )
            trends[key] = [
                {
                    "ts": r.timestamp.isoformat() if r.timestamp else None,
                    "value": r.value,
                }
                for r in reversed(rows)
            ]

        return {
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
