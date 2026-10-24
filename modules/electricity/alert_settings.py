"""Editable alert configuration; the same schema drives validation and the UI."""
from __future__ import annotations

import math
import re

# key, label, minimum, maximum, step, help
FIELDS = [
    ("low_balance", "余额预警（元）", 0, 1000000, 0.01, "余额低于此值时预警"),
    ("critical_balance", "余额严重告警（元）", 0, 1000000, 0.01, "须小于或等于余额预警阈值"),
    ("depletion_days", "预计可用天数", 0, 3650, 0.1, "预计可用天数低于此值时提醒"),
    ("monthly_budget", "月度预算（元）", 0, 1000000, 0.01, "0 表示关闭预算告警"),
    ("daily_spike_ratio", "日用电突增倍数", 1, 1000, 0.1, "超过近 7 日均值的此倍数"),
    ("daily_spike_min_kwh", "突增最低用电量（度）", 0, 1000000, 0.1, "同时达到此用量才触发突增提醒"),
    ("stale_after_seconds", "数据过期阈值（秒）", 60, 31536000, 1, "超过此时间未成功采集则告警"),
    ("step_warn_kwh", "阶梯剩余电量（度）", 0, 1000000, 0.1, "低于此值提醒；需上游提供阶梯数据"),
    ("tou_tip_ratio", "尖峰用电占比", 0, 1, 0.01, "0–1，例如 0.5 表示 50%；需分时数据"),
    ("relogin_warn", "重新登录告警次数", 1, 10000, 1, "采集重新登录达到此次数时告警"),
    ("price_per_kwh", "备用电价（元/度）", 0.01, 100, 0.01, "仅在账单无法提供实际均价时用于估算"),
    ("repeat_interval_seconds", "重复通知间隔（秒）", 60, 31536000, 1, "持续告警的最短提醒间隔；实际由定期评估触发"),
    ("max_notifications", "每轮告警通知上限（次）", 0, 10000, 1, "含首次提醒，0 表示不限；按告警计发送轮次，失败也计次，恢复通知另计"),
    ("pending_observations", "连续命中次数", 1, 1000, 1, "连续评估达到此次数后才开始通知"),
]


def schema() -> list[dict]:
    return [dict(key=k, label=label, min=lo, max=hi, step=step, help=help_text)
            for k, label, lo, hi, step, help_text in FIELDS]


def validate(payload: dict, current: dict) -> dict:
    allowed = {item[0] for item in FIELDS} | {"quiet_hours", "notify_recovery"}
    if set(payload) - allowed:
        raise ValueError("包含不支持的告警设置")
    result = {**current, **payload}
    for key, label, lo, hi, step, _ in FIELDS:
        value = result[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not lo <= value <= hi
                or (step == 1 and int(value) != value)):
            raise ValueError(f"{label}须为 {lo}–{hi} 范围内的{'整数' if step == 1 else '数值'}")
    if result["critical_balance"] > result["low_balance"]:
        raise ValueError("余额严重告警阈值不能高于余额预警阈值")
    if not isinstance(result["notify_recovery"], bool):
        raise ValueError("恢复通知须为开关值")
    from alerts import parse_quiet_hours
    quiet = result["quiet_hours"]
    if not isinstance(quiet, str) or (quiet and (
            not re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", quiet) or not parse_quiet_hours(quiet))):
        raise ValueError("免打扰时段须为 HH:MM-HH:MM，或留空")
    return {key: result[key] for key in allowed}
