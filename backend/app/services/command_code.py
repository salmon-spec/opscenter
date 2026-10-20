"""Read-only Command Code usage monitor.

Command Code does not publish a stable public usage API. The official CLI
reads usage through the private ``/alpha`` endpoints implemented here. The
adapter is deliberately fail-soft and exposes only normalised, non-secret
metrics so API contract changes are visible instead of silently becoming zero.
"""

from __future__ import annotations

import copy
import hashlib
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from sqlalchemy import func

from app.config import (
    COMMAND_CODE_API_BASE,
    COMMAND_CODE_API_KEY,
    COMMAND_CODE_TIMEOUT_SECONDS,
)
from app.database import get_db
from app.models import CommandCodeUsageSnapshot

PLAN_DETAILS = {
    "individual-go": {"name": "Go", "monthly_credits": 10},
    "individual-goat": {"name": "GOAT", "monthly_credits": 70},
    "individual-pro": {"name": "Pro", "monthly_credits": 30},
    "individual-pro-v1": {"name": "Pro", "monthly_credits": 80},
    "individual-provider": {"name": "Provider", "monthly_credits": 15},
    "individual-max": {"name": "Max", "monthly_credits": 150},
    "individual-ultra": {"name": "Ultra", "monthly_credits": 300},
    "teams-pro": {"name": "Teams Pro", "monthly_credits": 40},
}
DEFAULT_REFRESH_SECONDS = 60
SNAPSHOT_MIN_INTERVAL_SECONDS = 30
HISTORY_RETENTION_DAYS = 35
HISTORY_WINDOWS = {
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(days=7),
}


class CommandCodeUsageError(RuntimeError):
    """A safe Command Code error whose message can be shown in the module UI."""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _non_negative(value: Any) -> float:
    parsed = _number(value)
    return max(0.0, parsed) if parsed is not None else 0.0


def _mask_secret(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 10:
        return f"{raw[:2]}****{raw[-2:]}"
    return f"{raw[:6]}...{raw[-4:]}"


def _mask_identifier(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return "***"
    return f"{raw[:4]}...{raw[-2:]}"


def _mask_email(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or "@" not in raw:
        return ""
    local, domain = raw.rsplit("@", 1)
    return f"{local[:4]}***@{domain}"


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso_from_epoch(value: Any) -> str | None:
    numeric = _number(value)
    if numeric is None or numeric <= 0:
        return None
    if numeric > 100_000_000_000:
        numeric /= 1000
    try:
        return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _plan_info(plan_id: str) -> dict:
    normalized = str(plan_id or "").strip().lower().replace("_", "-")
    for key in sorted(PLAN_DETAILS, key=len, reverse=True):
        detail = PLAN_DETAILS[key]
        if normalized.startswith(key):
            return {"id": plan_id, **detail}
    return {
        "id": plan_id,
        "name": plan_id.replace("individual-", "").replace("teams-", "Teams ").replace("-", " ").title(),
        "monthly_credits": None,
    }


class CommandCodeUsageMonitor:
    def __init__(self) -> None:
        self._config_lock = threading.Lock()
        self._runtime_config: dict | None = None
        self._cache_lock = threading.Lock()
        self._cache: dict | None = None
        self._cache_at = 0.0
        self.history_scope: str | None = None

    def _config_snapshot(self) -> dict | None:
        with self._config_lock:
            return copy.deepcopy(self._runtime_config) if self._runtime_config else None

    def configure(self, config: dict | None) -> None:
        normalized = self._normalize_config(config) if isinstance(config, dict) else None
        with self._config_lock:
            if normalized == self._runtime_config:
                return
            self._runtime_config = normalized
        with self._cache_lock:
            self._cache = None
            self._cache_at = 0.0

    @staticmethod
    def _normalize_config(config: dict | None) -> dict:
        raw = config or {}
        api_base = str(raw.get("api_base") or COMMAND_CODE_API_BASE or "https://api.commandcode.ai").strip().rstrip("/")
        timeout = _number(raw.get("timeout_seconds"))
        refresh = _number(raw.get("refresh_interval_seconds"))
        return {
            "api_key": str(raw.get("api_key") or "").strip(),
            "api_base": api_base,
            "org_id": str(raw.get("org_id") or "").strip(),
            "timeout_seconds": min(max(timeout or COMMAND_CODE_TIMEOUT_SECONDS, 3.0), 60.0),
            "refresh_interval_seconds": int(min(max(refresh or DEFAULT_REFRESH_SECONDS, 15), 3600)),
        }

    def _effective_config(self, config: dict | None = None) -> dict:
        base = self._normalize_config({
            "api_key": COMMAND_CODE_API_KEY,
            "api_base": COMMAND_CODE_API_BASE or "https://api.commandcode.ai",
            "timeout_seconds": COMMAND_CODE_TIMEOUT_SECONDS,
            "refresh_interval_seconds": DEFAULT_REFRESH_SECONDS,
        })
        raw = config if isinstance(config, dict) else self._config_snapshot()
        if isinstance(raw, dict):
            base.update(self._normalize_config(raw))
        return base

    def configured(self) -> bool:
        config = self._effective_config()
        return bool(config["api_key"] and config["api_base"])

    def config_summary(self) -> dict:
        config = self._effective_config()
        module_config = self._config_snapshot()
        return {
            "configured": self.configured(),
            "source": "module" if module_config else "environment",
            "api_key_masked": _mask_secret(config["api_key"]),
            "api_base": config["api_base"],
            "org_id": config["org_id"],
            "timeout_seconds": config["timeout_seconds"],
            "refresh_interval_seconds": config["refresh_interval_seconds"],
        }

    @staticmethod
    def _validate_api_base(value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise CommandCodeUsageError("API Base 必须是 HTTP(S) 地址")
        if parsed.username or parsed.password:
            raise CommandCodeUsageError("API Base 不能包含账号密码")
        return value.rstrip("/")

    def _request_json(self, endpoint: str, query: dict | None = None, config: dict | None = None) -> dict:
        effective = self._effective_config(config)
        if not effective["api_key"]:
            raise CommandCodeUsageError("Command Code API Key 未配置")
        api_base = self._validate_api_base(effective["api_base"])
        try:
            response = requests.get(
                f"{api_base}{endpoint}",
                params={key: value for key, value in (query or {}).items() if value not in (None, "")},
                headers={
                    "Authorization": f"Bearer {effective['api_key']}",
                    "Accept": "application/json",
                    "User-Agent": "OpsCenter-CommandCode-Monitor/1.0",
                },
                timeout=effective["timeout_seconds"],
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise CommandCodeUsageError("Command Code 接口请求超时") from exc
        except requests.RequestException as exc:
            raise CommandCodeUsageError(self._safe_error(exc, effective["api_key"])) from exc

        if response.status_code == 401:
            raise CommandCodeUsageError("Command Code API Key 无效或已过期")
        if response.status_code == 403:
            raise CommandCodeUsageError("Command Code API Key 无权访问用量接口")
        if response.status_code == 404:
            raise CommandCodeUsageError("Command Code 用量接口不存在或已变更")
        if response.status_code == 429:
            raise CommandCodeUsageError("Command Code 用量接口请求过于频繁")
        if response.status_code >= 300:
            raise CommandCodeUsageError(f"Command Code 用量接口返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise CommandCodeUsageError("Command Code 用量接口返回了非 JSON 数据") from exc
        if not isinstance(payload, dict):
            raise CommandCodeUsageError("Command Code 用量接口响应格式不合法")
        return payload

    @staticmethod
    def _safe_error(exc: Exception, secret: str = "") -> str:
        message = str(exc).strip() or exc.__class__.__name__
        if secret:
            message = message.replace(secret, "***")
        return message[:300]

    def _safe_call(self, endpoint: str, query: dict, config: dict) -> tuple[dict | None, str]:
        try:
            return self._request_json(endpoint, query, config), ""
        except CommandCodeUsageError as exc:
            return None, str(exc)

    @staticmethod
    def _subscription(payload: dict | None) -> dict:
        if not isinstance(payload, dict):
            return {}
        data = payload.get("data")
        if isinstance(data, list):
            data = data[0] if data else None
        return data if isinstance(data, dict) else {}

    def _normalize_usage(
        self,
        whoami: dict,
        credits_payload: dict,
        subscriptions_payload: dict,
        summary_payload: dict,
        errors: list[str],
        latency_ms: float,
        config: dict,
    ) -> dict:
        credits = credits_payload.get("credits") if isinstance(credits_payload.get("credits"), dict) else {}
        window_limits = (
            credits_payload.get("windowLimits")
            if isinstance(credits_payload.get("windowLimits"), dict)
            else {}
        )
        subscription = self._subscription(subscriptions_payload)
        summary = summary_payload if isinstance(summary_payload, dict) else {}

        plan_id = str(subscription.get("planId") or credits.get("planId") or "").strip()
        plan = _plan_info(plan_id)
        monthly_remaining = _number(credits.get("monthlyCredits"))
        purchased_remaining = _number(credits.get("purchasedCredits"))
        free_remaining = _number(credits.get("freeCredits"))
        has_balance = all(value is not None for value in (monthly_remaining, purchased_remaining, free_remaining))
        remaining_total = monthly_remaining + purchased_remaining + free_remaining if has_balance else None
        total_spent = _number(summary.get("totalCredits", summary.get("totalCost")))
        monthly_cap = plan.get("monthly_credits") if subscription.get("status") == "active" else None
        total_pool = (
            max(float(monthly_cap), monthly_remaining) + purchased_remaining + free_remaining
            if monthly_cap is not None and has_balance else None
        )
        usage_percent = min(max(((total_pool - remaining_total) / total_pool * 100), 0.0), 100.0) if total_pool is not None and total_pool > 0 else None

        def window(name: str) -> dict:
            raw = window_limits.get(name) if isinstance(window_limits.get(name), dict) else {}
            used = _number(raw.get("used"))
            cap = _number(raw.get("cap"))
            return {
                "used": used,
                "cap": cap,
                "remaining": max(0.0, cap - used) if used is not None and cap is not None else None,
                "percent": min(max((used / cap * 100), 0.0), 100.0) if used is not None and cap is not None and cap > 0 else None,
                "reset_at": _iso_from_epoch(raw.get("resetAt")),
                "exceeded": bool(raw.get("exceeded") or window_limits.get("exceeded")),
            }

        user = whoami.get("user") if isinstance(whoami.get("user"), dict) else {}
        org = whoami.get("org") if isinstance(whoami.get("org"), dict) else None
        return {
            "configured": True,
            "available": True,
            "source": "module" if self._config_snapshot() else "environment",
            "api_base": config["api_base"],
            "latency_ms": round(latency_ms, 1),
            "error": "；".join(dict.fromkeys(error for error in errors if error))[:500],
            "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "account": {
                "user_name": str(user.get("userName") or user.get("name") or "").strip(),
                "email_masked": _mask_email(user.get("email")),
                "user_id_masked": _mask_identifier(user.get("id")),
            },
            "org": {
                "login": str(org.get("login") or "").strip() if org else "",
                "id_masked": _mask_identifier(org.get("id")) if org else "",
            },
            "plan": {
                "id": plan_id,
                "name": plan["name"],
                "monthly_credits": plan["monthly_credits"],
                "status": str(subscription.get("status") or "").strip(),
                "current_period_start": subscription.get("currentPeriodStart"),
                "current_period_end": subscription.get("currentPeriodEnd"),
                "cancel_at_period_end": bool(subscription.get("cancelAtPeriodEnd")),
            },
            "credits": {
                "monthly_remaining": monthly_remaining,
                "purchased_remaining": purchased_remaining,
                "free_remaining": free_remaining,
                "remaining_total": remaining_total,
                "total_spent": total_spent,
                "total_pool": total_pool,
                "usage_percent": usage_percent,
            },
            "windows": {
                "limited": bool(window_limits.get("limited")),
                "five_hour": window("fiveHour"),
                "weekly": window("weekly"),
            },
            "usage": {
                "period_basis": str(summary.get("periodBasis") or "").strip(),
                "total_count": int(_non_negative(summary.get("totalCount"))),
                "completed_count": int(_non_negative(summary.get("completedCount"))),
                "failed_count": int(_non_negative(summary.get("failedCount"))),
                "success_rate": _non_negative(summary.get("successRate")),
                "average_cost": _non_negative(summary.get("averageCost")),
                "total_cost": _non_negative(summary.get("totalCost")),
                "total_credits": _non_negative(summary.get("totalCredits")),
                "total_tokens": int(_non_negative(summary.get("totalTokens"))),
                "total_tokens_in": int(_non_negative(summary.get("totalTokensIn"))),
                "total_tokens_out": int(_non_negative(summary.get("totalTokensOut"))),
            },
        }

    @staticmethod
    def _snapshot_scope(whoami: dict) -> str:
        user = whoami.get("user") if isinstance(whoami.get("user"), dict) else {}
        org = whoami.get("org") if isinstance(whoami.get("org"), dict) else {}
        identity = str(org.get("id") or user.get("id") or "command-code").strip()
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    def _record_snapshot(self, result: dict, scope_key: str) -> None:
        now = datetime.utcnow()
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        credits = result.get("credits") if isinstance(result.get("credits"), dict) else {}
        windows = result.get("windows") if isinstance(result.get("windows"), dict) else {}
        plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
        with get_db() as db:
            latest = (
                db.query(CommandCodeUsageSnapshot)
                .filter(CommandCodeUsageSnapshot.scope_key == scope_key)
                .order_by(CommandCodeUsageSnapshot.captured_at.desc())
                .first()
            )
            if latest and latest.captured_at and (now - latest.captured_at).total_seconds() < SNAPSHOT_MIN_INTERVAL_SECONDS:
                return
            five_hour = windows.get("five_hour") if isinstance(windows.get("five_hour"), dict) else {}
            weekly = windows.get("weekly") if isinstance(windows.get("weekly"), dict) else {}
            db.add(CommandCodeUsageSnapshot(
                scope_key=scope_key,
                captured_at=now,
                plan_id=str(plan.get("id") or "")[:80] or None,
                period_start=str(plan.get("current_period_start") or "")[:40] or None,
                total_credits=_number(usage.get("total_credits")),
                total_cost=_number(usage.get("total_cost")),
                total_tokens=int(_non_negative(usage.get("total_tokens"))) if usage.get("total_tokens") is not None else None,
                total_tokens_in=int(_non_negative(usage.get("total_tokens_in"))) if usage.get("total_tokens_in") is not None else None,
                total_tokens_out=int(_non_negative(usage.get("total_tokens_out"))) if usage.get("total_tokens_out") is not None else None,
                total_count=int(_non_negative(usage.get("total_count"))) if usage.get("total_count") is not None else None,
                completed_count=int(_non_negative(usage.get("completed_count"))) if usage.get("completed_count") is not None else None,
                failed_count=int(_non_negative(usage.get("failed_count"))) if usage.get("failed_count") is not None else None,
                monthly_remaining=_number(credits.get("remaining_total")),
                five_hour_used=_number(five_hour.get("used")),
                weekly_used=_number(weekly.get("used")),
            ))
            db.commit()

    @staticmethod
    def _history_window(baseline: CommandCodeUsageSnapshot | None, result: dict, current_at: datetime, seconds: int) -> dict:
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
        current_values = {
            "total_credits": _number(usage.get("total_credits")),
            "total_tokens": _number(usage.get("total_tokens")),
            "total_count": _number(usage.get("total_count")),
        }
        # Snapshot timestamps are stored as naive UTC. Normalize the current
        # timestamp before comparing so aware and naive values never mix.
        current_utc = current_at.astimezone(timezone.utc).replace(tzinfo=None) if current_at.tzinfo else current_at
        start_at = current_utc - timedelta(seconds=seconds)
        if baseline and baseline.captured_at < start_at:
            baseline = None
        can_diff = bool(
            baseline
            and plan.get("current_period_start")
            and str(baseline.period_start or "") == str(plan.get("current_period_start") or "")
        )

        def delta(field: str, integer: bool = False):
            if not can_diff:
                return None
            before = _number(getattr(baseline, field))
            after = current_values.get(field)
            if before is None or after is None or after < before:
                return None
            value = after - before
            return int(value) if integer else value

        observed_seconds = 0
        if baseline and baseline.captured_at:
            observed_seconds = max(0, int((current_utc - baseline.captured_at).total_seconds()))
        complete = bool(
            delta("total_credits") is not None
            and baseline
            and baseline.captured_at <= start_at + timedelta(seconds=90)
        )
        return {
            "source": "local_snapshots",
            "unavailable": not bool(plan.get("current_period_start")),
            "complete": complete,
            "since": baseline.captured_at.isoformat() + "Z" if baseline and baseline.captured_at else None,
            "observed_seconds": observed_seconds,
            "credits": delta("total_credits"),
            "requests": delta("total_count", integer=True),
            "tokens": delta("total_tokens", integer=True),
        }

    def _build_history(self, result: dict, scope_key: str, now: datetime | None = None) -> dict:
        current_at = now or _parse_iso(result.get("checked_at")) or datetime.now(timezone.utc)
        if current_at.tzinfo is None:
            current_at = current_at.replace(tzinfo=timezone.utc)
        current_naive = current_at.astimezone(timezone.utc).replace(tzinfo=None)
        oldest = current_naive - timedelta(days=HISTORY_RETENTION_DAYS)
        with get_db() as db:
            def first_since(start):
                return (
                    db.query(CommandCodeUsageSnapshot)
                    .filter(
                        CommandCodeUsageSnapshot.scope_key == scope_key,
                        CommandCodeUsageSnapshot.captured_at >= start,
                        CommandCodeUsageSnapshot.captured_at < current_naive,
                    )
                    .order_by(CommandCodeUsageSnapshot.captured_at.asc())
                    .first()
                )

            oldest_row = first_since(oldest)
            baselines = {name: first_since(current_naive - delta) for name, delta in HISTORY_WINDOWS.items()}
        plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        observed_since = oldest_row.captured_at.isoformat() + "Z" if oldest_row else None
        windows = {
            name: self._history_window(baselines[name], result, current_at, int(delta.total_seconds()))
            for name, delta in HISTORY_WINDOWS.items()
        }
        windows["month"] = {
            "source": "billing_period",
            "complete": bool(plan.get("current_period_start")),
            "since": plan.get("current_period_start"),
            "observed_seconds": None,
            "credits": _number(usage.get("total_credits")),
            "requests": int(_non_negative(usage.get("total_count"))),
            "tokens": int(_non_negative(usage.get("total_tokens"))),
        }
        return {
            "available": True,
            "source": "local_snapshots",
            "sampled_at": current_at.isoformat().replace("+00:00", "Z"),
            "observed_since": observed_since,
            "windows": windows,
        }

    def _decorate_history(self, result: dict, scope_key: str) -> None:
        scope_key = self.history_scope or scope_key
        try:
            self._record_snapshot(result, scope_key)
            result["history"] = self._build_history(result, scope_key)
        except Exception:
            result["history"] = {
                "available": False,
                "source": "local_snapshots",
                "error": "本地历史快照暂不可用",
                "windows": {},
            }

    def snapshot_status(self, min_captured_at: datetime | None = None) -> dict | None:
        """Serve the latest sample while the upstream collector refreshes in the background."""
        with get_db() as db:
            query = db.query(CommandCodeUsageSnapshot)
            query = query.filter(CommandCodeUsageSnapshot.scope_key == self.history_scope) if self.history_scope else query.filter(func.length(CommandCodeUsageSnapshot.scope_key) == 32)
            row = query.order_by(CommandCodeUsageSnapshot.captured_at.desc()).first()
            if not row or (min_captured_at and row.captured_at < min_captured_at):
                return None
            sample = {name: getattr(row, name) for name in (
                "scope_key", "captured_at", "plan_id", "period_start", "total_credits",
                "total_cost", "total_tokens", "total_tokens_in", "total_tokens_out",
                "total_count", "completed_count", "failed_count", "monthly_remaining",
                "five_hour_used", "weekly_used",
            )}
        checked_at = sample["captured_at"].replace(tzinfo=timezone.utc)
        count = sample["total_count"] or 0
        result = {
            "configured": True, "available": True, "snapshot_only": True, "stale": True,
            "source": "module" if self._config_snapshot() else "environment",
            "api_base": self._effective_config()["api_base"], "latency_ms": None,
            "error": "当前显示上次采样；实时采集仍在后台进行",
            "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
            "plan": {"id": sample["plan_id"], "name": _plan_info(sample["plan_id"] or "")["name"],
                     "current_period_start": sample["period_start"]},
            "credits": {"remaining_total": sample["monthly_remaining"]},
            "windows": {"five_hour": {"used": sample["five_hour_used"]},
                        "weekly": {"used": sample["weekly_used"]}},
            "usage": {
                "total_count": count, "completed_count": sample["completed_count"],
                "failed_count": sample["failed_count"], "total_cost": sample["total_cost"],
                "total_credits": sample["total_credits"], "total_tokens": sample["total_tokens"],
                "total_tokens_in": sample["total_tokens_in"], "total_tokens_out": sample["total_tokens_out"],
                "average_cost": sample["total_cost"] / count if count and sample["total_cost"] is not None else None,
                "success_rate": sample["completed_count"] * 100 / count if count and sample["completed_count"] is not None else None,
            },
        }
        result["history"] = self._build_history(result, sample["scope_key"], checked_at)
        return result

    def _fetch_usage(self, config: dict, record_history: bool = True) -> dict:

        started = time.perf_counter()
        errors: list[str] = []
        whoami, error = self._safe_call("/alpha/whoami", {"limits": 1}, config)
        if whoami is None:
            return {
                "configured": self.configured(),
                "available": False,
                "source": "module" if self._config_snapshot() else "environment",
                "api_base": config["api_base"],
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": error or "Command Code 账号信息读取失败",
                "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        if error:
            errors.append(error)

        org = whoami.get("org") if isinstance(whoami.get("org"), dict) else {}
        org_id = config.get("org_id") or str(org.get("id") or "").strip()
        credits, credits_error = self._safe_call("/alpha/billing/credits", {"orgId": org_id}, config)
        if credits_error:
            errors.append(credits_error)
        subscriptions, subscription_error = self._safe_call(
            "/alpha/billing/subscriptions", {"orgId": org_id}, config,
        )
        if subscription_error:
            errors.append(subscription_error)

        subscription = self._subscription(subscriptions)
        since = subscription.get("currentPeriodStart")
        summary, summary_error = self._safe_call(
            "/alpha/usage/summary",
            {"orgId": org_id, "since": since},
            config,
        )
        if summary_error:
            errors.append(summary_error)

        result = self._normalize_usage(
            whoami,
            credits or {},
            subscriptions or {},
            summary or {},
            errors,
            (time.perf_counter() - started) * 1000,
            config,
        )
        if record_history:
            self._decorate_history(result, self._snapshot_scope(whoami))
        return result

    def fetch_usage(self, force: bool = False, config: dict | None = None,
                    min_captured_at: datetime | None = None) -> dict:
        effective = self._effective_config(config)
        if not effective["api_key"]:
            return {
                "configured": False,
                "available": False,
                "source": "environment",
                "api_base": effective["api_base"],
                "latency_ms": None,
                "error": "Command Code API Key 未配置",
                "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        if config is not None:
            return self._fetch_usage(effective)

        with self._cache_lock:
            if not force and self._cache is not None:
                cached = copy.deepcopy(self._cache)
                if time.monotonic() - self._cache_at > effective["refresh_interval_seconds"] * 2:
                    cached["error"] = "最近一次采集尚未更新，当前显示上次成功数据"
                    cached["stale"] = True
                return cached
        if not force and min_captured_at is not None:
            snapshot = self.snapshot_status(min_captured_at)
            if snapshot is not None:
                return snapshot
        result = self._fetch_usage(effective)
        if result.get("available"):
            with self._cache_lock:
                self._cache = copy.deepcopy(result)
                self._cache_at = time.monotonic()
        return result

    def status(self, force: bool = False, min_captured_at: datetime | None = None) -> dict:
        try:
            return self.fetch_usage(force=force, min_captured_at=min_captured_at)
        except CommandCodeUsageError as exc:
            return {
                "configured": self.configured(),
                "available": False,
                "source": "module" if self._config_snapshot() else "environment",
                "api_base": self._effective_config()["api_base"],
                "latency_ms": None,
                "error": str(exc),
                "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }

    def validate_config(self, config: dict) -> dict:
        normalized = self._normalize_config(config)
        if not normalized["api_key"]:
            raise CommandCodeUsageError("Command Code API Key 不能为空")
        normalized["api_base"] = self._validate_api_base(normalized["api_base"])
        result = self._fetch_usage(normalized, record_history=False)
        if not result.get("available"):
            raise CommandCodeUsageError(result.get("error") or "Command Code 凭据验证失败")
        if result.get("error"):
            raise CommandCodeUsageError(result["error"])
        return result


command_code = CommandCodeUsageMonitor()
