"""Long-term metric rollups and time-range query API."""
from __future__ import annotations

import asyncio
import calendar
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, HTTPException, Query

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
    end_dt = _parse_time(end, datetime.utcnow())
    start_dt = _parse_time(start, end_dt - timedelta(hours=1))
    if start_dt >= end_dt or end_dt - start_dt > timedelta(days=366 * 5):
        raise HTTPException(400, "时间范围必须在5年以内且开始时间早于结束时间")
    names = list(dict.fromkeys(item.strip() for item in metrics.split(",") if item.strip()))
    if not names or len(names) > 8 or any(item not in ALLOWED_METRICS for item in names):
        raise HTTPException(400, "监控指标无效或数量超过8个")
    selected_resolution = _auto_resolution(start_dt, end_dt) if resolution == "auto" else resolution
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
