from fastapi import Depends
from runtime.app import create_module_app, require_internal_service
from runtime.database import database_ready

from collector import findings_provider, snapshot, summary_provider

app = create_module_app(
    key="k3s",
    version="0.3.0",
    summary_provider=summary_provider,
    findings_provider=findings_provider,
    readiness_provider=database_ready,
    capabilities=["summary:read", "findings:read"],
)


@app.get('/api/v1/internal/alert-targets', dependencies=[Depends(require_internal_service)])
def alert_targets():
    return snapshot.alert_targets()


@app.on_event("startup")
def start_snapshot():
    snapshot.start()


@app.on_event("shutdown")
def stop_snapshot():
    snapshot.stop()
