import json
from datetime import datetime, timezone, timedelta
import main


def test_today_counts_all_audit_rows_and_uses_china_date(tmp_path, monkeypatch):
    path = tmp_path/'audit.jsonl'
    now = datetime.now(timezone(timedelta(hours=8)))
    records = [{'at': now.astimezone(timezone.utc).isoformat(), 'ok': True} for _ in range(250)]
    records += [{'at': now.isoformat(), 'ok': False}, {'at': (now-timedelta(days=1)).isoformat(), 'ok': True}]
    path.write_text('\n'.join(json.dumps(row) for row in records)+'\ninvalid\n', encoding='utf-8')
    monkeypatch.setattr(main, 'AUDIT_PATH', path)
    data = main.summary_provider()['data']
    assert data['sent_today'] == 250 and data['failed_today'] == 1 and data['audit_rows'] == 252
