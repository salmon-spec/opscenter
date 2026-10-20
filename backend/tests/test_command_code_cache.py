import pytest
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import app.services.command_code as command_code_module
from app.services.command_code import CommandCodeUsageMonitor, _iso_from_epoch


def test_stale_cache_returns_without_network(monkeypatch):
    monitor = CommandCodeUsageMonitor()
    monitor._cache = {"available": True, "checked_at": "2026-10-16T00:00:00Z"}
    monitor._cache_at = 1
    monkeypatch.setattr(monitor, "_effective_config", lambda _: {"api_key": "test", "refresh_interval_seconds": 60})
    monkeypatch.setattr(monitor, "_fetch_usage", lambda _: pytest.fail("unexpected network request"))
    result = monitor.fetch_usage()
    assert result["available"] and result["stale"]
    assert _iso_from_epoch(0) is None


def test_persisted_sample_avoids_upstream_when_cache_is_empty(monkeypatch):
    monitor = CommandCodeUsageMonitor()
    sample = SimpleNamespace(**{
        "scope_key": "current", "captured_at": datetime(2026, 10, 16, 8, 0),
        "plan_id": "individual-goat", "period_start": "2026-09-17T06:06:00Z",
        "total_credits": 12.5, "total_cost": 12.5, "total_tokens": 900,
        "total_tokens_in": 800, "total_tokens_out": 100, "total_count": 10,
        "completed_count": 9, "failed_count": 1, "monthly_remaining": 57.5,
        "five_hour_used": 1.0, "weekly_used": 2.0,
    })
    class Query:
        def filter(self, *_): return self
        def order_by(self, *_): return self
        def first(self): return sample
    class Db:
        def query(self, *_): return Query()
    @contextmanager
    def db():
        yield Db()
    monkeypatch.setattr(command_code_module, "get_db", db)
    monkeypatch.setattr(monitor, "_build_history", lambda *_: {"available": True})
    monkeypatch.setattr(monitor, "_effective_config", lambda *_: {"api_key": "test", "api_base": "https://example.com", "refresh_interval_seconds": 60})
    monkeypatch.setattr(monitor, "_fetch_usage", lambda *_: pytest.fail("unexpected network request"))
    result = monitor.fetch_usage(min_captured_at=datetime(2026, 10, 16, 7, 0))
    assert result["snapshot_only"] and result["usage"]["total_count"] == 10
    assert result["usage"]["success_rate"] == 90
    assert monitor.snapshot_status(datetime(2026, 10, 16, 9, 0)) is None
