from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException

def _query_range(start: str, end: str, resolution: str, metrics: str) -> dict:
    try:
        start_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_at = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(400, "时间范围无效") from error
    if start_at.tzinfo is None or end_at.tzinfo is None or start_at >= end_at:
        raise HTTPException(400, "时间范围无效")
    if resolution not in {"auto", "raw", "5m", "1h"}:
        raise HTTPException(400, "分辨率无效")
    allowed = {"cpu", "memory", "disk", "load1", "net_rx", "net_tx"}
    selected = list(dict.fromkeys(item for item in metrics.split(",") if item))
    if not selected or any(item not in allowed for item in selected):
        raise HTTPException(400, "监控指标无效")
    return {"start": start, "end": end, "resolution": resolution, "metrics": ",".join(selected)}
