"""统一“数据新鲜度”契约（计划 §8.3）。

所有监控数据响应都要能回答四件事：采集于何时、来自哪里、是否命中缓存、是否已过期。
字段名与已成型的 app.k8s_monitor._envelope / app.data_services._envelope 对齐，
不引入第二套命名：

    data_timestamp     epoch 秒（float），响应所载数据的采集时刻
    cached             是否命中缓存（该端点无缓存层时恒为 False）
    cache_age_seconds  缓存年龄（秒）；未命中为 0
    source_status      {子源: "ok"|"empty"|"fallback_summary_cache"|"unavailable"|"skipped"}
    partial_errors     采集降级信息（与既有信封同名，可与 notes 并存）
    stale              数据年龄是否超过 staleness_seconds
    staleness_seconds  服务端判定的过期阈值；None = 该端点无“过期”概念

字典型端点直接并进响应体；列表型端点（/servers、/services/plaza）顶层是数组、
前端按数组消费，不能换结构，改由同名 HTTP 响应头下发（见 freshness_headers()）。

判定“过期”的口径沿用既有 app.topology._metric_is_stale：数据缺失（无时间戳）
同样算不新鲜。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

# 指标过期阈值：与 app.topology._METRIC_STALE_SECONDS 一致（约 30s 采样 ×3）
METRIC_STALENESS_SECONDS = 90.0


def to_epoch(value: Any) -> Optional[float]:
    """datetime / epoch 秒 / ISO 字符串 → epoch 秒；None 或无法解析返回 None。

    无时区的 datetime 与无偏移的 ISO 串一律按 UTC 解释（与本仓库的
    datetime.utcnow() 约定一致）。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    try:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    except Exception:
        return None


def age_seconds(value: Any) -> Optional[float]:
    """数据时刻 → 距今秒数（≥0）；缺失/无法解析返回 None。"""
    epoch = to_epoch(value)
    if epoch is None:
        return None
    return max(0.0, time.time() - epoch)


def freshness_fields(*, data_timestamp: Any = None, cached: bool = False,
                     cache_age_seconds: float = 0.0, source_status: Optional[dict] = None,
                     partial_errors: Optional[list] = None,
                     staleness_seconds: Optional[float] = METRIC_STALENESS_SECONDS) -> dict:
    """构造新鲜度字段（并入字典型响应体）。

    data_timestamp 缺省 = 响应生成时刻（无独立数据时刻的实时读取）。
    """
    age = age_seconds(data_timestamp)
    stamp = to_epoch(data_timestamp)
    if stamp is None:
        stamp = time.time()
    if staleness_seconds is None:
        stale = False
    else:
        stale = age is None or age > float(staleness_seconds)
    return {
        "data_timestamp": round(float(stamp), 3),
        "cached": bool(cached),
        "cache_age_seconds": round(float(cache_age_seconds or 0.0), 3),
        "source_status": dict(source_status or {}),
        "partial_errors": list(partial_errors or []),
        "stale": bool(stale),
        "staleness_seconds": (None if staleness_seconds is None
                              else round(float(staleness_seconds), 3)),
    }


def freshness_headers(values: dict) -> dict:
    """新鲜度字段 → HTTP 响应头（列表型端点的结构兼容通道）。

    data_timestamp → X-Data-Timestamp，cached → X-Cached，其余同理；
    列表/字典值用 JSON 编码，标量用 JSON 字面量（true/false/数字）。
    """
    out: dict[str, str] = {}
    for key, value in values.items():
        name = "X-" + key.replace("_", "-").title()
        out[name] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return out


def _demo() -> None:
    """最小自检：跑 `python -m app.freshness` 时执行。"""
    now = time.time()
    fresh = freshness_fields(data_timestamp=now - 5, source_status={"metrics": "ok"})
    assert fresh["stale"] is False and fresh["staleness_seconds"] == 90.0
    assert abs(fresh["data_timestamp"] - (now - 5)) < 0.01
    aged = freshness_fields(data_timestamp=now - 3600)
    assert aged["stale"] is True
    missing = freshness_fields(data_timestamp=None)
    assert missing["stale"] is True and missing["data_timestamp"] > 0
    no_ttl = freshness_fields(data_timestamp=None, staleness_seconds=None)
    assert no_ttl["stale"] is False and no_ttl["staleness_seconds"] is None
    assert abs(age_seconds(datetime.utcnow()) or 99) < 2
    assert abs(age_seconds(datetime.now(timezone.utc).isoformat()) or 99) < 2
    assert age_seconds("not-a-time") is None
    head = freshness_headers(fresh)
    assert head["X-Data-Timestamp"] == json.dumps(fresh["data_timestamp"])
    assert head["X-Cached"] == "false"
    assert json.loads(head["X-Source-Status"]) == {"metrics": "ok"}
    print("freshness self-check ok")


if __name__ == "__main__":  # pragma: no cover
    _demo()
