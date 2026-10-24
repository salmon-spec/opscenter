from datetime import datetime, timezone
import jwt

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from modules.runtime.app import create_module_app
from modules.runtime.auth import ModuleAuth


def test_signed_module_session_is_local_and_health_stays_public(monkeypatch, tmp_path):
    private_key = Ed25519PrivateKey.generate()
    public_path = tmp_path / "public.pem"
    public_path.write_bytes(private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv("OPS_JWT_PUBLIC_KEY_FILE", str(public_path))
    monkeypatch.delenv("OPS_JWT_SECRET", raising=False)
    monkeypatch.setenv("MODULE_AUTH_REQUIRED", "true")
    app = create_module_app("sample", "1.0.0", lambda: {"status": "ok", "data": {}}, lambda: {"items": []})
    client = TestClient(app)
    claims = {"sub": "1", "username": "admin", "role": "viewer", "iss": "opscenter",
              "aud": "opscenter-modules", "exp": datetime(2099, 1, 1, tzinfo=timezone.utc)}
    token = jwt.encode(claims, private_key, algorithm="EdDSA")
    assert client.get("/health/live").status_code == 200
    assert client.get("/api/v1/summary").status_code == 401
    assert client.get("/api/v1/summary", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get("/api/v1/summary", headers={"Authorization": f"Bearer {jwt.encode({**claims, 'aud': 'wrong'}, private_key, algorithm='EdDSA')}"}).status_code == 401
    assert client.get("/api/v1/summary", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_workbench_token_authorizes_module_without_module_login(monkeypatch):
    monkeypatch.setenv("OPS_JWT_SECRET", "test-secret-with-at-least-32-characters")
    monkeypatch.setenv("OPS_ADMIN_USER", "admin")
    auth = ModuleAuth(lambda: None)
    app = FastAPI()
    auth.register(app)
    @app.post("/write")
    def write(user=Depends(app.state.require_editor)):
        return {"actor": user["username"]}
    client = TestClient(app)
    token = jwt.encode({"sub": "1", "username": "admin", "exp": datetime(2099, 1, 1, tzinfo=timezone.utc)}, "test-secret-with-at-least-32-characters", algorithm="HS256")
    assert client.post("/write").status_code == 401
    assert client.post("/write", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.post("/write", headers={"Authorization": f"Bearer {token}"}).json() == {"actor": "admin"}
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "x"}).status_code == 404
    assert client.post("/api/v1/auth/users", json={"username": "admin", "password": "x", "role": "admin"}).status_code == 404


def test_module_auth_fails_closed_without_workbench_secret(monkeypatch):
    monkeypatch.delenv("OPS_JWT_SECRET", raising=False)
    monkeypatch.setenv("OPS_ADMIN_USER", "admin")
    auth = ModuleAuth(lambda: None)
    app = FastAPI()
    auth.register(app)

    @app.get("/write")
    def write(user=Depends(app.state.require_editor)):
        return user

    client = TestClient(app)
    token = jwt.encode({"sub": "1", "username": "admin", "exp": datetime(2099, 1, 1, tzinfo=timezone.utc)}, "test-secret-with-at-least-32-characters", algorithm="HS256")
    assert client.get("/write", headers={"Authorization": f"Bearer {token}"}).status_code == 503


def test_module_gateway_requires_workbench_session():
    from pathlib import Path

    caddyfile = Path(__file__).resolve().parents[3] / "deploy" / "docker" / "Caddyfile.test"
    config = caddyfile.read_text(encoding="utf-8")
    compose = (caddyfile.parent / "compose.vm1-test.yaml").read_text(encoding="utf-8")
    assert "forward_auth @moduleRequests" not in config
    assert "handle_path /modules/k3s/*" in config
    for block in config.split("handle_path /modules/")[1:]:
        assert "respond @internalProbe 404" in block.split("reverse_proxy", 1)[0]
    assert 'MODULE_AUTH_REQUIRED: "true"' in compose
    assert 'OPS_AUTH_ENABLED: "true"' in compose


def test_module_contract_reports_summary_and_findings():
    now = datetime.now(timezone.utc).isoformat()
    app = create_module_app(
        "sample",
        "1.0.0",
        lambda: {"status": "ok", "collected_at": now, "data": {"ready": 2}},
        lambda: {
            "status": "ok",
            "collected_at": now,
            "items": [{
                "id": "sample:node-1:down",
                "resource_id": "node-1",
                "severity": "critical",
                "title": "节点不可用",
                "observed_at": now,
            }],
        },
        ["summary:read", "findings:read"],
    )
    client = TestClient(app)

    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").json()["version"] == "1.0.0"
    assert client.get("/api/v1/module").json()["capabilities"] == ["summary:read", "findings:read"]
    assert client.get("/api/v1/version").json() == {"key": "sample", "version": "1.0.0"}
    assert 'opscenter_module_info{module_key="sample"' in client.get("/metrics").text
    assert client.get("/api/v1/summary").json()["data"] == {"ready": 2}
    assert client.get("/api/v1/findings").json()["items"][0]["id"] == "sample:node-1:down"


def test_internal_module_reads_require_dedicated_token(monkeypatch, tmp_path):
    token_path = tmp_path / "module-token"
    token_path.write_text("internal-test-token", encoding="utf-8")
    monkeypatch.setenv("OPS_MODULE_INTERNAL_TOKEN_FILE", str(token_path))
    monkeypatch.setenv("MODULE_AUTH_REQUIRED", "true")
    app = create_module_app("sample", "1.0.0", lambda: {"status": "ok", "data": {"n": 1}},
                            lambda: {"status": "ok", "items": []})
    client = TestClient(app)

    assert client.get("/api/v1/internal/summary").status_code == 401
    assert client.get("/api/v1/internal/summary", headers={"X-OpsCenter-Module-Token": "wrong"}).status_code == 401
    assert client.get("/api/v1/internal/summary", headers={"X-OpsCenter-Module-Token": "internal-test-token"}).json()["data"] == {"n": 1}
    assert client.get("/api/v1/internal/findings", headers={"X-OpsCenter-Module-Token": "internal-test-token"}).status_code == 200
    assert client.get("/api/v1/summary").status_code == 401


def test_provider_failure_is_module_local_and_does_not_leak_exception():
    def fail():
        raise RuntimeError("credential=do-not-return")

    client = TestClient(create_module_app("sample", "1.0.0", fail, lambda: {"items": []}))
    response = client.get("/api/v1/summary")

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert "do-not-return" not in response.text
    assert client.get("/health/live").status_code == 200


def test_readiness_dependency_failure_is_safe_and_module_local():
    def unavailable():
        raise RuntimeError("private connection details")

    client = TestClient(create_module_app("sample", "1.0.0", lambda: {}, lambda: {}, readiness_provider=unavailable))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert "private connection details" not in response.text
    assert client.get("/health/live").status_code == 200
