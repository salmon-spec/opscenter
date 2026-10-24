from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest
import main
from storage import CatalogStore
from test_main import FakeStore


def test_unsupported_notification_policy_is_not_silently_accepted():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        main.ServiceProfileUpdate(probe_notifications_enabled=True)


def test_source_refresh_failure_still_runs_saved_catalog_probes(monkeypatch):
    calls = []
    class Stop(Exception): pass
    def fail(): raise RuntimeError('source offline')
    def stop(_): raise Stop()
    monkeypatch.setattr(main, 'refresh_source_snapshot', fail)
    monkeypatch.setattr(main, 'run_probe_cycle', lambda: calls.append('probe'))
    monkeypatch.setattr(main.time, 'monotonic', lambda: 100)
    monkeypatch.setattr(main.time, 'sleep', stop)
    with pytest.raises(Stop): main.probe_loop()
    assert calls == ['probe']


def test_one_broken_target_does_not_discard_good_results(monkeypatch):
    saved = []
    class Store(FakeStore):
        def save_probe_result(self, key, result): saved.append(key)
    monkeypatch.setattr(main, 'CatalogStore', Store)
    monkeypatch.setattr(main, 'services_snapshot', lambda **kwargs: ([{'key':'bad'}, {'key':'good'}], datetime.now(timezone.utc)))
    def probe(service):
        if service['key'] == 'bad': raise ValueError('invalid URL')
        return {'status':'up'}
    monkeypatch.setattr(main, 'probe_service', probe)
    assert main.run_probe_cycle() == 1 and saved == ['good']


def test_uptime_uses_lifetime_collection_start_instead_of_recent_first_row(monkeypatch):
    now = datetime.now(timezone.utc)
    class Store(FakeStore):
        def probe_summaries(self, hours):
            return {'wiki': {'checked_at':now, 'first_checked_at':now-timedelta(days=7),
                             'status':'down', 'checks':1440, 'uptime_percent':0, 'avg_latency_ms':10}}
    store = Store({'services':([{'key':'wiki', 'uptime_percent':99.99}], now-timedelta(days=7))})
    monkeypatch.setattr(main, 'CatalogStore', lambda:store)
    result = main.module_health_overview(24)
    assert result['items'][0]['uptime_percent'] == 0
    assert result['catalog_stale'] is True and result['stale'] is False
    assert result['uptime_history_pending'] is False
    # Verify that the lifetime start query is not truncated to the rolling window.
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchall.return_value = []
    real = CatalogStore()
    real.connect = lambda: connection
    real.probe_summaries()
    sql = connection.__enter__.return_value.execute.call_args.args[0]
    assert 'origin.service_key=recent.service_key' in sql
    assert 'min(checked_at) AS first_checked_at' not in sql
