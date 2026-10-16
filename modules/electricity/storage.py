"""Electricity module owned storage: session, snapshot and alert state.

The module keeps its own data directory (a dedicated volume) and never reads
or writes another module's database or the legacy backend.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


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

    def alert_state(self) -> dict:
        return read_json(self.alerts_path, {}) or {}

    def save_alert_state(self, value: dict) -> None:
        save_json(self.alerts_path, value)

    # ---------- readiness ----------
    def ping(self) -> bool:
        """Readiness: data directory must be writable. Collection results are
        reported through summary/findings instead of failing the probe."""
        self.dir.mkdir(parents=True, exist_ok=True)
        probe = self.dir / ".ready"
        probe.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        return True
