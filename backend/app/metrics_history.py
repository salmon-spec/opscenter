"""Long-term metric rollups and time-range query API."""
from __future__ import annotations

import asyncio
import calendar
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, func

from app.config import RETENTION_ROLLUP_1H_DAYS, RETENTION_ROLLUP_5M_DAYS
from app.database import get_db
from app.models import MetricHistory, MetricRollup, Server


router = APIRouter(prefix="/api/v2", tags=["metric-history"])
ALLOWED_METRICS = {"cpu", "memory", "disk", "swap", "load1", "load5", "load15", "net_rx", "net_tx", "disk_read", "disk_write"}
_RESOLUTION_SECONDS = {"5m": 300, "1h": 3600}


def _floor(value: datetime, seconds: int) -> datetime:
    stamp = calendar.timegm(value.utctimetuple())
    return datetime.fromtimestamp(stamp - stamp % seconds, timezone.utc).replace(tzinfo=None)


def _upsert_buckets(db, rows: list, resolution: str, bucket_seconds: int, now: datetime) -> int:
    complete_before = _floor(now, bucket_seconds)
    groups = defaultdict(list)
    for row in rows:
        timestamp = row.timestamp if hasattr(row, "timestamp") else row.bucket_at
        if timestamp >= complete_before or row.metric.endswith("_raw"):
            continue
        bucket = _floor(timestamp, bucket_seconds)
        if hasattr(row, "value"):
            groups[(row.server_id, row.metric, bucket)].append((float(row.value), float(row.value), float(row.value), 1))
        else:
            groups[(row.server_id, row.metric, bucket)].append((float(row.value_avg), float(row.value_min), float(row.value_max), int(row.sample_count)))
    changed = 0
    for (server_id, metric, bucket), samples in groups.items():
        total_count = sum(item[3] for item in samples)
        average = sum(item[0] * item[3] for item in samples) / total_count
        values = {"value_avg": average, "value_min": min(item[1] for item in samples), "value_max": max(item[2] for item in samples), "sample_count": total_count}
        existing = db.query(MetricRollup).filter(
            MetricRollup.server_id == server_id, MetricRollup.metric == metric,
            MetricRollup.resolution == resolution, MetricRollup.bucket_at == bucket,
        ).first()
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            db.add(MetricRollup(server_id=server_id, metric=metric, resolution=resolution, bucket_at=bucket, **values))
        changed += 1
    return changed


def build_metric_rollups(now: datetime | None = None, raw_lookback_hours: int = 24) -> dict:
    """Build recent complete buckets idempotently, covering ordinary downtime."""
    now = now or datetime.utcnow()
    with get_db() as db:
        raw_rows = db.query(MetricHistory).filter(MetricHistory.timestamp >= now - timedelta(hours=raw_lookback_hours)).all()
        five_minute = _upsert_buckets(db, raw_rows, "5m", 300, now)
        db.flush()
        rollup_rows = db.query(MetricRollup).filter(
            MetricRollup.resolution == "5m", MetricRollup.bucket_at >= now - timedelta(hours=max(48, raw_lookback_hours)),
        ).all()
        hourly = _upsert_buckets(db, rollup_rows, "1h", 3600, now)
        if RETENTION_ROLLUP_5M_DAYS > 0:
            db.query(MetricRollup).filter(
                MetricRollup.resolution == "5m", MetricRollup.bucket_at < now - timedelta(days=RETENTION_ROLLUP_5M_DAYS),
            ).delete(synchronize_session=False)
        if RETENTION_ROLLUP_1H_DAYS > 0:
            db.query(MetricRollup).filter(
                MetricRollup.resolution == "1h", MetricRollup.bucket_at < now - timedelta(days=RETENTION_ROLLUP_1H_DAYS),
            ).delete(synchronize_session=False)
        db.commit()
    return {"five_minute": five_minute, "hourly": hourly}


async def metric_rollup_loop() -> None:
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.to_thread(build_metric_rollups)
        except Exception as exc:
            print(f"Metric rollup error: {exc}", flush=True)
        await asyncio.sleep(300)


def _parse_time(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        raise HTTPException(400, "时间格式无效，请使用 ISO 8601")


def _auto_resolution(start: datetime, end: datetime) -> str:
    span = end - start
    if span <= timedelta(hours=24):
        return "raw"
    if span <= timedelta(days=90):
        return "5m"
    return "1h"


def _thin(values: list, limit: int = 2500) -> list:
    if len(values) <= limit:
        return values
    step = max(1, len(values) // limit)
    return values[::step][:limit]


def _metric_query_args(metrics: str, start: str | None, end: str | None, resolution: str):
    end_dt = _parse_time(end, datetime.utcnow())
    start_dt = _parse_time(start, end_dt - timedelta(hours=1))
    if start_dt >= end_dt or end_dt - start_dt > timedelta(days=366 * 5):
        raise HTTPException(400, "时间范围必须在5年以内且开始时间早于结束时间")
    names = list(dict.fromkeys(item.strip() for item in metrics.split(",") if item.strip()))
    if not names or len(names) > 8 or any(item not in ALLOWED_METRICS for item in names):
        raise HTTPException(400, "监控指标无效或数量超过8个")
    selected_resolution = _auto_resolution(start_dt, end_dt) if resolution == "auto" else resolution
    return names, start_dt, end_dt, selected_resolution


@router.get("/servers/{server_id}/metrics/timeseries")
def metric_timeseries(
    server_id: str,
    metrics: str = Query("cpu,memory"),
    start: str | None = None,
    end: str | None = None,
    resolution: str = Query("auto", pattern="^(auto|raw|5m|1h)$"),
):
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(404, "主机不存在")
    names, start_dt, end_dt, selected_resolution = _metric_query_args(metrics, start, end, resolution)
    series = {name: [] for name in names}
    with get_db() as db:
        server = db.query(Server).filter(Server.id == server_uuid).first()
        if not server:
            raise HTTPException(404, "主机不存在")
        if selected_resolution == "raw":
            rows = db.query(MetricHistory).filter(
                MetricHistory.server_id == server_uuid, MetricHistory.metric.in_(names),
                MetricHistory.timestamp >= start_dt, MetricHistory.timestamp <= end_dt,
            ).order_by(MetricHistory.timestamp.asc()).all()
            for row in rows:
                series[row.metric].append([calendar.timegm(row.timestamp.utctimetuple()), row.value, row.value, row.value, 1])
        else:
            rows = db.query(MetricRollup).filter(
                MetricRollup.server_id == server_uuid, MetricRollup.metric.in_(names),
                MetricRollup.resolution == selected_resolution,
                MetricRollup.bucket_at >= start_dt, MetricRollup.bucket_at <= end_dt,
            ).order_by(MetricRollup.bucket_at.asc()).all()
            for row in rows:
                series[row.metric].append([calendar.timegm(row.bucket_at.utctimetuple()), row.value_avg, row.value_min, row.value_max, row.sample_count])
    series = {name: _thin(values) for name, values in series.items()}
    return {
        "server_id": server_id, "metrics": names, "resolution": selected_resolution,
        "start": start_dt.isoformat() + "Z", "end": end_dt.isoformat() + "Z",
        "series": series, "point_count": sum(len(values) for values in series.values()),
    }


@router.get("/metrics/hosts/overview")
def metric_hosts_overview(
    metrics: str = Query("cpu,memory,disk"),
    start: str | None = None,
    end: str | None = None,
    resolution: str = Query("auto", pattern="^(auto|raw|5m|1h)$"),
):
    """Return one aggregate row per host without making the browser query hosts one by one."""
    names, start_dt, end_dt, selected_resolution = _metric_query_args(metrics, start, end, resolution)
    with get_db() as db:
        servers = db.query(Server).order_by(Server.name.asc(), Server.host.asc()).all()
        if selected_resolution == "raw":
            aggregates = db.query(
                MetricHistory.server_id,
                MetricHistory.metric,
                func.avg(MetricHistory.value).label("average"),
                func.min(MetricHistory.value).label("minimum"),
                func.max(MetricHistory.value).label("maximum"),
                func.count(MetricHistory.id).label("samples"),
            ).filter(
                MetricHistory.metric.in_(names),
                MetricHistory.timestamp >= start_dt,
                MetricHistory.timestamp <= end_dt,
            ).group_by(MetricHistory.server_id, MetricHistory.metric).all()
            latest_times = db.query(
                MetricHistory.server_id.label("server_id"),
                MetricHistory.metric.label("metric"),
                func.max(MetricHistory.timestamp).label("last_at"),
            ).filter(
                MetricHistory.metric.in_(names),
                MetricHistory.timestamp >= start_dt,
                MetricHistory.timestamp <= end_dt,
            ).group_by(MetricHistory.server_id, MetricHistory.metric).subquery()
            latest = db.query(
                MetricHistory.server_id, MetricHistory.metric,
                MetricHistory.value.label("latest"), MetricHistory.timestamp.label("last_at"),
            ).join(latest_times, and_(
                MetricHistory.server_id == latest_times.c.server_id,
                MetricHistory.metric == latest_times.c.metric,
                MetricHistory.timestamp == latest_times.c.last_at,
            )).all()
        else:
            weighted_total = func.sum(MetricRollup.value_avg * MetricRollup.sample_count)
            sample_total = func.sum(MetricRollup.sample_count)
            aggregates = db.query(
                MetricRollup.server_id,
                MetricRollup.metric,
                (weighted_total / sample_total).label("average"),
                func.min(MetricRollup.value_min).label("minimum"),
                func.max(MetricRollup.value_max).label("maximum"),
                sample_total.label("samples"),
            ).filter(
                MetricRollup.metric.in_(names),
                MetricRollup.resolution == selected_resolution,
                MetricRollup.bucket_at >= start_dt,
                MetricRollup.bucket_at <= end_dt,
            ).group_by(MetricRollup.server_id, MetricRollup.metric).all()
            latest_times = db.query(
                MetricRollup.server_id.label("server_id"),
                MetricRollup.metric.label("metric"),
                func.max(MetricRollup.bucket_at).label("last_at"),
            ).filter(
                MetricRollup.metric.in_(names),
                MetricRollup.resolution == selected_resolution,
                MetricRollup.bucket_at >= start_dt,
                MetricRollup.bucket_at <= end_dt,
            ).group_by(MetricRollup.server_id, MetricRollup.metric).subquery()
            latest = db.query(
                MetricRollup.server_id, MetricRollup.metric,
                MetricRollup.value_avg.label("latest"), MetricRollup.bucket_at.label("last_at"),
            ).join(latest_times, and_(
                MetricRollup.server_id == latest_times.c.server_id,
                MetricRollup.metric == latest_times.c.metric,
                MetricRollup.bucket_at == latest_times.c.last_at,
            )).filter(MetricRollup.resolution == selected_resolution).all()

        host_map = {
            str(server.id): {
                "server_id": str(server.id), "name": server.name, "host": server.host,
                "status": server.status, "metrics": {},
            }
            for server in servers
        }
        latest_map = {(str(row.server_id), row.metric): row for row in latest}
        for row in aggregates:
            host = host_map.get(str(row.server_id))
            if not host:
                continue
            last = latest_map.get((str(row.server_id), row.metric))
            host["metrics"][row.metric] = {
                "average": round(float(row.average), 4),
                "minimum": round(float(row.minimum), 4),
                "maximum": round(float(row.maximum), 4),
                "latest": round(float(last.latest), 4) if last else None,
                "samples": int(row.samples or 0),
                "last_at": last.last_at.isoformat() + "Z" if last else None,
            }
    return {
        "metrics": names, "resolution": selected_resolution,
        "start": start_dt.isoformat() + "Z", "end": end_dt.isoformat() + "Z",
        "hosts": list(host_map.values()),
    }
