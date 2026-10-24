from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest
from agent import normalize
from storage import MonitorStore, _thin


@pytest.mark.parametrize('data', [{}, {'cpu_percent': None}, {'cpu_percent': float('nan')},
                                  {'cpu_percent': 101}, {'cpu_percent': 0, 'timestamp': 0}])
def test_invalid_samples_do_not_produce_fresh_zero_history(data):
    with pytest.raises(ValueError):
        normalize(data, 'host', datetime.now(timezone.utc))


def test_partial_sample_keeps_real_zero_and_omits_missing_resources():
    summary, metrics = normalize({'cpu_percent': 0}, 'host', datetime.now(timezone.utc))
    assert metrics['cpu'] == 0 and 'memory' not in metrics and summary['partial'] is True
    assert 'net_rx' not in metrics and 'net_tx' not in metrics
    _, zero = normalize({'cpu_percent': 0, 'net_rx_rate': 0, 'network_summary': {'rx_bytes_per_sec': 123}}, 'host', datetime.now(timezone.utc))
    assert zero['net_rx'] == 0


def test_rollups_have_bounded_rebuild_windows_and_retention_is_opt_in(monkeypatch):
    connection = MagicMock()
    store = MonitorStore()
    store.connect = lambda: connection
    store.build_rollups()
    calls = [call for call in connection.__enter__.return_value.execute.call_args_list if 'INSERT INTO' in call.args[0]]
    assert len(calls) == 2
    assert 'timestamp >=' in calls[0].args[0]
    assert 47.99 < (datetime.now(timezone.utc).replace(tzinfo=None) - calls[0].args[1][0]).total_seconds()/3600 < 48.01
    assert 'bucket_at >=' in calls[1].args[0]
    connection.reset_mock()
    for key in ('HOST_MONITOR_RAW_RETENTION_DAYS', 'HOST_MONITOR_5M_RETENTION_DAYS', 'HOST_MONITOR_1H_RETENTION_DAYS'):
        monkeypatch.delenv(key, raising=False)
    store.prune_history()
    connection.__enter__.return_value.execute.assert_not_called()
    monkeypatch.setenv('HOST_MONITOR_RAW_RETENTION_DAYS', '30')
    store.prune_history()
    assert 'LIMIT 5000' in connection.__enter__.return_value.execute.call_args.args[0]


def test_thinning_keeps_latest_sample():
    values = _thin(list(range(3000)))
    assert values[-1] == 2999 and len(values) == 2500
