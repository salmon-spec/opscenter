"""Static safety checks for the Loki/Alloy deployment bundle."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_compose_keeps_loki_private_and_collects_local_host_logs():
    compose = (ROOT / "deploy/observability/docker-compose.yml").read_text(encoding="utf-8")
    assert "${LOKI_BIND_IP:-10.66.66.5}:3100:3100" in compose
    assert "grafana/loki:3.7.2" in compose
    assert "grafana/alloy:v${ALLOY_VERSION:-1.18.0}" in compose
    assert "${LOKI_DATA_DIR:-/opt/opscenter-data/loki}:/loki" in compose
    assert "OPSCENTER_SERVER_ID: ${OPSCENTER_SERVER_ID:?" in compose
    assert "/var/log/journal:/var/log/journal:ro" in compose
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in compose
    assert "condition: service_healthy" in compose


def test_loki_retention_and_installer_guards_are_present():
    loki = (ROOT / "deploy/observability/loki.yml").read_text(encoding="utf-8")
    installer = (ROOT / "deploy/observability/install.sh").read_text(encoding="utf-8")
    assert "retention_enabled: true" in loki
    assert "retention_period: 8760h" in loki
    assert "max_query_lookback: 8760h" in loki
    assert "compose config" in installer
    assert "command -v docker-compose" in installer
    assert "OPSCENTER_SERVER_ID is not a valid UUID" in installer
    assert "Loki did not become ready" in installer
    assert "LOKI_DATA_DIR must use an approved dedicated path" in installer
