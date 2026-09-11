"""K3s/Kubernetes 只读监控路由（需求基线 2026-09-10 §4/§10）。

契约（主控冻结并由主控实现——原 BE-1 agent 三次未产出）：
- router = APIRouter(prefix="/api/v2", tags=["kubernetes"])
- 除 GET /clusters 外统一信封：{"data", "data_timestamp"(epoch 秒，前端 kubernetes.js
  unwrap 按 Number 消费), "cached", "cache_age_seconds", "partial_errors", "source_status"}
- GET /clusters 返回裸数组（前端 ClusterSelector 直接消费）。
- 严格只读：全部端点仅调用 app.k8s_client 的 GET 方法；无任何写动词。
- 数据来自 Kubernetes API（httpx 客户端），禁止 kubectl 子进程。
- 集群行存 Cluster 表（models.Cluster）；空库由 /clusters 懒种子默认集群。
- 缓存：模块级 TTL 缓存（K8S_CACHE_TTL，列表类减半）；source_status 记录指标源等子源状态。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_operator
from app.config import K8S_CACHE_TTL
from app.database import get_session
from app.models import Cluster
from app import k8s_client as k8c

router = APIRouter(prefix="/api/v2", tags=["kubernetes"], dependencies=[Depends(require_operator)])

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
# 节点快照缓存（供 server_details.py 的接缝复用：get_cached_node/get_cached_nodes_snapshot）
_NODES: dict = {"ts": 0.0, "items": []}

_LIST_TTL = max(3, K8S_CACHE_TTL // 2)


def _unwrap(payload) -> list:
    """client list 方法返回原始 {'items': [...]}；提取为列表。"""
    if isinstance(payload, dict):
        return payload.get("items") or []
    return payload or []


def _now_ts() -> float:
    return time.time()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _k_time(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return _iso(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except Exception:
        return None


def _envelope(data: Any, *, cached: bool = False, cache_age: float = 0.0,
              partial_errors: Optional[list] = None, source_status: Optional[dict] = None) -> dict:
    return {
        "data": data,
        "data_timestamp": round(_now_ts(), 3),
        "cached": bool(cached),
        "cache_age_seconds": round(float(cache_age or 0.0), 3),
        "partial_errors": list(partial_errors or []),
        "source_status": dict(source_status or {}),
    }


def _cache_get(key: str, ttl: float):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and (_now_ts() - hit["ts"]) < ttl:
            age = _now_ts() - hit["ts"]
            payload = dict(hit["payload"])
            payload["cached"] = True
            payload["cache_age_seconds"] = round(age, 3)
            return payload
    return None


def _cache_put(key: str, payload: dict) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) > 512:
            _CACHE.clear()
        _CACHE[key] = {"ts": _now_ts(), "payload": dict(payload)}


def _get_client():
    client = k8c.get_client()
    if client is None:
        raise HTTPException(503, "Kubernetes 客户端初始化失败")
    st = client.status()
    if not st["ok"]:
        raise HTTPException(503, f"Kubernetes 连接未配置或不可用（mode={st['mode']}）：{st['error']}")
    return client


def _load_cluster(db: Session, cluster_id: str) -> Cluster:
    try:
        import uuid as _uuid
        cid = _uuid.UUID(cluster_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(422, "非法集群 ID")
    row = db.query(Cluster).filter(Cluster.id == cid).first()
    if not row:
        raise HTTPException(404, "集群不存在")
    return row


def _list_items(client, method: str, *args, errors: list, source: str):
    """list 类调用：404 视为资源类型不存在 → 空列表 + source_status 记录（不伪造数据）。"""
    try:
        return _unwrap(getattr(client, method)(*args))
    except k8c.K8sError as exc:
        if exc.status == 404:
            errors.append(f"{source} 资源类型不可用（404）")
            source_status_note(source, "unavailable")
            return []
        errors.append(f"{source} 采集失败：{exc}")
        source_status_note(source, "error")
        return []
    except Exception as exc:
        errors.append(f"{source} 采集失败：{k8c.sanitize_error(exc)}")
        source_status_note(source, "error")
        return []


_SOURCE_STATUS: dict = {}


def source_status_note(source: str, state: str) -> None:
    with _CACHE_LOCK:
        _SOURCE_STATUS[source] = state


def _collect_source_status() -> dict:
    with _CACHE_LOCK:
        return dict(_SOURCE_STATUS)


# ── 集群 CRUD（只读 + 懒种子） ────────────────────────────────

def _seed_default_cluster(db: Session, client) -> Cluster:
    st = client.status()
    mode = st.get("mode") if st.get("mode") in ("in_cluster", "env", "kubeconfig") else "in_cluster"
    row = Cluster(
        name="k3s", type="k3s", api_mode=mode,
        status="ok" if st.get("ok") else "unknown",
        last_error="" if st.get("ok") else (st.get("error") or "")[:500],
        last_success_at=datetime.utcnow() if st.get("ok") else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _sync_cluster_status(row: Cluster, client) -> None:
    """把 client.status() 同步回 Cluster 行（仅变化时 commit）。"""
    try:
        st = client.status()
        ok = bool(st.get("ok"))
        version = ""
        if ok:
            try:
                version = str((client.version() or {}).get("gitVersion") or "")
            except Exception:
                version = ""
        new_status = "ok" if ok else "unreachable"
        new_error = "" if ok else (st.get("error") or "")[:500]
        changed = (row.status != new_status or (row.version or "") != version or (row.last_error or "") != new_error)
        if changed:
            row.status = new_status
            row.version = version or row.version
            row.last_error = new_error
            row.last_success_at = datetime.utcnow() if ok else row.last_success_at
            db_commit_safe()
    except Exception:
        pass


_DB_SESSION_HOLDER: dict = {}


def db_commit_safe() -> None:
    sess = _DB_SESSION_HOLDER.get("session")
    if sess is not None:
        try:
            sess.commit()
        except Exception:
            sess.rollback()


def _clusters_payload(db: Session) -> list:
    rows = db.query(Cluster).all()
    if not rows:
        client = k8c.get_client()
        if client is None:
            raise HTTPException(503, "Kubernetes 客户端初始化失败")
        rows = [_seed_default_cluster(db, client)]
    out = []
    for row in rows:
        client = k8c.get_client()
        if client is not None:
            _DB_SESSION_HOLDER["session"] = db
            try:
                _sync_cluster_status(row, client)
            finally:
                _DB_SESSION_HOLDER.pop("session", None)
        out.append({
            "id": str(row.id), "name": row.name, "type": row.type,
            "api_mode": row.api_mode, "api_url": row.api_url or "",
            "version": row.version or "", "status": row.status,
            "default_namespace": row.default_namespace,
            "last_success_at": _iso(row.last_success_at), "last_error": row.last_error or "",
        })
    return out


@router.get("/clusters")
def list_clusters(db: Session = Depends(get_session)):
    return _clusters_payload(db)


# ── 解析工具 ─────────────────────────────────────────────────

def _node_roles(item: dict) -> list:
    labels = (item.get("metadata") or {}).get("labels") or {}
    return [k.split("/", 1)[1] for k in labels if k.startswith("node-role.kubernetes.io/") and labels[k] in ("", "true")]


def _node_conditions(item: dict) -> dict:
    out = {}
    for cond in (item.get("status") or {}).get("conditions") or []:
        out[cond.get("type")] = str(cond.get("status"))
    return out


def _fmt_cores(v: Optional[float]) -> str:
    if v is None:
        return ""
    return f"{v:.2f}"


def _fmt_bytes(v: Optional[float]) -> str:
    if v is None:
        return ""
    for unit, size in (("Ti", 1024.0 ** 4), ("Gi", 1024.0 ** 3), ("Mi", 1024.0 ** 2), ("Ki", 1024.0)):
        if v >= size:
            return f"{v / size:.1f}{unit}"
    return f"{v:.0f}B"


def _node_summary(item: dict, usage_map: Optional[dict]) -> dict:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    name = meta.get("name") or ""
    conds = _node_conditions(item)
    alloc = item.get("status", {}).get("allocatable") or {}
    cap = item.get("status", {}).get("capacity") or {}
    alloc_f = {k: alloc.get(k) for k in ("cpu", "memory", "pods") if alloc.get(k)}
    cap_f = {k: cap.get(k) for k in ("cpu", "memory", "pods") if cap.get(k)}
    usage = (usage_map or {}).get(name)
    pressure: dict = {}
    if usage and usage.get("cpu_cores") is not None and alloc.get("cpu"):
        cpu_alloc = k8c.parse_cpu(alloc.get("cpu"))
        if cpu_alloc:
            pressure["cpu"] = round(usage["cpu_cores"] / cpu_alloc * 100, 1)
    if usage and usage.get("memory_bytes") is not None and alloc.get("memory"):
        mem_alloc = k8c.parse_memory(alloc.get("memory"))
        if mem_alloc:
            pressure["memory"] = round(usage["memory_bytes"] / mem_alloc * 100, 1)
    return {
        "name": name,
        "roles": _node_roles(item),
        "ready": conds.get("Ready") == "True",
        "pressure": pressure,
        "conditions": conds,
        "taints": [
            {"key": t.get("key"), "effect": t.get("effect")}
            for t in (item.get("spec") or {}).get("taints") or []
        ],
        "allocatable": alloc_f,
        "capacity": cap_f,
        "usage": ({"cpu": _fmt_cores(usage.get("cpu_cores")), "memory": _fmt_bytes(usage.get("memory_bytes"))}
                  if usage else None),
    }


def _usage_map(client, errors: list) -> Optional[dict]:
    try:
        items = _unwrap(client.node_metrics())
    except k8c.K8sError as exc:
        if exc.status == 404:
            errors.append("metrics-server 不可用（未安装或 404）")
            source_status_note("metrics", "unavailable")
        else:
            errors.append(f"指标源采集失败：{exc}")
            source_status_note("metrics", "error")
        return None
    except Exception as exc:
        errors.append(f"指标源采集失败：{k8c.sanitize_error(exc)}")
        source_status_note("metrics", "error")
        return None
    out = {}
    for item in items:
        name = ((item.get("metadata") or {}).get("name")) or ""
        usage = (item.get("usage") or {})
        out[name] = {"cpu_cores": k8c.parse_cpu(usage.get("cpu")), "memory_bytes": k8c.parse_memory(usage.get("memory"))}
    source_status_note("metrics", "ok")
    return out


def _pod_phase(item: dict) -> str:
    phase = (item.get("status") or {}).get("phase") or "Unknown"
    if phase == "Running" and (item.get("metadata", {}).get("deletionTimestamp")):
        return "Terminating"
    return phase


def _pod_restarts(item: dict) -> int:
    total = 0
    for cs in (item.get("status") or {}).get("containerStatuses") or []:
        total += int(cs.get("restartCount") or 0)
    return total


def _ready_str(item: dict) -> str:
    statuses = (item.get("status") or {}).get("containerStatuses") or []
    spec_c = ((item.get("spec") or {}).get("containers")) or []
    ready = sum(1 for cs in statuses if cs.get("ready"))
    total = len(spec_c) or len(statuses) or 1
    return f"{ready}/{total}"


def _pod_qos(item: dict) -> str:
    return (item.get("status") or {}).get("qosClass") or ""


def _pod_row(item: dict) -> dict:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    containers = []
    for cs in status.get("containerStatuses") or []:
        state = ""
        st = cs.get("state") or {}
        for k in ("running", "waiting", "terminated"):
            if k in st:
                state = k
                break
        containers.append({"name": cs.get("name"), "ready": bool(cs.get("ready")),
                           "restarts": int(cs.get("restartCount") or 0), "state": state})
    return {
        "name": meta.get("name"), "namespace": meta.get("namespace"),
        "phase": _pod_phase(item), "ready": _ready_str(item),
        "restarts": _pod_restarts(item),
        "pod_ip": (status.get("podIP") or ""), "node": (status.get("phase") and (item.get("spec") or {}).get("nodeName")) or (item.get("spec") or {}).get("nodeName") or "",
        "qos": _pod_qos(item),
        "age": _k_time(meta.get("creationTimestamp")),
        "created_at": _k_time(meta.get("creationTimestamp")),
        "containers": containers,
    }


def _match_selector(pod: dict, match_labels: dict) -> bool:
    labels = (pod.get("metadata") or {}).get("labels") or {}
    return all(labels.get(k) == v for k, v in (match_labels or {}).items())


def _workload_row(kind: str, item: dict, pods: list) -> dict:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    spec = item.get("spec") or {}
    selector = ((spec.get("selector") or {}).get("matchLabels")) or {}
    related = [p for p in pods if p.get("metadata", {}).get("namespace") == meta.get("namespace") and _match_selector(p, selector)]
    restarts = sum(_pod_restarts(p) for p in related)
    images = []
    for c in ((spec.get("template") or {}).get("spec") or {}).get("containers") or []:
        if c.get("image"):
            images.append(c["image"])
    desired = int(status.get("replicas") if status.get("replicas") is not None else spec.get("replicas") or 0)
    ready_n = int(status.get("readyReplicas") or 0)
    return {
        "name": meta.get("name"), "type": kind, "namespace": meta.get("namespace"),
        "replicas": {
            "desired": desired, "ready": ready_n,
            "updated": int(status.get("updatedReplicas") or 0),
            "available": int(status.get("availableReplicas") or 0),
        },
        "images": images, "restarts": restarts,
        "age": _k_time(meta.get("creationTimestamp")),
        "created_at": _k_time(meta.get("creationTimestamp")),
        "pods_count": len(related),
    }


def _pod_usage(client, namespace: str, pod_name: str, errors: list) -> Optional[dict]:
    """metrics.k8s.io 的单 Pod 容器用量（尽力而为，失败返回 None 不伪造 0）。"""
    try:
        items = client.pod_metrics(namespace)
    except k8c.K8sError as exc:
        if exc.status == 404:
            source_status_note("metrics", "unavailable")
        else:
            errors.append(f"Pod 指标采集失败：{exc}")
        return None
    except Exception as exc:
        errors.append(f"Pod 指标采集失败：{k8c.sanitize_error(exc)}")
        return None
    for it in items:
        meta = it.get("metadata") or {}
        if meta.get("name") == pod_name and meta.get("namespace") == namespace:
            out = {}
            for c in it.get("containers") or []:
                out[c.get("name")] = {"cpu": c.get("usage", {}).get("cpu"), "memory": c.get("usage", {}).get("memory")}
            return out
    return None


def _owner_top(item: dict, rs_index: dict) -> dict:
    refs = (item.get("metadata") or {}).get("ownerReferences") or []
    if not refs:
        return {"kind": "Pod", "name": (item.get("metadata") or {}).get("name")}
    ref = refs[0]
    if ref.get("kind") == "ReplicaSet" and ref.get("name") in rs_index:
        top = rs_index[ref["name"]]
        return {"kind": top.get("kind"), "name": top.get("name")}
    return {"kind": ref.get("kind"), "name": ref.get("name")}


def _pod_detail(item: dict, pod_usage: Optional[dict], rs_index: dict, events: list) -> dict:
    meta = item.get("metadata") or {}
    status = item.get("status") or {}
    spec = item.get("spec") or {}
    containers, init_containers = [], []
    cs_by_name = {cs.get("name"): cs for cs in status.get("containerStatuses") or []}

    def _entry(container: dict, kind: str) -> dict:
        cs = cs_by_name.get(container.get("name")) or {}
        st = cs.get("state") or {}
        state = next((k for k in ("running", "waiting", "terminated") if k in st), "")
        probes = {}
        for p in ("readinessProbe", "livenessProbe", "startupProbe"):
            if container.get(p):
                probes["readiness" if p == "readinessProbe" else "liveness" if p == "livenessProbe" else "startup"] = "configured"
        res = {"requests": container.get("resources", {}).get("requests") or {},
               "limits": container.get("resources", {}).get("limits") or {}}
        u = (pod_usage or {}).get(container.get("name"))
        return {
            "name": container.get("name"), "kind": kind,
            "ready": bool(cs.get("ready")), "restarts": int(cs.get("restartCount") or 0),
            "state": state, "state_detail": (st.get("waiting") or st.get("terminated") or {}).get("reason", ""),
            "probes": probes, "resources": res,
            "usage": ({"cpu": _fmt_cores(k8c.parse_cpu(u.get("cpu"))), "memory": _fmt_bytes(k8c.parse_memory(u.get("memory")))}
                      if u else None),
            "image": container.get("image") or "",
        }

    for c in spec.get("containers") or []:
        containers.append(_entry(c, "main"))
    for c in spec.get("initContainers") or []:
        init_containers.append(_entry(c, "init"))
    return {
        "name": meta.get("name"), "namespace": meta.get("namespace"),
        "phase": _pod_phase(item), "qos": _pod_qos(item),
        "node": spec.get("nodeName") or "", "pod_ip": status.get("podIP") or "",
        "created_at": _k_time(meta.get("creationTimestamp")),
        "containers": containers, "init_containers": init_containers,
        "owner": _owner_top(item, rs_index),
        "events": events,
    }


def _service_row(item: dict, endpoints_index: dict) -> dict:
    meta = item.get("metadata") or {}
    spec = item.get("spec") or {}
    ports = []
    for p in spec.get("ports") or []:
        ports.append({"port": p.get("port"), "node_port": p.get("nodePort"),
                      "target_port": p.get("targetPort"), "protocol": p.get("protocol") or "TCP"})
    ep = endpoints_index.get((meta.get("namespace"), meta.get("name")))
    ready_eps = 0
    if ep:
        for sub in (ep.get("subsets") or []):
            for addr in sub.get("addresses") or []:
                ready_eps += 1
    return {
        "name": meta.get("name"), "namespace": meta.get("namespace"),
        "type": spec.get("type") or "ClusterIP",
        "cluster_ip": spec.get("clusterIP") or "",
        "external_ip": ",".join(spec.get("externalIPs") or []) or (spec.get("externalName") or ""),
        "ports": ports, "endpoints_ready": ready_eps, "exposed": ready_eps > 0,
        "age": _k_time(meta.get("creationTimestamp")),
    }


def _ingress_row(item: dict) -> dict:
    meta = item.get("metadata") or {}
    spec = item.get("spec") or {}
    rules = []
    for r in spec.get("rules") or []:
        http = r.get("http") or {}
        rules.append({"host": r.get("host") or "", "http": {"paths": http.get("paths") or []}})
    return {
        "name": meta.get("name"), "namespace": meta.get("namespace"),
        "hosts": [r.get("host") for r in rules if r.get("host")],
        "rules": rules,
        "tls": [{"hosts": t.get("hosts") or []} for t in spec.get("tls") or []],
        "age": _k_time(meta.get("creationTimestamp")),
    }


def _storage_payload(pvcs: list, pvs: list) -> dict:
    pvc_rows, counts = [], {"bound": 0, "pending": 0, "lost": 0}
    for it in pvcs:
        phase = (it.get("status") or {}).get("phase") or "Unknown"
        key = {"Bound": "bound", "Pending": "pending", "Lost": "lost"}.get(phase)
        if key:
            counts[key] += 1
        meta = it.get("metadata") or {}
        spec = it.get("spec") or {}
        pvc_rows.append({
            "name": meta.get("name"), "namespace": meta.get("namespace"), "phase": phase,
            "capacity": ((it.get("status") or {}).get("capacity") or {}).get("storage") or "",
            "storage_class": spec.get("storageClassName") or "",
            "volume_name": spec.get("volumeName") or "",
            "age": _k_time(meta.get("creationTimestamp")),
        })
    pv_rows, pv_counts = [], {"available": 0, "bound": 0, "released": 0, "failed": 0}
    for it in pvs:
        meta = it.get("metadata") or {}
        status = it.get("status") or {}
        spec = it.get("spec") or {}
        phase = status.get("phase") or "Unknown"
        if phase in pv_counts:
            pv_counts[phase.lower()] += 1
        claim = (spec.get("claimRef") or {})
        pv_rows.append({
            "name": meta.get("name"), "phase": phase,
            "reclaim_policy": spec.get("persistentVolumeReclaimPolicy") or "",
            "capacity": (spec.get("capacity") or {}).get("storage") or "",
            "storage_class": spec.get("storageClassName") or "",
            "claim": f"{claim.get('namespace', '')}/{claim.get('name', '')}" if claim.get("name") else "",
            "age": _k_time(meta.get("creationTimestamp")),
        })
    return {"pvcs": pvc_rows, "pvs": pv_rows,
            "pvc": counts, "pv": pv_counts}


def _jobs_payload(jobs: list, cronjobs: list) -> dict:
    job_rows = []
    for it in jobs:
        meta = it.get("metadata") or {}
        status = it.get("status") or {}
        job_rows.append({
            "name": meta.get("name"), "namespace": meta.get("namespace"),
            "succeeded": int(status.get("succeeded") or 0),
            "failed": int(status.get("failed") or 0),
            "active": int(status.get("active") or 0),
            "completion_time": _k_time(status.get("completionTime")),
            "start_time": _k_time(status.get("startTime")),
            "age": _k_time(meta.get("creationTimestamp")),
        })
    cron_rows = []
    for it in cronjobs:
        meta = it.get("metadata") or {}
        spec = it.get("spec") or {}
        status = it.get("status") or {}
        cron_rows.append({
            "name": meta.get("name"), "namespace": meta.get("namespace"),
            "schedule": spec.get("schedule") or "", "suspend": bool(spec.get("suspend")),
            "last_schedule_time": _k_time(status.get("lastScheduleTime")),
            "active": len(status.get("active") or []),
        })
    return {
        "jobs": job_rows, "cronjobs": cron_rows,
        "recent_success": sum(r["succeeded"] for r in job_rows),
        "recent_failed": sum(r["failed"] for r in job_rows),
    }


def _event_row(item: dict) -> dict:
    meta = item.get("metadata") or {}
    involved = item.get("involvedObject") or {}
    return {
        "namespace": involved.get("namespace") or meta.get("namespace") or "",
        "object": f"{involved.get('kind')}/{involved.get('name')}",
        "reason": item.get("reason") or "", "message": (item.get("message") or "")[:300],
        "count": int(item.get("count") or 1), "type": item.get("type") or "",
        "first_at": _k_time(item.get("firstTimestamp")), "last_at": _k_time(item.get("lastTimestamp")),
    }


def _netpol_payload(items: list) -> dict:
    rows = []
    covered = set()
    namespaces = set()
    for it in items:
        meta = it.get("metadata") or {}
        ns = meta.get("namespace") or ""
        if ns:
            namespaces.add(ns)
            covered.add(ns)
        spec = it.get("spec") or {}
        types = spec.get("policyTypes") or (["Ingress"] if spec.get("ingress") else []) + (["Egress"] if spec.get("egress") else [])
        rows.append({
            "name": meta.get("name"), "namespace": ns,
            "types": list(dict.fromkeys(types)),
            "pod_selector": str((spec.get("podSelector") or {}).get("matchLabels") or {}),
            "age": _k_time(meta.get("creationTimestamp")),
        })
    return {"items": rows, "total": len(rows), "covered": len(covered), "namespaces": len(namespaces)}


# ── 概览聚合 ─────────────────────────────────────────────────

def _collect_summary(client) -> tuple[dict, list, dict]:
    errors: list = []
    results: dict = {}

    def run(name, fn):
        try:
            results[name] = _unwrap(fn())
            source_status_note(name, "ok")
        except Exception as exc:
            errors.append(f"{name} 采集失败：{k8c.sanitize_error(exc)}")
            source_status_note(name, "error")
            results[name] = []

    with ThreadPoolExecutor(max_workers=max(2, min(6, k8c.K8S_MAX_CONCURRENCY))) as pool:
        futures = {
            pool.submit(run, "nodes", lambda: client.list_nodes()): "nodes",
            pool.submit(run, "namespaces", lambda: client.list_namespaces()): "namespaces",
            pool.submit(run, "deployments", lambda: client.list_workloads("deployment")): "deployments",
            pool.submit(run, "statefulsets", lambda: client.list_workloads("statefulset")): "statefulsets",
            pool.submit(run, "daemonsets", lambda: client.list_workloads("daemonset")): "daemonsets",
            pool.submit(run, "pods", lambda: client.list_pods()): "pods",
            pool.submit(run, "services", lambda: client.list_services()): "services",
            pool.submit(run, "endpoints", lambda: client.list_endpoints()): "endpoints",
            pool.submit(run, "ingresses", lambda: client.list_ingresses()): "ingresses",
            pool.submit(run, "pvcs", lambda: client.list_pvcs()): "pvcs",
            pool.submit(run, "pvs", lambda: client.list_pv()): "pvs",
            pool.submit(run, "jobs", lambda: client.list_jobs()): "jobs",
            pool.submit(run, "cronjobs", lambda: client.list_cronjobs()): "cronjobs",
            pool.submit(run, "events", lambda: client.list_events(field_selector="type=Warning")): "events",
            pool.submit(run, "netpols", lambda: client.list_network_policies()): "netpols",
        }
        for f in as_completed(futures):
            f.result()
    usage_map = _usage_map(client, errors)

    pods = results.get("pods") or []
    phases = {"running": 0, "pending": 0, "failed": 0, "unknown": 0}
    restarts = []
    for p in pods:
        phase = _pod_phase(p)
        if phase == "Running":
            phases["running"] += 1
        elif phase == "Pending":
            phases["pending"] += 1
        elif phase == "Failed":
            phases["failed"] += 1
        elif phase == "Succeeded":
            pass
        else:
            phases["unknown"] += 1
        r = _pod_restarts(p)
        if r > 0:
            meta = p.get("metadata") or {}
            restarts.append({"name": meta.get("name"), "namespace": meta.get("namespace"), "restarts": r})
    restarts.sort(key=lambda x: x["restarts"], reverse=True)

    def wl(kind_key, items):
        desired = sum(int((it.get("status") or {}).get("replicas") or 0) for it in items)
        ready = sum(int((it.get("status") or {}).get("readyReplicas") or 0) for it in items)
        return {"desired": desired, "ready": ready}

    nodes = results.get("nodes") or []
    ready_nodes = sum(1 for n in nodes if _node_conditions(n).get("Ready") == "True")
    roles = sorted({r for n in nodes for r in _node_roles(n)})
    endpoints_index = {}
    for ep in results.get("endpoints") or []:
        meta = ep.get("metadata") or {}
        endpoints_index[(meta.get("namespace"), meta.get("name"))] = ep
    svc_rows = [_service_row(it, endpoints_index) for it in results.get("services") or []]
    events = [_event_row(it) for it in results.get("events") or []]
    events = [e for e in events if (e.get("type") or "").lower() == "warning"]
    events.sort(key=lambda e: e.get("last_at") or "", reverse=True)
    netpol = _netpol_payload(results.get("netpols") or [])
    storage = _storage_payload(results.get("pvcs") or [], results.get("pvs") or [])
    jobs = _jobs_payload(results.get("jobs") or [], results.get("cronjobs") or [])

    version = ""
    try:
        version = str((client.version() or {}).get("gitVersion") or "")
    except Exception:
        pass

    summary = {
        "cluster": {"version": version},
        "nodes": {"total": len(nodes), "ready": ready_nodes, "roles": roles},
        "namespaces": {"count": len(results.get("namespaces") or [])},
        "workloads": {
            "deployments": wl("deployment", results.get("deployments") or []),
            "statefulsets": wl("statefulset", results.get("statefulsets") or []),
            "daemonsets": wl("daemonset", results.get("daemonsets") or []),
        },
        "pods": {**phases, "total": len(pods), "restart_top": restarts[:5]},
        "services": {"count": len(svc_rows), "exposed": sum(1 for s in svc_rows if s["exposed"])},
        "ingresses": {"count": len(results.get("ingresses") or [])},
        "storage": {"pvc": storage.get("pvc") or {}, "pv": storage.get("pv") or {}},
        "jobs": {"recent_success": jobs["recent_success"], "recent_failed": jobs["recent_failed"]},
        "cronjobs": jobs.get("cronjobs") or [],
        "events": {"warning_count": len(events), "recent": events[:5]},
        "netpol": {"covered": netpol.get("covered", 0), "total": netpol.get("total", 0),
                   "namespaces": netpol.get("namespaces", 0)},
    }
    # 更新节点缓存（供 server_details 接缝）
    with _CACHE_LOCK:
        _NODES["ts"] = _now_ts()
        _NODES["items"] = [_node_summary(n, usage_map) for n in nodes]
    return summary, errors, {"nodes": usage_map}


def _cached_nodes_refresh(client, errors: list) -> None:
    """节点快照缓存刷新（供 /nodes 端点与 server_details 接缝共用）。"""
    try:
        nodes = _unwrap(client.list_nodes())
    except Exception as exc:
        errors.append(f"节点采集失败：{k8c.sanitize_error(exc)}")
        return
    usage_map = _usage_map(client, errors)
    with _CACHE_LOCK:
        _NODES["ts"] = _now_ts()
        _NODES["items"] = [_node_summary(n, usage_map) for n in nodes]


# ── 通用端点实现 ─────────────────────────────────────────────

def _cached_view(key: str, ttl: float, builder) -> dict:
    hit = _cache_get(key, ttl)
    if hit:
        return hit
    payload = builder()
    _cache_put(key, payload)
    return payload


@router.get("/clusters/{cluster_id}/summary")
def cluster_summary(cluster_id: str, db: Session = Depends(get_session)):
    _load_cluster(db, cluster_id)
    client = _get_client()

    def build():
        summary, errors, _src = _collect_summary(client)
        return _envelope(summary, partial_errors=errors, source_status=_collect_source_status())

    return _cached_view(f"summary:{cluster_id}", K8S_CACHE_TTL, build)


@router.get("/clusters/{cluster_id}/nodes")
def cluster_nodes(cluster_id: str, db: Session = Depends(get_session)):
    _load_cluster(db, cluster_id)
    client = _get_client()

    def build():
        errors: list = []
        try:
            nodes = _unwrap(client.list_nodes())
            source_status_note("nodes", "ok")
        except Exception as exc:
            errors.append(f"节点采集失败：{k8c.sanitize_error(exc)}")
            source_status_note("nodes", "error")
            nodes = []
        usage_map = _usage_map(client, errors)
        rows = [_node_summary(n, usage_map) for n in nodes]
        with _CACHE_LOCK:
            _NODES["ts"] = _now_ts()
            _NODES["items"] = rows
        return _envelope(rows, partial_errors=errors, source_status=_collect_source_status())

    return _cached_view(f"nodes:{cluster_id}", _LIST_TTL, build)


@router.get("/clusters/{cluster_id}/namespaces")
def cluster_namespaces(cluster_id: str, db: Session = Depends(get_session)):
    _load_cluster(db, cluster_id)
    client = _get_client()

    def build():
        errors: list = []
        items = _list_items(client, "list_namespaces", errors=errors, source="namespaces")
        rows = [{
            "name": (it.get("metadata") or {}).get("name"),
            "phase": (it.get("status") or {}).get("phase") or "",
            "labels": list(((it.get("metadata") or {}).get("labels") or {}).keys())[:8],
            "age": _k_time((it.get("metadata") or {}).get("creationTimestamp")),
        } for it in items]
        return _envelope(rows, partial_errors=errors, source_status=_collect_source_status())

    return _cached_view(f"ns:{cluster_id}", _LIST_TTL, build)


@router.get("/clusters/{cluster_id}/workloads")
def cluster_workloads(
    cluster_id: str,
    namespace: Optional[str] = Query(None),
    type: Optional[str] = Query(None, alias="type"),
    status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_session),
):
    _load_cluster(db, cluster_id)
    client = _get_client()

    def build():
        errors: list = []
        kinds = []
        t = (type or "all").lower()
        if t in ("all", ""):
            kinds = ["deployment", "statefulset", "daemonset"]
        elif t in ("deployment", "statefulset", "daemonset"):
            kinds = [t]
        else:
            raise HTTPException(422, "type 仅支持 deployment/statefulset/daemonset/all")
        with ThreadPoolExecutor(max_workers=max(2, len(kinds) + 1)) as pool:
            futs = {pool.submit(_list_items, client, "list_workloads", k, namespace, errors=errors, source=k): k for k in kinds}
            pods_fut = pool.submit(_list_items, client, "list_pods", namespace, errors=errors, source="pods")
            by_kind = {futs[f]: f.result() for f in as_completed(futs)}
            pods = pods_fut.result()
        rows = []
        for k in kinds:
            for it in by_kind.get(k, []):
                rows.append(_workload_row(k, it, pods))
        if status == "ready":
            rows = [r for r in rows if r["replicas"]["ready"] >= r["replicas"]["desired"]]
        elif status == "not_ready":
            rows = [r for r in rows if r["replicas"]["ready"] < r["replicas"]["desired"]]
        if keyword:
            kw = keyword.lower()
            rows = [r for r in rows if kw in (r["name"] or "").lower() or kw in (r["namespace"] or "").lower()
                    or any(kw in (img or "").lower() for img in r["images"])]
        rows.sort(key=lambda r: (r["namespace"] or "", r["name"] or ""))
        return _envelope(rows[:limit], partial_errors=errors, source_status=_collect_source_status())

    return _cached_view(f"wl:{cluster_id}:{namespace}:{type}:{status}:{keyword}:{limit}", _LIST_TTL, build)


@router.get("/clusters/{cluster_id}/pods")
def cluster_pods(
    cluster_id: str,
    namespace: Optional[str] = Query(None),
    node: Optional[str] = Query(None),
    phase: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_session),
):
    _load_cluster(db, cluster_id)
    client = _get_client()

    def build():
        errors: list = []
        items = _list_items(client, "list_pods", namespace, errors=errors, source="pods")
        rows = [_pod_row(it) for it in items]
        if node:
            rows = [r for r in rows if r["node"] == node]
        if phase:
            rows = [r for r in rows if r["phase"].lower() == phase.lower()]
        if keyword:
            kw = keyword.lower()
            rows = [r for r in rows if kw in (r["name"] or "").lower() or kw in (r["namespace"] or "").lower()]
        rows.sort(key=lambda r: (r["namespace"] or "", r["name"] or ""))
        return _envelope(rows[:limit], partial_errors=errors, source_status=_collect_source_status())

    return _cached_view(f"pods:{cluster_id}:{namespace}:{node}:{phase}:{keyword}:{limit}", _LIST_TTL, build)


@router.get("/clusters/{cluster_id}/pods/{namespace}/{name}")
def cluster_pod_detail(cluster_id: str, namespace: str, name: str, db: Session = Depends(get_session)):
    if not k8c.valid_k8s_name(namespace) or not k8c.valid_k8s_name(name):
        raise HTTPException(422, "非法 namespace/pod 名称")
    _load_cluster(db, cluster_id)
    client = _get_client()
    try:
        item = client.get_pod(namespace, name)
    except k8c.K8sError as exc:
        raise HTTPException(exc.status or 502, exc.args[0] if exc.args else "K8s API 错误")
    errors: list = []
    pod_usage = _pod_usage(client, namespace, name, errors)
    # owner 归属：pod → ReplicaSet → Deployment（一次反向列表推导）
    rs_index: dict = {}
    try:
        refs = (item.get("metadata") or {}).get("ownerReferences") or []
        if refs and refs[0].get("kind") == "ReplicaSet":
            rs_name = refs[0].get("name")
            rss = client._items(client.get_json(
                f"/apis/apps/v1/namespaces/{namespace}/replicasets?fieldSelector=metadata.name={rs_name}"))
            for rs in rss:
                top_refs = (rs.get("metadata") or {}).get("ownerReferences") or []
                if top_refs:
                    rs_index[rs_name] = {"kind": top_refs[0].get("kind"), "name": top_refs[0].get("name")}
    except Exception:
        pass
    ev_rows: list = []
    try:
        ev_rows = [_event_row(e) for e in _unwrap(client.list_events(
            namespace, field_selector=f"involvedObject.name={name},involvedObject.namespace={namespace}"))][:20]
    except Exception:
        pass
    detail = _pod_detail(item, pod_usage, rs_index, ev_rows)
    return _envelope(detail, partial_errors=errors, source_status=_collect_source_status())


@router.get("/clusters/{cluster_id}/pods/{namespace}/{name}/logs")
def cluster_pod_logs(
    cluster_id: str, namespace: str, name: str,
    container: Optional[str] = Query(None),
    tail_lines: int = Query(300, ge=1, le=1000),
    since_seconds: Optional[int] = Query(None, ge=1, le=3600),
    db: Session = Depends(get_session),
):
    if not k8c.valid_k8s_name(namespace) or not k8c.valid_k8s_name(name):
        raise HTTPException(422, "非法 namespace/pod 名称")
    if container and not k8c.valid_k8s_name(container):
        raise HTTPException(422, "非法 container 名称")
    _load_cluster(db, cluster_id)
    client = _get_client()
    try:
        text = client.pod_log(namespace, name, container=container, tail_lines=tail_lines, since_seconds=since_seconds)
    except k8c.K8sError as exc:
        raise HTTPException(exc.status or 502, exc.args[0] if exc.args else "日志获取失败")
    max_bytes = 512 * 1024
    encoded = text.encode("utf-8", errors="replace")
    truncated = len(encoded) > max_bytes
    if truncated:
        text = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return _envelope(text, source_status=_collect_source_status()) | {"truncated": truncated}


@router.get("/clusters/{cluster_id}/services")
def cluster_services(cluster_id: str, db: Session = Depends(get_session)):
    _load_cluster(db, cluster_id)
    client = _get_client()

    def build():
        errors: list = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_svcs = pool.submit(_list_items, client, "list_services", None, errors=errors, source="services")
            f_eps = pool.submit(_list_items, client, "list_endpoints", None, errors=errors, source="endpoints")
            svcs, eps = f_svcs.result(), f_eps.result()
        index = {}
        for ep in eps:
            meta = ep.get("metadata") or {}
            index[(meta.get("namespace"), meta.get("name"))] = ep
        rows = [_service_row(it, index) for it in svcs]
        rows.sort(key=lambda r: (r["namespace"] or "", r["name"] or ""))
        return _envelope(rows, partial_errors=errors, source_status=_collect_source_status())

    return _cached_view(f"svc:{cluster_id}", _LIST_TTL, build)


@router.get("/clusters/{cluster_id}/ingresses")
def cluster_ingresses(cluster_id: str, db: Session = Depends(get_session)):
    _load_cluster(db, cluster_id)
    client = _get_client()

    def build():
        errors: list = []
        items = _list_items(client, "list_ingresses", None, errors=errors, source="ingresses")
        rows = [_ingress_row(it) for it in items]
        rows.sort(key=lambda r: (r["namespace"] or "", r["name"] or ""))
        return _envelope(rows, partial_errors=errors, source_status=_collect_source_status())

    return _cached_view(f"ing:{cluster_id}", _LIST_TTL, build)


@router.get("/clusters/{cluster_id}/storage")
def cluster_storage(cluster_id: str, db: Session = Depends(get_session)):
    _load_cluster(db, cluster_id)
    client = _get_client()

    def build():
        errors: list = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_pvc = pool.submit(_list_items, client, "list_pvcs", None, errors=errors, source="pvcs")
            f_pv = pool.submit(_list_items, client, "list_pv", errors=errors, source="pvs")
            pvcs, pvs = f_pvc.result(), f_pv.result()
        payload = _storage_payload(pvcs, pvs)
        return _envelope({"pvcs": payload["pvcs"], "pvs": payload["pvs"],
                          "pvc": payload["pvc"], "pv": payload["pv"]},
                         partial_errors=errors, source_status=_collect_source_status())

    return _cached_view(f"sto:{cluster_id}", _LIST_TTL, build)


@router.get("/clusters/{cluster_id}/jobs")
def cluster_jobs(cluster_id: str, db: Session = Depends(get_session)):
    _load_cluster(db, cluster_id)
    client = _get_client()

    def build():
        errors: list = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_j = pool.submit(_list_items, client, "list_jobs", None, errors=errors, source="jobs")
            f_c = pool.submit(_list_items, client, "list_cronjobs", None, errors=errors, source="cronjobs")
            jobs, cronjobs = f_j.result(), f_c.result()
        payload = _jobs_payload(jobs, cronjobs)
        return _envelope(payload, partial_errors=errors, source_status=_collect_source_status())

    return _cached_view(f"jobs:{cluster_id}", _LIST_TTL, build)


@router.get("/clusters/{cluster_id}/events")
def cluster_events(
    cluster_id: str,
    namespace: Optional[str] = Query(None),
    type: Optional[str] = Query("Warning", alias="type"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_session),
):
    _load_cluster(db, cluster_id)
    client = _get_client()

    def build():
        errors: list = []
        t = (type or "").lower()
        field = None
        if t == "warning":
            field = "type=Warning"
        elif t == "normal":
            field = "type=Normal"
        elif t in ("", "all", "any"):
            field = None
        else:
            raise HTTPException(422, "type 仅支持 Warning/Normal/all")
        items = _list_items(client, "list_events", namespace, field, errors=errors, source="events")
        rows = [_event_row(it) for it in items]
        if t == "warning":
            rows = [r for r in rows if (r["type"] or "").lower() == "warning"]
        elif t == "normal":
            rows = [r for r in rows if (r["type"] or "").lower() != "warning"]
        rows.sort(key=lambda r: r.get("last_at") or "", reverse=True)
        return _envelope(rows[:limit], partial_errors=errors, source_status=_collect_source_status())

    return _cached_view(f"ev:{cluster_id}:{namespace}:{type}:{limit}", _LIST_TTL, build)


@router.get("/clusters/{cluster_id}/network-policies")
def cluster_netpol(cluster_id: str, db: Session = Depends(get_session)):
    _load_cluster(db, cluster_id)
    client = _get_client()

    def build():
        errors: list = []
        items = _list_items(client, "list_network_policies", None, errors=errors, source="networkpolicies")
        payload = _netpol_payload(items)
        return _envelope(payload, partial_errors=errors, source_status=_collect_source_status())

    return _cached_view(f"np:{cluster_id}", _LIST_TTL, build)


# ── server_details.py 接缝（BE-2 已按 getattr 探测复用） ──────

def get_cached_nodes_snapshot() -> Optional[list]:
    with _CACHE_LOCK:
        if _NODES["items"]:
            return list(_NODES["items"])
    return None


def get_cached_node(node_name: str) -> Optional[dict]:
    with _CACHE_LOCK:
        for row in _NODES["items"]:
            if row.get("name") == node_name:
                return row
    return None
