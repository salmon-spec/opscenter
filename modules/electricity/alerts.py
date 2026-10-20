"""Alert rules, state machine, silence handling and usage forecasting.

Design notes (borrowed, see README "借鉴来源"):

* Rule evaluation is a pure function of the collection snapshot plus a JSON
  alert state, so `findings_for_workbench()` and the push notifier always agree.
* State machine follows the Prometheus Alertmanager model simplified to what a
  single household needs: ``inactive -> pending -> firing -> resolved``, with
  ``repeat_interval`` re-notification while firing and one resolved message when
  the condition clears.
* Every alert id is a stable ``rule:scope`` key, so dedup survives restarts and
  no notification storm happens across container restarts.
* Quiet hours suppress non-critical pushes (they are still recorded and still
  visible in the workbench).
* Tiered (阶梯) and time-of-use (分时) data is parsed heuristically from raw
  upstream payloads because the field names were never confirmed; when absent the
  forecast silently falls back to the flat-rate model.
"""
from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone

TZ = timezone(timedelta(hours=8))

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}

# Alert identifiers (stable, used as dedup keys and as silence targets).
RULE_CREDENTIALS = "electricity:credentials"
RULE_NO_DATA = "electricity:no-data"
RULE_STALE = "electricity:stale"
RULE_ARREARS = "electricity:arrears"
RULE_PENALTY = "electricity:penalty"
RULE_BALANCE_CRITICAL = "electricity:balance-critical"
RULE_BALANCE_LOW = "electricity:balance-low"
RULE_DEPLETION = "electricity:depletion"
RULE_DAILY_SPIKE = "electricity:daily-spike"
RULE_SLICE_RETRY = "electricity:slice-retry"
RULE_BUDGET = "electricity:budget"
RULE_STEP_ENTER = "electricity:step-enter"
RULE_TOU_TIP_HEAVY = "electricity:tou-tip-heavy"
RULE_RELOGIN_STORM = "electricity:relogin-storm"

MAX_BALANCE_HISTORY = 120


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "") or default))
    except ValueError:
        return default


def config() -> dict:
    """Thresholds and behaviour knobs, all overridable by environment."""
    return {
        "low_balance": _env_float("ELECTRICITY_LOW_BALANCE", 20.0),
        "critical_balance": _env_float("ELECTRICITY_CRITICAL_BALANCE", 5.0),
        "depletion_days": _env_float("ELECTRICITY_DEPLETION_DAYS", 5.0),
        "price_per_kwh": _env_float("ELECTRICITY_PRICE_PER_KWH", 0.5),
        "daily_spike_ratio": _env_float("ELECTRICITY_DAILY_SPIKE_RATIO", 2.0),
        "daily_spike_min_kwh": _env_float("ELECTRICITY_DAILY_SPIKE_MIN_KWH", 3.0),
        "stale_after_seconds": _env_int("ELECTRICITY_STALE_AFTER_SECONDS", 86400),
        "repeat_interval_seconds": _env_int("ELECTRICITY_REPEAT_INTERVAL_SECONDS", 86400),
        "pending_observations": max(1, _env_int("ELECTRICITY_PENDING_OBSERVATIONS", 1)),
        "quiet_hours": (os.getenv("ELECTRICITY_QUIET_HOURS") or "").strip(),
        # v0.3.0
        "step_warn_kwh": _env_float("ELECTRICITY_STEP_WARN_KWH", 100.0),
        "tou_tip_ratio": _env_float("ELECTRICITY_TOU_TIP_RATIO", 0.5),
        # v0.4.0
        "monthly_budget": _env_float("ELECTRICITY_MONTHLY_BUDGET", 0.0),
        "relogin_warn": _env_int("ELECTRICITY_RELOGIN_WARN", 4),
    }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def parse_updated(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def latest_row(snapshot: dict) -> dict:
    rows = snapshot.get("households") or []
    return rows[0] if rows and isinstance(rows[0], dict) else {}


def parse_quiet_hours(spec: str) -> tuple[time, time] | None:
    """``"23:00-07:00"`` -> (start, end); overnight ranges are allowed."""
    if not spec or "-" not in spec:
        return None
    left, _, right = spec.partition("-")
    try:
        start = time.fromisoformat(left.strip())
        end = time.fromisoformat(right.strip())
    except ValueError:
        return None
    return start, end


def in_quiet_hours(spec: str, now: datetime) -> bool:
    window = parse_quiet_hours(spec)
    if not window:
        return False
    start, end = window
    local = now.astimezone(TZ).time()
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def daily_average(daily: list, days: int = 7) -> float | None:
    values = [float(item.get("kwh") or 0) for item in (daily or [])[:days] if isinstance(item, dict)]
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def measured_burn_rate(history: list) -> float | None:
    """Fallback 元/天 from balance observations when daily kWh is unavailable.

    Balance changes can include recharges or adjustments, so published usage
    and the known price take precedence when estimating depletion.
    """
    points = []
    for item in history or []:
        try:
            when = datetime.fromisoformat(str(item["at"]))
            amount = float(item["balance"])
        except (KeyError, TypeError, ValueError):
            continue
        points.append((when if when.tzinfo else when.replace(tzinfo=timezone.utc), amount))
    if len(points) < 2:
        return None
    points.sort(key=lambda pair: pair[0])
    first, last = points[0], points[-1]
    span_days = (last[0] - first[0]).total_seconds() / 86400
    if span_days < 0.5:
        return None
    drop = first[1] - last[1]
    if drop <= 0:  # recharged, or balance went up: no usable burn rate
        return None
    return round(drop / span_days, 3)


def forecast_usage(daily: list, days: int = 7) -> dict:
    """Weekday-aware short forecast: same-weekday average first, then overall mean.

    National-grid daily readings are published per calendar day, so grouping by
    ``date.weekday()`` picks up the weekend pattern without any model.
    """
    rows = []
    for item in daily or []:
        if not isinstance(item, dict):
            continue
        try:
            when = datetime.strptime(str(item.get("day", ""))[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        rows.append((when, float(item.get("kwh") or 0)))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    if not rows:
        return {"days": 0, "daily_kwh": [], "total_kwh": None, "average_kwh": None, "method": "none"}

    overall = sum(value for _, value in rows) / len(rows)
    by_weekday: dict[int, list[float]] = {}
    for when, value in rows:
        by_weekday.setdefault(when.weekday(), []).append(value)

    projected: list[dict] = []
    start = rows[0][0] + timedelta(days=1)
    for offset in range(days):
        target = start + timedelta(days=offset)
        bucket = by_weekday.get(target.weekday()) or []
        value = (sum(bucket) / len(bucket)) if bucket else overall
        projected.append({"day": target.isoformat(), "kwh": round(value, 3)})
    return {
        "days": len(projected),
        "daily_kwh": projected,
        "total_kwh": round(sum(item["kwh"] for item in projected), 3),
        "average_kwh": round(overall, 3),
        "method": "weekday-average" if len(rows) >= 7 else "flat-average",
    }


def current_step(steps: list) -> dict | None:
    for item in steps or []:
        if isinstance(item, dict) and item.get("current"):
            return item
    return steps[0] if steps and isinstance(steps[0], dict) else None


def next_step(steps: list) -> dict | None:
    ordered = [item for item in (steps or []) if isinstance(item, dict)]
    current = current_step(ordered)
    if not current:
        return None
    try:
        index = ordered.index(current)
    except ValueError:
        return None
    return ordered[index + 1] if index + 1 < len(ordered) else None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def remaining_days(balance: float | None, ctx: dict, cfg: dict) -> tuple[float | None, float | None, str]:
    """Days until the balance runs out, the effective daily cost and the basis.

    Tier-aware when 阶梯 data is present: the balance is consumed tier by tier at
    each tier's own price instead of one flat rate.
    """
    if balance is None:
        return None, None, "none"
    balance = max(float(balance), 0.0)
    steps = [item for item in (ctx.get("steps") or []) if isinstance(item, dict)]
    step = current_step(steps)
    daily_kwh = ctx.get("average_7d_kwh")

    if step and daily_kwh and daily_kwh > 0:
        try:
            idx = steps.index(step)
        except ValueError:
            idx = 0
        left = balance
        days = 0.0
        for candidate in steps[idx:]:
            price = _as_float(candidate.get("price")) or ctx.get("effective_price") or cfg["price_per_kwh"]
            remain_kwh = _as_float(candidate.get("remain"))
            capacity = (remain_kwh * price) if remain_kwh and remain_kwh > 0 else None
            if capacity is None or capacity >= left:
                days += left / (daily_kwh * price)
                left = 0.0
                break
            days += remain_kwh / daily_kwh
            left -= capacity
        if left > 0:  # ran past the last known tier: keep the last price
            last_price = _as_float(steps[-1].get("price")) or cfg["price_per_kwh"]
            days += left / (daily_kwh * last_price)
        if days > 0:
            return round(days, 1), round(balance / days, 3), "tiered-kwh-price"

    if daily_kwh:
        price = ctx.get("effective_price") or cfg["price_per_kwh"]
        cost = round(daily_kwh * price, 3)
        if cost > 0:
            return round(balance / cost, 1), cost, "kwh-price"
    measured = ctx.get("measured_daily_cost")
    if measured and measured > 0:
        return round(balance / measured, 1), measured, "measured"
    return None, None, "none"


def _month_projection(month_cost: float | None, now: datetime) -> float | None:
    if month_cost is None:
        return None
    local_now = now.astimezone(TZ)
    elapsed = max(local_now.day, 1)
    first_next = (local_now.replace(day=1) + timedelta(days=32)).replace(day=1)
    total_days = (first_next - local_now.replace(day=1)).days or 30
    return round(float(month_cost) / elapsed * total_days, 2)


def build_context(snapshot: dict, configured: bool, now: datetime, cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    row = latest_row(snapshot)
    daily = row.get("daily") or []
    updated = parse_updated(snapshot.get("updated_at"))
    age = (now - updated).total_seconds() if updated else None
    history = snapshot.get("_balance_history") or []

    balance = row.get("balance")
    balance = float(balance) if isinstance(balance, (int, float)) else None

    ctx = {
        "configured": configured,
        "resource_id": str(row.get("id") or "") or "electricity",
        "name": str(row.get("name") or ""),
        "balance": balance,
        "arrears": row.get("arrears"),
        "due": row.get("due"),
        "daily": daily,
        "bills": row.get("bills") or [],
        "steps": [item for item in (row.get("steps") or []) if isinstance(item, dict)],
        "tou": row.get("tou") if isinstance(row.get("tou"), dict) else None,
        "updated_at": updated,
        "age_seconds": age,
        "average_7d_kwh": daily_average(daily, 7),
        "measured_daily_cost": measured_burn_rate(history),
        "daily_slice_errors": [str(item) for item in (snapshot.get("errors") or [])],
        "balance_history_points": len(history or []),
        "daily_history_points": len(snapshot.get("_daily_history") or []),
        "monthly_history_points": len(snapshot.get("_monthly_history") or []),
        "relogin_count": int(row.get("relogin_count") or snapshot.get("relogin_count") or 0),
        "penalty": float(row["penalty"]) if isinstance(row.get("penalty"), (int, float)) else None,
        "prepay": float(row["prepay"]) if isinstance(row.get("prepay"), (int, float)) else None,
        "year": row.get("year") if isinstance(row.get("year"), dict) else None,
        "month_cost": None,
        "month_cost_estimated": bool(snapshot.get("month_cost_estimated")),
        "month_label": str(snapshot.get("month_label") or now.astimezone(TZ).strftime("%Y%m")),
        "forecast": forecast_usage(daily, 7),
    }
    # month_cost lives on the snapshot (written by the collector), but a row-level
    # value wins if a future collector puts it there.
    month_cost = row.get("month_cost")
    if not isinstance(month_cost, (int, float)):
        month_cost = snapshot.get("month_cost")
    if isinstance(month_cost, (int, float)):
        ctx["month_cost"] = float(month_cost)
    # Real tariff-inclusive average price from the year-to-date bill totals.
    year = ctx["year"] or {}
    effective = year.get("price") if isinstance(year.get("price"), (int, float)) else None
    ctx["effective_price"] = effective
    ctx["price_source"] = "year-average" if effective else "env"
    if effective:
        ctx["price_per_kwh"] = effective
    else:
        ctx["price_per_kwh"] = cfg["price_per_kwh"]
    ctx["remaining_days"], ctx["estimated_daily_cost"], ctx["estimate_source"] = remaining_days(balance, ctx, cfg)
    ctx["month_cost_projection"] = _month_projection(ctx["month_cost"], now)
    return ctx


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #
def _alert(rule: str, severity: str, title: str, summary: str, ctx: dict, now: datetime, **extra) -> dict:
    item = {
        "id": rule,
        "rule": rule.split(":", 1)[1],
        "resource_id": ctx["resource_id"],
        "severity": severity,
        "title": title,
        "summary": summary,
        "observed_at": now,
    }
    item.update(extra)
    return item


def evaluate_rules(ctx: dict, now: datetime, cfg: dict | None = None) -> list[dict]:
    """Current alert set for one snapshot. Pure: no IO, no side effects."""
    cfg = cfg or config()
    items: list[dict] = []

    if not ctx["configured"]:
        items.append(_alert(
            RULE_CREDENTIALS, "critical", "电费采集凭据未配置",
            "模块缺少国网账号、密码摘要或目标户号，无法采集电费数据。", ctx, now,
        ))
        return items

    if ctx["updated_at"] is None:
        items.append(_alert(
            RULE_NO_DATA, "warning", "电费数据尚未采集",
            "模块启动后还没有成功完成一次采集。", ctx, now,
        ))
        return items

    if ctx["age_seconds"] is not None and ctx["age_seconds"] > cfg["stale_after_seconds"]:
        hours = cfg["stale_after_seconds"] // 3600
        items.append(_alert(
            RULE_STALE, "warning", f"电费数据已超过 {hours} 小时未更新",
            f"最近一次成功采集时间：{ctx['updated_at'].astimezone(TZ).isoformat(timespec='seconds')}。", ctx, now,
            value=round((ctx["age_seconds"] or 0) / 3600, 1),
            details={"last_success": ctx["updated_at"].astimezone(TZ).isoformat(timespec="seconds")},
        ))

    arrears = ctx["arrears"]
    if isinstance(arrears, (int, float)) and float(arrears) > 0:
        items.append(_alert(
            RULE_ARREARS, "critical", "存在待缴欠费",
            f"国网已产生欠费 {float(arrears):.2f} 元；若已签约智能交费，欠费可能直接触发停电，请尽快缴纳。",
            ctx, now, value=round(float(arrears), 2),
        ))

    penalty = ctx.get("penalty")
    if isinstance(penalty, (int, float)) and float(penalty) > 0:
        items.append(_alert(
            RULE_PENALTY, "critical", "已产生电费违约金",
            f"国网已计违约金 {float(penalty):.2f} 元，说明存在逾期未缴，请立即处理以免影响用电。",
            ctx, now, value=round(float(penalty), 2),
        ))

    balance = ctx["balance"]
    if balance is not None:
        if balance < cfg["critical_balance"]:
            items.append(_alert(
                RULE_BALANCE_CRITICAL, "critical", f"电费余额极度不足（低于 {cfg['critical_balance']:g} 元）",
                f"当前余额 {balance:.2f} 元，随时可能欠费停电，请立即充值。", ctx, now,
                value=round(balance, 2),
            ))
        elif balance < cfg["low_balance"]:
            items.append(_alert(
                RULE_BALANCE_LOW, "warning", f"电费余额低于 {cfg['low_balance']:g} 元",
                f"当前余额 {balance:.2f} 元，请及时充值。", ctx, now,
                value=round(balance, 2),
            ))

        remaining = ctx["remaining_days"]
        if remaining is not None and remaining < cfg["depletion_days"]:
            basis_text = {
                "measured": "按实测日均扣费",
                "kwh-price": "按近 7 日均电量估算",
                "tiered-kwh-price": "按当前阶梯电价与日均电量估算",
            }.get(ctx["estimate_source"], "按日均用量估算")
            items.append(_alert(
                RULE_DEPLETION, "warning", f"预计 {remaining:g} 天后余额耗尽",
                f"当前余额 {balance:.2f} 元，{basis_text}约 {ctx['estimated_daily_cost']:g} 元/天"
                f"（近 7 日均 {ctx['average_7d_kwh'] if ctx['average_7d_kwh'] is not None else '—'} 度）。",
                ctx, now, value=remaining,
                details={"remaining_days": remaining, "daily_cost": ctx["estimated_daily_cost"],
                         "estimate_source": ctx["estimate_source"]},
            ))

    daily = ctx["daily"]
    average = ctx["average_7d_kwh"]
    if daily and average and average > 0:
        latest_kwh = float((daily[0] or {}).get("kwh") or 0)
        if (latest_kwh >= cfg["daily_spike_min_kwh"]
                and latest_kwh > average * cfg["daily_spike_ratio"]):
            items.append(_alert(
                RULE_DAILY_SPIKE, "info", "日用电量异常升高",
                f"{daily[0].get('day')} 用电 {latest_kwh:g} 度，为近 7 日均值 {average:g} 度的 "
                f"{latest_kwh / average:.1f} 倍，请留意是否有大功率电器长时间运行。", ctx, now,
                value=round(latest_kwh, 2),
                details={"day": (daily[0] or {}).get("day"), "kwh": round(latest_kwh, 2),
                         "average_7d_kwh": average, "ratio": round(latest_kwh / average, 2)},
            ))

    # ---- tiered tariff ----
    step = current_step(ctx["steps"])
    upcoming = next_step(ctx["steps"])
    if step and upcoming:
        remain_value = _as_float(step.get("remain"))
        if remain_value is not None and remain_value < cfg["step_warn_kwh"]:
            severity = "warning" if int(upcoming.get("stage") or 2) >= 3 else "info"
            items.append(_alert(
                RULE_STEP_ENTER, severity, f"电量即将进入第 {upcoming.get('stage')} 阶梯",
                f"当前处于第 {step.get('stage')} 阶梯，剩余 {remain_value:g} 度；"
                f"进入第 {upcoming.get('stage')} 阶梯后单价升为 {upcoming.get('price')} 元/度，"
                "后续电费支出会上升。", ctx, now, value=remain_value,
                details={"stage": step.get("stage"), "remain_kwh": remain_value,
                         "next_stage": upcoming.get("stage"), "next_price": upcoming.get("price")},
            ))

    # ---- time-of-use share ----
    tou = ctx["tou"] or {}
    buckets = {key: tou.get(key) for key in ("tip", "peak", "flat", "valley")
               if isinstance(tou.get(key), (int, float))}
    total = sum(buckets.values())
    if total > 0:
        tip_peak = (buckets.get("tip") or 0) + (buckets.get("peak") or 0)
        share = tip_peak / total
        if share > cfg["tou_tip_ratio"]:
            items.append(_alert(
                RULE_TOU_TIP_HEAVY, "info", "尖峰时段用电占比偏高",
                f"本月尖+峰用电 {tip_peak:g} 度，占统计电量 {share * 100:.0f}%；"
                "把可延后的电器挪到 22:00 之后（谷段）能显著降低电费。", ctx, now,
                value=round(share, 3),
                details={"tip": buckets.get("tip"), "peak": buckets.get("peak"),
                         "flat": buckets.get("flat"), "valley": buckets.get("valley"),
                         "tip_peak_share": round(share, 3)},
            ))

    # ---- monthly budget ----
    budget = cfg["monthly_budget"]
    if budget and budget > 0 and ctx["month_cost"] is not None:
        projection = ctx["month_cost_projection"]
        month = ctx["month_label"]
        if ctx["month_cost"] >= budget:
            items.append(_alert(
                RULE_BUDGET, "critical", f"{month} 电费已超月度预算",
                f"本月已计电费 {ctx['month_cost']:.2f} 元，超过预算 {budget:.2f} 元"
                + (f"，预计月末达 {projection:.2f} 元。" if projection is not None else "。"), ctx, now,
                value=round(ctx["month_cost"], 2),
                details={"month": month, "spent": ctx["month_cost"], "budget": budget, "projection": projection},
            ))
        elif projection is not None and projection > budget:
            items.append(_alert(
                RULE_BUDGET, "warning", f"{month} 电费预计超出月度预算",
                f"本月已计 {ctx['month_cost']:.2f} 元，按当前节奏预计月末 {projection:.2f} 元，"
                f"超出预算 {budget:.2f} 元。", ctx, now,
                value=round(projection, 2),
                details={"month": month, "spent": ctx["month_cost"], "budget": budget, "projection": projection},
            ))

    # ---- session churn ----
    relogin = ctx.get("relogin_count") or 0
    if cfg["relogin_warn"] > 0 and relogin >= cfg["relogin_warn"]:
        items.append(_alert(
            RULE_RELOGIN_STORM, "warning", "国网会话频繁重登",
            f"近 12 小时已重新登录 {relogin} 次，可能是密码变更、会话策略调整或触发了风控，"
            "请检查采集是否仍能稳定取到数据。", ctx, now, value=relogin,
            details={"relogin_count": relogin},
        ))

    for message in ctx["daily_slice_errors"][:5]:
        digest = abs(hash(message)) % 10**8
        items.append(_alert(
            f"{RULE_SLICE_RETRY}:{digest}", "info", "部分电费数据待重试", message[:400], ctx, now,
            details={"message": message[:400]},
        ))

    return items


# --------------------------------------------------------------------------- #
# state machine
# --------------------------------------------------------------------------- #
def _entry_state(alert: dict) -> dict:
    return {
        "id": alert["id"],
        "rule": alert["rule"],
        "resource_id": alert["resource_id"],
        "severity": alert["severity"],
        "title": alert["title"],
        "status": "pending",
        "first_seen": alert["observed_at"].isoformat(timespec="seconds"),
        "last_seen": alert["observed_at"].isoformat(timespec="seconds"),
        "observations": 1,
        "fired_at": None,
        "last_notified_at": None,
        "notify_count": 0,
    }


def is_silenced(state: dict, alert_id: str, now: datetime) -> bool:
    silences = state.get("silences") or {}
    until = silences.get(alert_id) or silences.get("*")
    if not until:
        return False
    try:
        deadline = datetime.fromisoformat(str(until))
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return now < deadline


def should_notify(entry: dict, alert: dict, now: datetime, state: dict, cfg: dict) -> bool:
    if entry["status"] != "firing":
        return False
    if is_silenced(state, alert["id"], now):
        return False
    if alert["severity"] != "critical" and in_quiet_hours(cfg["quiet_hours"], now):
        return False
    last = entry.get("last_notified_at")
    if not last:
        return True
    try:
        last_at = datetime.fromisoformat(str(last))
    except ValueError:
        return True
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    return (now - last_at).total_seconds() >= cfg["repeat_interval_seconds"]


def evaluate(ctx: dict, state: dict | None, now: datetime, cfg: dict | None = None) -> dict:
    """Advance the alert state machine by one observation.

    Returns ``{"alerts": [...], "events": [...], "resolved": [...], "state": {...}}``
    where events are the notifications that must be delivered now.
    """
    cfg = cfg or config()
    state = dict(state or {})
    active = evaluate_rules(ctx, now, cfg)
    active_by_id = {item["id"]: item for item in active}
    entries: dict = dict(state.get("active") or {})
    events: list[dict] = []

    for alert_id, alert in active_by_id.items():
        entry = entries.get(alert_id)
        if entry is None:
            entry = _entry_state(alert)
            if cfg["pending_observations"] <= 1:
                entry["status"] = "firing"
                entry["fired_at"] = alert["observed_at"].isoformat(timespec="seconds")
        else:
            entry["last_seen"] = alert["observed_at"].isoformat(timespec="seconds")
            entry["severity"] = alert["severity"]
            entry["title"] = alert["title"]
            entry["observations"] = int(entry.get("observations") or 1) + 1
            if entry["status"] == "pending" and entry["observations"] >= cfg["pending_observations"]:
                entry["status"] = "firing"
                entry["fired_at"] = alert["observed_at"].isoformat(timespec="seconds")
        entries[alert_id] = entry
        if should_notify(entry, alert, now, state, cfg):
            entry["last_notified_at"] = now.isoformat(timespec="seconds")
            entry["notify_count"] = int(entry.get("notify_count") or 0) + 1
            events.append({
                "action": "firing" if entry["notify_count"] == 1 else "re-fire",
                "alert": alert,
                "previous_status": "pending" if entry["notify_count"] == 1 else "firing",
            })

    # Anything that was firing/pending and disappeared is resolved.
    resolved: list[dict] = []
    for alert_id in list(entries):
        if alert_id in active_by_id:
            continue
        entry = entries.pop(alert_id)
        if entry.get("status") in {"pending", "firing"}:
            resolved.append({
                "id": alert_id,
                "rule": entry.get("rule"),
                "resource_id": entry.get("resource_id"),
                "severity": entry.get("severity"),
                "title": entry.get("title"),
                "summary": f"{entry.get('title') or '告警'} 已恢复。",
                "observed_at": now,
            })

    state["active"] = entries
    state["updated_at"] = now.isoformat(timespec="seconds")
    state["silences"] = {
        key: value for key, value in (state.get("silences") or {}).items()
        if not _silence_expired(value, now)
    }
    return {"alerts": [active_by_id[key] for key in entries], "events": events, "resolved": resolved, "state": state}


def _silence_expired(value, now: datetime) -> bool:
    try:
        deadline = datetime.fromisoformat(str(value))
    except ValueError:
        return True
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline <= now


def record_balance(snapshot: dict, state: dict, now: datetime) -> dict:
    """Append the newest balance observation used for the measured burn rate."""
    row = latest_row(snapshot)
    balance = row.get("balance")
    if not isinstance(balance, (int, float)):
        return state
    ident = str(row.get("id") or "")
    history = list(state.get("balance_history") or [])
    point = {"at": now.isoformat(timespec="seconds"), "balance": round(float(balance), 2), "id": ident}
    if history and history[-1].get("balance") == point["balance"] and history[-1].get("id") == ident:
        history[-1] = point  # keep only the latest reading of an unchanged value
    else:
        history.append(point)
    state["balance_history"] = history[-MAX_BALANCE_HISTORY:]
    return state


def apply_silence(state: dict, alert_id: str, hours: float, now: datetime) -> dict:
    until = now + timedelta(hours=hours)
    silences = dict(state.get("silences") or {})
    silences[alert_id or "*"] = until.isoformat(timespec="seconds")
    state["silences"] = silences
    return state


def rules_documentation(cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    rules = [
        {"id": RULE_CREDENTIALS, "severity": "critical", "title": "电费采集凭据未配置"},
        {"id": RULE_NO_DATA, "severity": "warning", "title": "电费数据尚未采集"},
        {"id": RULE_STALE, "severity": "warning",
         "title": f"采集结果超过 {cfg['stale_after_seconds'] // 3600} 小时未更新"},
        {"id": RULE_ARREARS, "severity": "critical", "title": "存在待缴欠费（>0 元）"},
        {"id": RULE_PENALTY, "severity": "critical", "title": "已产生电费违约金（>0 元）"},
        {"id": RULE_BALANCE_CRITICAL, "severity": "critical", "title": f"余额 < {cfg['critical_balance']:g} 元"},
        {"id": RULE_BALANCE_LOW, "severity": "warning", "title": f"余额 < {cfg['low_balance']:g} 元"},
        {"id": RULE_DEPLETION, "severity": "warning",
         "title": f"预计可用天数 < {cfg['depletion_days']:g} 天"},
        {"id": RULE_DAILY_SPIKE, "severity": "info",
         "title": f"日电量 > 近 7 日均值 × {cfg['daily_spike_ratio']:g}"},
        {"id": RULE_STEP_ENTER, "severity": "info/warning",
         "title": f"距下一阶梯剩余电量 < {cfg['step_warn_kwh']:g} 度（进入三档升为 warning）"},
        {"id": RULE_TOU_TIP_HEAVY, "severity": "info",
         "title": f"尖+峰用电占比 > {cfg['tou_tip_ratio']:g}"},
        {"id": RULE_BUDGET, "severity": "warning/critical",
         "title": f"月度预算 {cfg['monthly_budget']:g} 元（0 = 关闭）：预计超出 warning / 已超 critical"},
        {"id": RULE_RELOGIN_STORM, "severity": "warning",
         "title": f"近 12 小时国网会话重登 ≥ {cfg['relogin_warn']} 次"},
        {"id": RULE_SLICE_RETRY, "severity": "info", "title": "部分数据待重试（逐日/账单切片失败）"},
    ]
    return {
        "thresholds": cfg,
        "rules": rules,
        "quiet_hours": cfg["quiet_hours"] or "未启用",
        "repeat_interval_hours": round(cfg["repeat_interval_seconds"] / 3600, 2),
    }
