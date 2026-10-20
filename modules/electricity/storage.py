"""Electricity module owned storage: session, snapshot, alert state, histories.

The module keeps its own data directory (a dedicated volume) and never reads
or writes another module's database or the legacy backend.

Layout under ``ELECTRICITY_DATA_DIR`` (default ``/data``)::

    snapshot.json            latest collection snapshot (overwritten)
    alerts.json              alert state machine + silences + balance observations
    alert_history.jsonl      notification audit (append-only, rotated)
    daily_history.jsonl      per-published-day kWh (append-only, deduped on read)
    monthly_history.jsonl    per-month kWh / cost (append-only, deduped on read)
    raw/                     captured upstream payloads (opt-in, redacted)
    notify.json              notification targets (people / groups) + settings
    web_session.json         State Grid web session
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))

# Keys whose values are identity-bearing and must never be persisted in raw dumps.
SENSITIVE_KEY = re.compile(
    r"(token|secret|password|passwd|acctid|userid|user_id|consno|cons_no|phone|mobile|"
    r"loginaccount|nickname|name|addr|idcard|cert|session|cookie)",
    re.IGNORECASE,
)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def append_jsonl(path: Path, records: list[dict]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        pass  # histories are best-effort; alerting/collection must not fail


def read_jsonl(path: Path, limit: int = 200) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict] = []
    for line in lines[-max(1, min(limit, 5000)):]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            records.append(item)
    records.reverse()
    return records


def rotate(path: Path, max_bytes: int, keep: int = 3) -> bool:
    """Rotate ``path`` into ``.1 ... .keep`` when it exceeds ``max_bytes``."""
    try:
        if path.stat().st_size <= max_bytes:
            return False
    except OSError:
        return False
    try:
        for index in range(keep, 0, -1):
            source = path if index == 1 else path.with_suffix(path.suffix + f".{index - 1}")
            target = path.with_suffix(path.suffix + f".{index}")
            if source.exists():
                os.replace(source, target)
        path.touch()
        os.chmod(path, 0o600)
        return True
    except OSError:
        return False


def redact(value, depth: int = 0):
    """Keep the shape, mask identity-bearing scalars. Never raises."""
    if depth > 12:
        return "..."
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if SENSITIVE_KEY.search(str(key)) and isinstance(item, (str, int, float)):
                text = str(item)
                result[key] = (text[:2] + "***" + text[-2:]) if len(text) > 6 else "***"
            else:
                result[key] = redact(item, depth + 1)
        return result
    if isinstance(value, list):
        return [redact(item, depth + 1) for item in value[:80]]
    return value


def describe_shape(payload, depth: int = 0, path: str = "") -> dict:
    """Structural summary of a payload: keys, types, list lengths, example paths."""
    if depth > 4:
        return {"path": path, "type": "truncated"}
    if isinstance(payload, dict):
        return {
            "path": path, "type": "object", "keys": list(payload.keys())[:40],
            "children": [describe_shape(value, depth + 1, f"{path}.{key}") for key, value in list(payload.items())[:20]],
        }
    if isinstance(payload, list):
        return {
            "path": path, "type": "list", "length": len(payload),
            "children": [describe_shape(payload[0], depth + 1, f"{path}[0]")] if payload else [],
        }
    return {"path": path, "type": type(payload).__name__}


class SessionStore:
    """Persist the State Grid web session so restarts do not force a new login."""

    def __init__(self, path: Path):
        self.path = path

    async def async_load(self):
        return read_json(self.path, {}) or {}

    async def async_save(self, value):
        save_json(self.path, value)


class ElectricityStore:
    def __init__(self) -> None:
        self.dir = Path(os.getenv("ELECTRICITY_DATA_DIR", "/data"))
        self.snapshot_path = self.dir / "snapshot.json"
        self.alerts_path = self.dir / "alerts.json"
        self.history_path = self.dir / "alert_history.jsonl"
        self.daily_history_path = self.dir / "daily_history.jsonl"
        self.monthly_history_path = self.dir / "monthly_history.jsonl"
        self.raw_dir = self.dir / "raw"
        self.notify_path = self.dir / "notify.json"
        self.session_path = self.dir / "web_session.json"

    # ---------- credentials (injected by the deployment, never written to disk) ----------
    def credentials(self) -> dict:
        raw_meter = os.getenv("ELECTRICITY_TARGET_METER", "").strip()
        try:
            meter = json.loads(raw_meter) if raw_meter else {}
        except ValueError:
            meter = {}
        return {
            "account": (os.getenv("ELECTRICITY_ACCOUNT") or "").strip(),
            "password_digest": (os.getenv("ELECTRICITY_PASSWORD_DIGEST") or "").strip(),
            "target_id": (os.getenv("ELECTRICITY_TARGET_ID") or "").strip(),
            "target_meter": meter if isinstance(meter, dict) else {},
        }

    def configured(self) -> bool:
        creds = self.credentials()
        meter = creds["target_meter"]
        return bool(
            creds["account"]
            and creds["password_digest"]
            and creds["target_id"]
            and str(meter.get("consNo_dst") or "") == creds["target_id"]
        )

    # ---------- snapshot ----------
    def snapshot(self) -> dict:
        return read_json(self.snapshot_path, {}) or {}

    def save_snapshot(self, value: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        save_json(self.snapshot_path, value)

    # ---------- alert state ----------
    def alert_state(self) -> dict:
        return read_json(self.alerts_path, {}) or {}

    def save_alert_state(self, value: dict) -> None:
        save_json(self.alerts_path, value)

    # ---------- alert history (rotated) ----------
    def append_alert_history(self, records: list[dict]) -> None:
        self.rotate_alerts()
        append_jsonl(self.history_path, records)

    def alert_history(self, limit: int = 50) -> list[dict]:
        return read_jsonl(self.history_path, limit)

    def rotate_alerts(self) -> bool:
        return rotate(self.history_path,
                      int(os.getenv("ELECTRICITY_HISTORY_MAX_BYTES", "2000000") or 2_000_000),
                      int(os.getenv("ELECTRICITY_HISTORY_KEEP", "3") or 3))

    # ---------- long term histories (append-only, deduped on read) ----------
    def append_daily_history(self, rows: list[dict]) -> None:
        append_jsonl(self.daily_history_path, rows)

    def daily_history(self, days: int = 120) -> list[dict]:
        """Newest first, one row per published day (later collection wins)."""
        seen: dict[str, dict] = {}
        for row in read_jsonl(self.daily_history_path, 5000):  # already newest-first
            day = str(row.get("day") or "")
            if day and day not in seen:
                seen[day] = row
        ordered = sorted(seen.values(), key=lambda row: str(row.get("day")), reverse=True)
        return ordered[:days]

    def append_monthly_history(self, rows: list[dict]) -> None:
        append_jsonl(self.monthly_history_path, rows)

    def monthly_history(self, months: int = 24) -> list[dict]:
        seen: dict[str, dict] = {}
        for row in read_jsonl(self.monthly_history_path, 5000):
            month = str(row.get("month") or "")
            if month and month not in seen:
                seen[month] = row
        ordered = sorted(seen.values(), key=lambda row: str(row.get("month")), reverse=True)
        return ordered[:months]

    def trim_history(self) -> dict:
        """Drop history rows older than the configured retention windows."""
        removed = {"daily": 0, "monthly": 0}
        daily_days = int(os.getenv("ELECTRICITY_DAILY_HISTORY_DAYS", "730") or 730)
        cutoff = (datetime.now(TZ) - timedelta(days=daily_days)).date().isoformat()
        removed["daily"] = self._trim_by_key(self.daily_history_path, "day", cutoff)
        monthly_days = int(os.getenv("ELECTRICITY_MONTHLY_HISTORY_DAYS", str(365 * 10)) or 3650)
        month_cutoff = (datetime.now(TZ) - timedelta(days=monthly_days)).strftime("%Y%m")
        removed["monthly"] = self._trim_by_key(self.monthly_history_path, "month", month_cutoff)
        return removed

    @staticmethod
    def _trim_by_key(path: Path, key: str, cutoff: str) -> int:
        if not path.exists() or not cutoff:
            return 0
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        kept: list[str] = []
        dropped = 0
        for line in lines:
            try:
                value = str(json.loads(line).get(key) or "")
            except ValueError:
                dropped += 1
                continue
            if value and value >= cutoff:
                kept.append(line)
            else:
                dropped += 1
        if not dropped:
            return 0
        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError:
            return 0
        return dropped

    # ---------- notification targets (people / groups) ----------
    def notify_config(self) -> dict:
        value = read_json(self.notify_path, {}) or {}
        targets = value.get("targets")
        if not isinstance(targets, list):
            targets = []
        return {"targets": [item for item in targets if isinstance(item, dict)],
                "settings": value.get("settings") if isinstance(value.get("settings"), dict) else {}}

    def save_notify_config(self, value: dict) -> None:
        save_json(self.notify_path, {
            "targets": [item for item in (value.get("targets") or []) if isinstance(item, dict)],
            "settings": value.get("settings") or {},
        })

    def notify_targets(self, enabled_only: bool = False) -> list[dict]:
        targets = self.notify_config()["targets"]
        if enabled_only:
            targets = [item for item in targets if item.get("enabled", True)]
        return targets

    # ---------- raw payload capture (opt-in, redacted) ----------
    def raw_capture_enabled(self) -> bool:
        return (os.getenv("ELECTRICITY_RAW_CAPTURE", "") or "").lower() in {"1", "true", "yes", "on"}

    def save_raw(self, api: str, request, response) -> str | None:
        """Store a redacted request/response pair; returns the file name."""
        if not self.raw_capture_enabled():
            return None
        slug = re.sub(r"[^a-z0-9]+", "-", str(api).lower()).strip("-")[-40:] or "api"
        stamp = datetime.now(TZ).strftime("%Y%m%dT%H%M%S")
        name = f"{slug}-{stamp}.json"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        save_json(self.raw_dir / name, {
            "api": api,
            "captured_at": datetime.now(TZ).isoformat(timespec="seconds"),
            "request": redact(request),
            "response": redact(response),
        })
        self._trim_raw()
        return name

    def _trim_raw(self) -> None:
        keep = int(os.getenv("ELECTRICITY_RAW_KEEP", "40") or 40)
        try:
            files = sorted(self.raw_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        except OSError:
            return
        for stale in files[keep:]:
            try:
                stale.unlink()
            except OSError:
                pass

    def list_raw(self, limit: int = 40) -> list[dict]:
        if not self.raw_dir.exists():
            return []
        items = []
        try:
            files = sorted(self.raw_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        except OSError:
            return []
        for path in files[:limit]:
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append({"name": path.name, "bytes": stat.st_size,
                          "modified": datetime.fromtimestamp(stat.st_mtime, TZ).isoformat(timespec="seconds")})
        return items

    def read_raw(self, name: str) -> dict | None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}\.json", str(name or "")):
            return None
        path = self.raw_dir / name
        if not path.exists():
            return None
        return read_json(path, None)

    def raw_shape(self, name: str) -> dict | None:
        payload = self.read_raw(name)
        if payload is None:
            return None
        return {"name": name, "shape": describe_shape(payload.get("response"))}

    # ---------- readiness ----------
    def ping(self) -> bool:
        """Readiness: data directory must be writable. Collection results are
        reported through summary/findings instead of failing the probe."""
        self.dir.mkdir(parents=True, exist_ok=True)
        probe = self.dir / ".ready"
        probe.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        return True
