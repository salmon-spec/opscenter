"""Multiple usage accounts, stored in the existing encrypted module config."""

from __future__ import annotations

import copy
import math
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func

from app.database import get_db
from app.models import CommandCodeUsageSnapshot
from app.services.command_code import (
    CommandCodeUsageError, CommandCodeUsageMonitor, _mask_secret, _number, command_code,
)

PROVIDERS = {
    "command-code": {"name": "Command Code", "api_base": "https://api.commandcode.ai"},
    "opencode-go": {"name": "OpenCode Go", "api_base": "https://opencode.ai/zen/go/v1"},
    "deepseek": {"name": "DeepSeek", "api_base": "https://api.deepseek.com"},
}


class UsageAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field("", max_length=64, pattern=r"^[a-zA-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=60)
    provider: Literal["command-code", "opencode-go", "deepseek"]
    api_key: str = Field("", max_length=1024)
    api_base: str = Field("", max_length=500)
    org_id: str = Field("", max_length=256)
    refresh_interval_seconds: int = Field(60, ge=15, le=3600)
    timeout_seconds: float = Field(12, ge=3, le=60)


class UsageAccounts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accounts: list[UsageAccount] = Field(min_length=1, max_length=10)
    active_account_id: str = Field("", max_length=64)
    display_account_ids: list[str] = Field(default_factory=list, max_length=10)


def _finite(value):
    parsed = _number(value)
    return parsed if parsed is not None and math.isfinite(parsed) else None


class ProviderUsageMonitor(CommandCodeUsageMonitor):
    def __init__(self, provider: str):
        super().__init__()
        self.provider = provider

    def snapshot_status(self, min_captured_at=None):
        # These upstreams expose balance/quota, not cumulative request counters.
        return None

    def _fetch_usage(self, config, record_history=True):
        started = time.perf_counter()
        payload = self._request_json("/user/balance" if self.provider == "deepseek" else "/usage", config=config)
        result = {
            "configured": True, "available": True, "source": "module", "error": "",
            "api_base": config["api_base"], "provider": self.provider,
            "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        if self.provider == "deepseek":
            balances = payload.get("balance_infos")
            if not isinstance(payload.get("is_available"), bool) or not isinstance(balances, list) or not balances:
                raise CommandCodeUsageError("DeepSeek 余额响应格式已变更")
            result["balances"] = []
            for row in balances:
                if not isinstance(row, dict) or row.get("currency") not in {"CNY", "USD"}:
                    raise CommandCodeUsageError("DeepSeek 余额币种无法识别")
                values = {key: _finite(row.get(key)) for key in ("total_balance", "granted_balance", "topped_up_balance")}
                if any(value is None for value in values.values()):
                    raise CommandCodeUsageError("DeepSeek 余额响应缺少有效金额")
                result["balances"].append({"currency": row["currency"], **values})
            result["can_use_api"] = payload["is_available"]
            result["capability_note"] = "官方接口提供账户余额；请求数、Token 和账期成本未开放。"
            return result

        usage = payload.get("usage", payload)
        if not isinstance(usage, dict):
            raise CommandCodeUsageError("OpenCode Go 用量响应格式已变更")
        windows = {}
        for key, aliases in {"five_hour": ("rolling", "rollingUsage"), "weekly": ("weekly", "weeklyUsage"), "monthly": ("monthly", "monthlyUsage")}.items():
            row = next((usage[name] for name in aliases if isinstance(usage.get(name), dict)), {})
            percent = _finite(row.get("percent", row.get("usagePercent")))
            if percent is None or percent < 0:
                raise CommandCodeUsageError("OpenCode Go 响应缺少有效额度百分比")
            reset_at = row.get("resetAt") or row.get("resetsAt")
            seconds = _finite(row.get("resetInSec"))
            if not reset_at and seconds is not None:
                reset_at = (datetime.now(timezone.utc) + timedelta(seconds=max(0, seconds))).isoformat().replace("+00:00", "Z")
            windows[key] = {"percent": percent, "resets_at": reset_at, "status": str(row.get("status") or ""), "used": None, "cap": None}
        result.update(windows=windows, use_balance=payload.get("useBalance"),
                      capability_note="官方额度使用率；账户余额、请求数与 Token 未在此接口提供。")
        return result

    def _request_json(self, endpoint, query=None, config=None):
        try:
            return super()._request_json(endpoint, query, config)
        except CommandCodeUsageError as exc:
            raise CommandCodeUsageError(str(exc).replace("Command Code", PROVIDERS[self.provider]["name"])) from None


class ModelUsageAccounts:
    def __init__(self):
        self._lock = threading.RLock()
        self._config = None
        self._monitors = {}
        self._sample_at = {}

    def _accounts(self, config=None):
        raw = config if config is not None else self._config
        if raw and "accounts" in raw:
            return copy.deepcopy(raw)
        legacy = command_code._effective_config(raw)
        return {"accounts": [{**legacy, "id": "legacy", "name": "Command Code", "provider": "command-code"}],
                "active_account_id": "legacy", "display_account_ids": ["legacy"]}

    def configure(self, config):
        with self._lock:
            self._config = copy.deepcopy(config)
            if not config or "accounts" not in config:
                command_code.configure(config)

    def configured(self):
        with self._lock:
            return any(a.get("api_key") for a in self._accounts()["accounts"])

    def config_summary(self):
        with self._lock:
            config = self._accounts()
            accounts = [{**{k: v for k, v in row.items() if k not in {"api_key", "history_scope"}},
                         "api_key_masked": _mask_secret(row.get("api_key", "")), "configured": bool(row.get("api_key"))}
                        for row in config["accounts"]]
            active = next(a for a in accounts if a["id"] == config["active_account_id"])
            return {**active, "accounts": accounts, "active_account_id": active["id"],
                    "display_account_ids": config["display_account_ids"], "providers": PROVIDERS,
                    "source": "module" if self._config else "environment"}

    def prepare_config(self, data, previous):
        try:
            parsed = UsageAccounts.model_validate(data)
        except ValidationError as exc:
            raise CommandCodeUsageError(str(exc.errors()[0]["msg"])) from None
        previous_accounts = {a["id"]: a for a in self._accounts(previous)["accounts"]}
        accounts = []
        for item in parsed.accounts:
            row = item.model_dump()
            row["id"] = row["id"] or uuid.uuid4().hex
            old = previous_accounts.get(row["id"], {})
            row["api_base"] = row["api_base"] or PROVIDERS[row["provider"]]["api_base"]
            row["api_base"] = CommandCodeUsageMonitor._validate_api_base(row["api_base"])
            from urllib.parse import urlparse
            url = urlparse(row["api_base"])
            if url.query or url.fragment:
                raise CommandCodeUsageError("API Base 不能包含查询参数或片段")
            if not row["api_key"]:
                if old.get("provider") != row["provider"] or old.get("api_base") != row["api_base"]:
                    raise CommandCodeUsageError("新增账户或更换厂商/API 地址时必须重新填写 API Key")
                row["api_key"] = old.get("api_key", "")
            if len(row["api_key"]) < 8:
                raise CommandCodeUsageError("API Key 至少需要 8 个字符")
            # Credential/endpoint changes start a new scope to avoid mixing account history.
            changed = any(old.get(k) != row[k] for k in ("api_key", "api_base", "org_id", "provider"))
            row["history_scope"] = (old.get("history_scope") if not changed else None) or "account:" + (uuid.uuid4().hex if old and changed else row["id"])
            if row["id"] == "legacy" and not changed and not old.get("history_scope"):
                # Adopt only pre-multi-account history; never another account's samples.
                with get_db() as db:
                    latest = db.query(CommandCodeUsageSnapshot).filter(func.length(CommandCodeUsageSnapshot.scope_key) == 32).order_by(CommandCodeUsageSnapshot.captured_at.desc()).first()
                    if latest:
                        row["history_scope"] = latest.scope_key
            if changed:
                self._monitor(row).validate_config(row)
            accounts.append(row)
        ids = [a["id"] for a in accounts]
        active = parsed.active_account_id or ids[0]
        shown = parsed.display_account_ids or [active]
        if len(set(ids)) != len(ids) or active not in ids or any(i not in ids for i in shown):
            raise CommandCodeUsageError("账户重复或默认/展示账户不存在")
        return {"accounts": accounts, "active_account_id": active,
                "display_account_ids": list(dict.fromkeys([active, *shown]))}

    def _monitor(self, account):
        if account["id"] == "legacy" and account["provider"] == "command-code" and (not self._config or "accounts" not in self._config):
            return command_code
        ident = account["id"]
        signature = {key: account.get(key) for key in ("provider", "api_key", "api_base", "org_id", "timeout_seconds", "refresh_interval_seconds", "history_scope")}
        with self._lock:
            entry = self._monitors.get(ident)
            if entry and entry[0] == signature:
                return entry[1]
            monitor = CommandCodeUsageMonitor() if account["provider"] == "command-code" else ProviderUsageMonitor(account["provider"])
            monitor.configure(account)
            monitor.history_scope = account.get("history_scope") or "account:" + ident
            self._monitors[ident] = (signature, monitor)
            self._sample_at.pop(ident, None)
            return monitor

    def status(self, force=False, min_captured_at=None, account_id=None):
        with self._lock:
            config = self._accounts()
            ident = account_id or config["active_account_id"]
            account = next((a for a in config["accounts"] if a["id"] == ident), None)
            if not account:
                raise CommandCodeUsageError("账户不存在，请刷新配置")
            monitor = self._monitor(account)
        snapshot_since = datetime.min if monitor.history_scope else min_captured_at
        result = monitor.status(force=force, min_captured_at=snapshot_since)
        return {**result, "account_id": ident, "account_name": account["name"], "provider": account["provider"]}

    def sample_all(self):
        with self._lock:
            config = self._accounts()
        def sample(account):
            ident = account["id"]
            monitor = self._monitor(account)
            now = time.monotonic()
            due = now - self._sample_at.get(ident, 0) >= account["refresh_interval_seconds"]
            result = monitor.status(force=due)
            if due:
                self._sample_at[ident] = now
            return ident, {**result, "account_id": ident, "account_name": account["name"], "provider": account["provider"]}
        with ThreadPoolExecutor(max_workers=min(4, len(config["accounts"]))) as pool:
            results = dict(pool.map(sample, config["accounts"]))
        return results[config["active_account_id"]]


model_usage = ModelUsageAccounts()
