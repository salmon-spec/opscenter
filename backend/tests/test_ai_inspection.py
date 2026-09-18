from __future__ import annotations

import app.ai_inspection as inspection


def test_auto_inspection_deduplicates_and_keeps_dry_run(monkeypatch):
    row = {
        "key": "demo-service",
        "name": "Demo Service",
        "status": "down",
        "last_error_code": "timeout",
        "last_error": "upstream timeout",
        "consecutive_failures": 3,
        "active_incident_id": "incident-1",
    }
    monkeypatch.setattr(inspection, "AI_OPS_AUTOCHECK_ENABLED", True)
    monkeypatch.setattr(inspection, "AI_OPS_AUTOCHECK_MAX_PER_CYCLE", 3)
    monkeypatch.setattr(inspection, "AI_OPS_AUTOCHECK_COOLDOWN_SECONDS", 900)
    monkeypatch.setattr(inspection, "_service_rows", lambda: [row])
    monkeypatch.setattr(
        inspection,
        "run_ai_analysis",
        lambda *args, **kwargs: {
            "analysis": {
                "conclusion": "超时，需要继续观察",
                "recommended_actions": [{"action": "observe"}],
            },
            "context": {"summary": {"service_count": 1}},
            "model": "deepseek/deepseek-v4.1-flash",
        },
    )
    with inspection._state_lock:
        inspection._records.clear()
        inspection._last_dispatch.clear()

    assert inspection.run_auto_inspection_cycle() == 1
    assert inspection.run_auto_inspection_cycle() == 0
    item = inspection.list_inspections(1)[0]
    assert item["status"] == "completed"
    assert item["trigger"] == "automatic"
    assert item["analysis"]["conclusion"]

