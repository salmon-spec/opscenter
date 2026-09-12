"""主机详情侧栏后端（需求基线 2026-09-10 §9/§10）。

契约（主控冻结，Wave1-BE2 实现本文件）：
- router = APIRouter(prefix="/api/v2", tags=["server-details"])
- GET  /servers/{id}/overview        基本信息与最新指标快照（300ms 目标：不等待慢源）
- GET  /servers/{id}/metrics/trends  ?range=1h|6h|24h|7d|30d&metrics=cpu,memory,disk,network
- GET  /servers/{id}/services        资产服务 + 广场健康（内部/外部）
- GET  /servers/{id}/connectivity    LAN/WG 双通道诊断（首选通道失败只做一次有界备用探测）
- POST /servers/{id}/agent/check     Agent 版本检查（只读，不部署）
- GET  /servers/{id}/agent/upgrade/status  读取 app.agent_tasks.AGENT_TASKS 注册表
- Agent 升级继续使用 main.py 既有 POST /servers/{id}/upgrade-agent（已带 task_id）。
- models.Server 的新列（lan_ip/wireguard_ip/…）已由主控冻结，本模块只读不迁移。

实现说明：
- 时间统一 UTC；响应内 ISO 字符串带 Z 后缀；趋势点 t 为 epoch 秒。
- 指标名与 main.py 采集循环写入名一致：cpu/memory/disk/load1/net_rx/net_tx
  （响应字段映射为 load/net_in/net_out）。
- trends 粒度（§8.2）：1h/6h 用原始点；24h/7d/30d 强制 1h 聚合桶，
  1h 桶缺失时回退 5m 桶并在 notes 说明；绝不整段拉取原始点。
- connectivity 每请求最多 2 次探测（主通道 + 一次备用），结果持久化到
  servers.last_lan_probe / last_wg_probe（{ok,latency_ms,error,checked_at}）。
- 所有错误信息经脱敏（不携带密码/Token）；overview 的 K8s 节点快照尽力而为、
  有 0.9s 预算，失败降级为 null + notes。
"""

from __future__ import annotations

import calendar
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.agent_manager import get_agent_version
from app.agent_tasks import AGENT_TASKS
from app.auth import require_operator
from app.config import LOCAL_AGENT_HOST
from app.database import get_db
from app.freshness import METRIC_STALENESS_SECONDS, freshness_fields
from app.models import MetricHistory, MetricRollup, PlazaHealthState, Server, Service

router = APIRouter(prefix="/api/v2", tags=["server-details"], dependencies=[Depends(require_operator)])

_PROBE_TIMEOUT = 3.0
_K8S_LOOKUP_BUDGET = 0.9
_RAW_POINT_CAP = 2500
_LATEST_METRICS = ("cpu", "memory", "disk", "load1", "net_rx", "net_tx")
_TREND_RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600, "7d": 7 * 86400, "30d": 30 * 86400}
_TREND_METRICS = ("cpu", "memory", "disk", "network")
_K8S_MONITOR_FNS = ("node_snapshot", "get_node_snapshot", "get_cached_node", "cached_node", "lookup_node", "get_node")
_K8S_CLIENT_FNS = ("list_nodes", "list_k8s_nodes")


def _sanitize(value, limit: int = 200) -> str | None:
    """错误脱敏：不携带密码/Token 等凭据片段。"""
    text = str(value or "")
    for marker in ("Bearer ", "Authorization", "token=", "__password__", "ssh_key", "agent_token"):
        text = text.replace(marker, "***")
    text = text.strip()
    return text[:limit] if text else None


def _version_parts(value: str | None) -> tuple[int, ...]:
    """与 main.py:_version_parts 等价的本地副本（不得 import main）。"""
    parts = re.findall(r"\d+", value or "")
    return tuple(int(part) for part in parts[:4]) if parts else (0,)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _load_server(server_id: str) -> Server:
    try:
        uid = uuid.UUID(server_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(422, "非法主机 ID")
    with get_db() as db:
        row = db.query(Server).filter(Server.id == uid).first()
        if not row:
            raise HTTPException(404, "Server not found")
        db.expunge(row)
        return row


# ── K8s 节点快照（尽力而为，短预算） ─────────────────────────────────────────

def _lookup_k8s_node(node_name: str) -> tuple[dict | None, str | None]:
    """优先复用 app.k8s_monitor 的缓存函数，其次 app.k8s_client.list_nodes。

    两个模块由并行开发（BE-1）提供，此处在模块缺失/签名不符时全部降级为 None+note，
    绝不让 overview 因慢源阻塞。
    """
    try:
        from app import k8s_monitor
        for name in _K8S_MONITOR_FNS:
            fn = getattr(k8s_monitor, name, None)
            if callable(fn):
                try:
                    snapshot = fn(node_name)
                except TypeError:
                    continue  # 签名不匹配，尝试下一个候选
                return (snapshot if isinstance(snapshot, dict) else None), None
    except Exception:
        pass
    try:
        from app import k8s_client
        fn = None
        for name in _K8S_CLIENT_FNS:
            candidate = getattr(k8s_client, name, None)
            if callable(candidate):
                fn = candidate
                break
        if fn:
            nodes = fn()
            for node in nodes or []:
                if isinstance(node, dict) and (
                    node.get("name") == node_name
                    or (node.get("metadata") or {}).get("name") == node_name
                ):
                    return node, None
            return None, f"K8s 节点 {node_name} 未在集群快照中找到"
    except Exception as exc:
        return None, f"K8s 节点快照暂不可用（{type(exc).__name__}），稍后重试"
    return None, "K8s 节点快照源尚未接入（k8s_monitor/k8s_client 未提供可复用函数）"


def _k8s_node_bounded(node_name: str) -> tuple[dict | None, list[str]]:
    """把未知实现的 K8s 查询关进 0.9s 预算里，超时放弃（后台线程自然结束）。"""
    if not node_name:
        return None, []
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="k8s-node-lookup")
    try:
        future = pool.submit(_lookup_k8s_node, node_name)
        try:
            snapshot, note = future.result(timeout=_K8S_LOOKUP_BUDGET)
            return snapshot, ([note] if note else [])
        except Exception:
            return None, [f"K8s 节点快照查询超过 {_K8S_LOOKUP_BUDGET}s 预算，已放弃本次"]
    finally:
        pool.shutdown(wait=False)


# ── 1. GET /servers/{id}/overview ────────────────────────────────────────────

def _summary_cache_metrics(server_id) -> dict:
    """metric_history 为空时回退读 system_control._SUMMARY_CACHE（进程内，非网络）。"""
    try:
        from app.system_control import _SUMMARY_CACHE
        cached = _SUMMARY_CACHE.get(str(server_id)) or {}
        data = cached.get("data") or {}
        metrics = data.get("metrics") or {}
        stamp = data.get("timestamp")
        ts = None
        if isinstance(stamp, (int, float)):
            ts = datetime.fromtimestamp(stamp, timezone.utc).replace(tzinfo=None)
        latest = {}
        for name in _LATEST_METRICS:
            value = metrics.get(name)
            if value is not None:
                latest[name] = (float(value), ts)
        return latest
    except Exception:
        return {}


@router.get("/servers/{server_id}/overview")
def server_overview(server_id: str):
    server = _load_server(server_id)
    notes: list[str] = []
    partial_errors: list[str] = []
    latest: dict = {}
    with get_db() as db:
        for metric in _LATEST_METRICS:
            row = db.query(MetricHistory).filter(
                MetricHistory.server_id == server.id,
                MetricHistory.metric == metric,
            ).order_by(MetricHistory.timestamp.desc()).first()
            if row:
                latest[metric] = (float(row.value), row.timestamp)
    metrics_source = "ok"
    if not latest:
        latest = _summary_cache_metrics(server.id)
        if latest:
            metrics_source = "fallback_summary_cache"
            notes.append("metric_history 暂无数据，已回退到进程内摘要缓存")
            partial_errors.append("metrics: metric_history 为空，回退进程内摘要缓存")
    if not latest:
        metrics_source = "empty"
        notes.append("暂无监控数据（等待 Agent 采集循环写入）")
        partial_errors.append("metrics: 无数据")

    def metric_value(name: str):
        item = latest.get(name)
        return round(item[0], 4) if item else None

    stamps = [item[1] for item in latest.values() if item[1]]
    metrics = {
        "cpu": metric_value("cpu"),
        "memory": metric_value("memory"),
        "disk": metric_value("disk"),
        "load": metric_value("load1"),
        "net_in": metric_value("net_rx"),
        "net_out": metric_value("net_tx"),
        "ts": _iso(max(stamps)) if stamps else None,
    }

    k8s_node = None
    k8s_source = "skipped"
    if server.kubernetes_node_name:
        k8s_node, k8s_notes = _k8s_node_bounded(server.kubernetes_node_name)
        notes.extend(k8s_notes)
        partial_errors.extend(k8s_notes)
        k8s_source = "ok" if k8s_node else "unavailable"

    # 新鲜度契约（§8.3）：data_timestamp 取最新一个指标采样时刻（无数据则回退响应时刻，
    # 同时 metrics 源标记为 empty 且 stale=True）；阈值沿用 topology 的 90s。
    return {
        "server": {
            "id": str(server.id),
            "name": server.name,
            "host": server.host,
            "ssh_port": server.ssh_port,
            "ssh_user": server.ssh_user,
            "status": server.status,
            "tags": server.tags or [],
            "remark": server.remark or "",
            "agent_status": server.agent_status or "not_deployed",
            "agent_version": server.agent_version or "",
            "agent_port": server.agent_port or 19100,
            "agent_type": server.agent_type or "remote",
            "log_agent_status": server.log_agent_status or "unknown",
            "lan_ip": server.lan_ip or "",
            "wireguard_ip": server.wireguard_ip or "",
            "preferred_management_channel": server.preferred_management_channel or "auto",
            "management_address_override": server.management_address_override or "",
            "kubernetes_node_name": server.kubernetes_node_name or "",
            "node_role": server.node_role or "",
            "runtime_type": server.runtime_type or "",
            "last_seen": _iso(server.last_seen),
            "last_online_at": _iso(server.last_online_at),
            "last_error": server.last_error or "",
        },
        "metrics": metrics,
        "k8s_node": k8s_node,
        "notes": notes,
        **freshness_fields(
            data_timestamp=max(stamps) if stamps else None,
            source_status={"metrics": metrics_source, "k8s_node": k8s_source},
            partial_errors=partial_errors,
            staleness_seconds=METRIC_STALENESS_SECONDS,
        ),
    }


# ── 2. GET /servers/{id}/metrics/trends ──────────────────────────────────────

def _thin_points(points: list, limit: int = _RAW_POINT_CAP) -> list:
    if len(points) <= limit:
        return points
    step = max(1, len(points) // limit)
    return points[::step][:limit]


@router.get("/servers/{server_id}/metrics/trends")
def server_metric_trends(
    server_id: str,
    range: str = Query("6h", pattern="^(1h|6h|24h|7d|30d)$"),
    metrics: str = Query("cpu,memory,disk,network"),
):
    server = _load_server(server_id)
    wanted = list(dict.fromkeys(item.strip() for item in (metrics or "").split(",") if item.strip()))
    invalid = [item for item in wanted if item not in _TREND_METRICS]
    if invalid or not wanted:
        raise HTTPException(422, "metrics 仅支持 cpu/memory/disk/network")
    names: list[str] = []
    for item in wanted:
        names.extend(["net_rx", "net_tx"] if item == "network" else [item])

    span = _TREND_RANGES[range]
    end = datetime.utcnow()
    start = end - timedelta(seconds=span)
    aggregated = span >= 24 * 3600  # §8.2：24h/7d/30d 禁止整段拉原始点
    notes: list[str] = []
    resolution = "1h" if aggregated else "raw"

    points_by_metric: dict[str, list] = {name: [] for name in names}
    with get_db() as db:
        if not aggregated:
            rows = db.query(MetricHistory).filter(
                MetricHistory.server_id == server.id,
                MetricHistory.metric.in_(names),
                MetricHistory.timestamp >= start,
            ).order_by(MetricHistory.timestamp.asc()).all()
            for row in rows:
                points_by_metric[row.metric].append((row.timestamp, float(row.value)))
            if rows:
                notes.append(f"range={range} 使用原始采样点（约 30s 间隔）")
            else:
                notes.append("所选时间范围内暂无原始采样点")
        else:
            def rollup_rows(res: str):
                return db.query(MetricRollup).filter(
                    MetricRollup.server_id == server.id,
                    MetricRollup.metric.in_(names),
                    MetricRollup.resolution == res,
                    MetricRollup.bucket_at >= start,
                ).order_by(MetricRollup.bucket_at.asc()).all()

            rows = rollup_rows("1h")
            if rows:
                for row in rows:
                    points_by_metric[row.metric].append((row.bucket_at, float(row.value_avg)))
            else:
                rows = rollup_rows("5m")
                if rows:
                    resolution = "5m"
                    notes.append("1h 聚合桶尚未生成，已回退到 5m 聚合桶")
                    for row in rows:
                        points_by_metric[row.metric].append((row.bucket_at, float(row.value_avg)))
                else:
                    notes.append("所选时间范围的聚合数据尚未生成（rollup 任务每 5 分钟构建）")

    def epoch(value: datetime) -> int:
        return calendar.timegm(value.utctimetuple())

    series: dict = {}
    for item in wanted:
        if item == "network":
            series["network"] = {
                "in": _thin_points([{"t": epoch(ts), "v": round(v, 4)} for ts, v in points_by_metric["net_rx"]]),
                "out": _thin_points([{"t": epoch(ts), "v": round(v, 4)} for ts, v in points_by_metric["net_tx"]]),
            }
        else:
            series[item] = _thin_points([{"t": epoch(ts), "v": round(v, 4)} for ts, v in points_by_metric[item]])

    return {
        "range": range,
        "interval_seconds": {"raw": 0, "5m": 300, "1h": 3600}[resolution],
        "resolution": resolution,
        "series": series,
        "notes": notes,
    }


# ── 3. GET /servers/{id}/services ────────────────────────────────────────────

@router.get("/servers/{server_id}/services")
def server_services(server_id: str):
    server = _load_server(server_id)
    notes = ["服务双层健康（内部/外部）由服务广场后续迭代提供"]
    with get_db() as db:
        services = db.query(Service).filter(Service.server_id == server.id).order_by(
            Service.sort_order.asc(), Service.name.asc(),
        ).all()
        keys = {
            svc.id: (f"manual-{svc.id}" if (svc.source or "") == "manual" else f"scan-{svc.id}")
            for svc in services
        }
        states = {}
        if keys:
            for state in db.query(PlazaHealthState).filter(PlazaHealthState.plaza_key.in_(list(keys.values()))).all():
                states[state.plaza_key] = state
    result = []
    hidden_count = 0
    for svc in services:
        if svc.hidden:
            hidden_count += 1
            continue
        state = states.get(keys[svc.id])
        result.append({
            "id": str(svc.id),
            "name": svc.name,
            "source": svc.source,
            "port": svc.port,
            "url": svc.url,
            "health": {
                "status": state.stable_status if state else "unknown",
                "internal": None,
                "external": None,
                "last_change_at": _iso(state.last_transition_at) if state else None,
                "last_checked_at": _iso(state.last_checked_at) if state else None,
                "last_http_status": state.last_http_status if state else None,
                "latency_ms": state.last_latency_ms if state else None,
                "error": _sanitize(state.last_error, 300) if state else None,
            },
        })
    if hidden_count:
        notes.append(f"{hidden_count} 个已隐藏服务未展示")
    return {"services": result, "notes": notes}


# ── 4. GET /servers/{id}/connectivity ────────────────────────────────────────

def _probe_agent_health(addr: str, port: int, timeout: float = _PROBE_TIMEOUT) -> dict:
    """探测 Agent /health（免鉴权）。返回 {ok, latency_ms, error}；错误不含凭据。"""
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"http://{addr}:{port}/health")
        latency = round((time.perf_counter() - started) * 1000, 2)
        if response.status_code == 200:
            return {"ok": True, "latency_ms": latency, "error": None}
        return {"ok": False, "latency_ms": latency, "error": f"HTTP {response.status_code}"}
    except Exception as exc:
        return {"ok": False, "latency_ms": None, "error": f"{type(exc).__name__}: {exc}"}


def _channel_candidates(server: Server) -> list[tuple[str, str]]:
    """Monitoring is LAN-first; without LAN, use the configured main address."""
    lan_ip = (server.lan_ip or "").strip()
    external = (server.host or "").strip()
    primary = ("lan", lan_ip) if lan_ip else ("wireguard", external)
    backup = ("wireguard", external) if lan_ip and external and external != lan_ip else None

    candidates = [primary]
    if backup and backup[1] and backup[0] != primary[0] and backup[1] != primary[1]:
        candidates.append(backup)
    return candidates


@router.get("/servers/{server_id}/connectivity")
def server_connectivity(server_id: str):
    server = _load_server(server_id)
    notes: list[str] = []
    is_local = server.effective_is_local
    if is_local:
        # 本机：走 LOCAL_AGENT_HOST（与 agent_manager.resolve_agent_host 语义一致），
        # 单次探测，不区分 LAN/WG，不重复探测。
        candidates: list[tuple[str, str]] = [("lan", LOCAL_AGENT_HOST)]
        notes.append("本机主机走本地回环/host.docker.internal，不区分 LAN/WG 通道")
    else:
        candidates = _channel_candidates(server)

    probe_results: dict[str, dict] = {}      # 持久化形态：{ok,latency_ms,error,checked_at}
    response_results: dict[str, dict] = {}   # 响应形态：额外带 channel/target
    primary_channel, primary_addr = candidates[0]
    effective_target = primary_addr
    ok_channel = None
    for index, (channel, addr) in enumerate(candidates):
        raw = _probe_agent_health(addr, server.agent_port or 19100)
        entry = {
            "ok": bool(raw.get("ok")),
            "latency_ms": raw.get("latency_ms"),
            "error": _sanitize(raw.get("error")),
            "checked_at": _now_iso(),
        }
        probe_results[channel] = entry
        response_results[channel] = {**entry, "channel": channel, "target": addr}
        if entry["ok"]:
            ok_channel = channel
            effective_target = addr
            break
        if index >= 1:
            break  # 主通道失败后只做一次备用探测，绝不双路无限重试
    preferred = primary_channel
    if ok_channel and ok_channel != primary_channel:
        notes.append(f"{primary_channel} 主通道失败，已启用 {ok_channel} 备用通道")
    if not ok_channel and len(candidates) > 1:
        notes.append("主/备通道均探测失败（每请求最多 2 次探测）")
    elif not ok_channel:
        notes.append("Agent 不可达")

    with get_db() as db:
        row = db.query(Server).filter(Server.id == server.id).first()
        if row:
            if "lan" in probe_results:
                row.last_lan_probe = probe_results["lan"]
            if "wireguard" in probe_results:
                row.last_wg_probe = probe_results["wireguard"]
            db.commit()

    return {
        "preferred": preferred,
        "effective_target": effective_target,
        "lan": response_results.get("lan"),
        "wireguard": response_results.get("wireguard"),
        "agent": {
            "status": server.agent_status or "not_deployed",
            "version": server.agent_version or "",
            "port": server.agent_port or 19100,
        },
        "notes": notes,
    }


# ── 5. POST /servers/{id}/agent/check ────────────────────────────────────────

def _probe_agent_api(addr: str, port: int, token: str, timeout: float = _PROBE_TIMEOUT) -> bool | None:
    """带 Bearer 的最小只读校验（/api/v1/system/summary）。失败不抛出。"""
    if not token:
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(
                f"http://{addr}:{port}/api/v1/system/summary",
                headers={"Authorization": f"Bearer {token}"},
            )
        return response.status_code == 200
    except Exception:
        return False


@router.post("/servers/{server_id}/agent/check")
def agent_check(server_id: str):
    server = _load_server(server_id)
    current = (server.agent_version or "").strip()
    target = get_agent_version()
    outdated = _version_parts(current) < _version_parts(target)
    notes: list[str] = []
    if outdated and current:
        notes.append(f"Agent 当前版本 v{current} 低于目标版本 v{target}，可在侧栏执行升级")
    reachable = None
    if server.agent_status == "running":
        channel, addr = _channel_candidates(server)[0]
        reachable = _probe_agent_api(addr, server.agent_port or 19100, server.agent_token or "")
        if reachable is None:
            notes.append("未配置 Agent Token，跳过在线验证（仅版本对比）")
        elif reachable is False:
            notes.append(f"在线验证失败：{channel} 通道 {addr} 只读接口不可达或 Token 无效（不影响版本对比结论）")
    else:
        notes.append("主机 Agent 未处于 running 状态，跳过在线验证")
    return {
        "current_version": current,
        "target_version": target,
        "outdated": outdated,
        "reachable": reachable,
        "notes": notes,
    }


# ── 6. GET /servers/{id}/agent/upgrade/status ────────────────────────────────

@router.get("/servers/{server_id}/agent/upgrade/status")
def agent_upgrade_status(server_id: str):
    server = _load_server(server_id)
    task = AGENT_TASKS.get(server_id)
    payload = {
        "task": task,
        "agent_status": server.agent_status or "not_deployed",
        "last_error": _sanitize(server.last_error, 1000),
    }
    if task:
        payload["success_criteria"] = "新版本进程运行 /health 200 / Bearer 可读"
    return payload
