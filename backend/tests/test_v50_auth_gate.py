"""v5.0.0 鉴权门禁测试（BE-6）。

单元级验证 app.auth 的 check_operator_token / require_operator，
路由级验证 v5.0.0 加装鉴权依赖的既有路由（k8s_monitor / server_details）。

运行（backend 目录）：
    New-Item -ItemType Directory -Force -Path .tmp
    $env:DATABASE_URL = "sqlite:///./.tmp/test-v50-auth.db"
    python -m pytest tests/test_v50_auth_gate.py -v
"""

import uuid

import pytest
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

import app.auth as auth
from app.auth import (
    check_operator_token,
    get_current_user,
    require_configured_operator,
    require_operator,
)
from app.database import Base, engine
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


# ── 单元级：app.auth ──────────────────────────────────────────

def test_unconfigured_operator_token_allows(monkeypatch):
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "")
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    assert check_operator_token(None) is True
    assert require_operator(None) is not None


def test_configured_operator_token_enforced(monkeypatch):
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "s3cret")
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    assert check_operator_token(None) is False
    assert check_operator_token("wrong") is False
    assert check_operator_token("s3cret") is True

    with pytest.raises(HTTPException) as excinfo:
        require_operator(None)
    assert excinfo.value.status_code == 401

    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="s3cret")
    assert require_operator(credentials) is not None
    with pytest.raises(HTTPException):
        get_current_user(None)
    assert get_current_user(credentials) is not None


def test_configured_operator_dependency_fails_closed(monkeypatch):
    monkeypatch.setattr(auth, "MONITOR_ALLOW_ANONYMOUS", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "")
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    with pytest.raises(HTTPException) as excinfo:
        require_configured_operator(None)
    assert excinfo.value.status_code == 503

    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "s3cret")
    with pytest.raises(HTTPException) as excinfo:
        require_configured_operator(None)
    assert excinfo.value.status_code == 401
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="s3cret")
    assert require_configured_operator(credentials) is not None


def test_monitor_anonymous_override_allows_loginless_test_environment(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "")
    monkeypatch.setattr(auth, "MONITOR_ALLOW_ANONYMOUS", True)
    assert require_configured_operator(None).username == "admin"


def test_auth_enabled_requires_credentials(monkeypatch):
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "s3cret")
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)

    with pytest.raises(HTTPException) as excinfo:
        get_current_user(None)
    assert excinfo.value.status_code == 401

    with pytest.raises(HTTPException) as excinfo:
        require_operator(None)
    assert excinfo.value.status_code == 401


# ── 路由级：既有路由已挂载鉴权依赖 ────────────────────────────

def test_k8s_clusters_requires_operator_token(monkeypatch):
    import app.k8s_monitor  # noqa: F401  确保路由模块已加载

    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "s3cret")
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr("app.k8s_client.get_client", lambda: None)

    assert client.get("/api/v2/clusters").status_code == 401

    response = client.get("/api/v2/clusters", headers={"Authorization": "Bearer s3cret"})
    assert response.status_code != 401


def test_server_overview_requires_operator_token(monkeypatch):
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "s3cret")
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)

    server_id = uuid.uuid4()
    assert client.get(f"/api/v2/servers/{server_id}/overview").status_code == 401

    response = client.get(
        f"/api/v2/servers/{server_id}/overview",
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code != 401


def test_mutation_middleware_covers_legacy_write_routes(monkeypatch):
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "s3cret")
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)

    response = client.post("/api/v2/health-check")

    assert response.status_code == 401
    assert response.json()["detail"] == "缺少或无效的运维令牌"

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "SECRET_KEY", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setattr(auth, "ADMIN_USER", "admin")
    token = auth.create_access_token(1, "admin")
    authorized = client.post(
        "/api/v2/servers", json={}, headers={"Authorization": f"Bearer {token}"},
    )
    assert authorized.status_code == 422


def test_host_monitor_token_is_read_only_and_scoped_to_monitoring(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "SECRET_KEY", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setattr(auth, "ADMIN_USER", "admin")
    monkeypatch.setattr(auth, "METRICS_READER_TOKEN", "metrics-reader-only")

    headers = {"Authorization": "Bearer metrics-reader-only"}
    metrics = client.get("/api/v2/metrics/hosts/overview", headers=headers)
    summary_path = f"/api/v2/servers/{uuid.uuid4()}/system/summary"
    anonymous_summary = client.get(summary_path)
    summary = client.get(summary_path, headers=headers)
    mutation = client.post("/api/v2/servers", json={}, headers=headers)

    assert metrics.status_code == 200
    assert anonymous_summary.status_code == 401
    assert summary.status_code == 404  # authorized, but the host does not exist
    assert mutation.status_code == 401


def test_builtin_admin_login_works_without_user_table(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "SECRET_KEY", "test-jwt-secret")
    monkeypatch.setattr(auth, "ADMIN_USER", "admin")
    monkeypatch.setattr(auth, "ADMIN_PASSWORD", "correct-password")

    denied = client.post("/api/v2/auth/login", json={"username": "admin", "password": "wrong"})
    assert denied.status_code == 401
    response = client.post(
        "/api/v2/auth/login", json={"username": "admin", "password": "correct-password"},
    )

    assert response.status_code == 200
    token = response.json()["access_token"]
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert get_current_user(credentials).username == "admin"


def test_asymmetric_session_signing_and_legacy_window(monkeypatch, tmp_path):
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    private_path.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public_path.write_bytes(private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setattr(auth, "PRIVATE_KEY_FILE", str(private_path))
    monkeypatch.setattr(auth, "PUBLIC_KEY_FILE", str(public_path))
    monkeypatch.setattr(auth, "SECRET_KEY", "old-key")
    monkeypatch.setattr(auth, "LEGACY_UNTIL", "2020-01-01T00:00:00Z")
    token = auth.create_access_token(1, "admin")
    assert jwt.get_unverified_header(token)["alg"] == "EdDSA"
    assert auth.decode_token(token)["role"] == "admin"
    old = jwt.encode({"sub": "1", "username": "admin", "exp": 4102444800}, "old-key", algorithm="HS256")
    assert auth.decode_token(old) is None


def test_jwt_read_endpoints_without_api_keys(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "SECRET_KEY", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setattr(auth, "ADMIN_USER", "admin")
    token = auth.create_access_token(1, "admin")
    for path in ("/api/v2/services/health", "/api/v2/screen/summary"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert client.get(path, headers={"Authorization": "Bearer invalid"}).status_code == 401

    assert client.get("/api/v2/keys").status_code == 404

    assert client.get("/api/v2/auth/me").status_code == 401
    assert client.get("/api/v2/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["username"] == "admin"
    assert client.get("/api/v2/ai/summary", headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_builtin_admin_login_is_rate_limited(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "SECRET_KEY", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setattr(auth, "ADMIN_USER", "admin")
    monkeypatch.setattr(auth, "ADMIN_PASSWORD", "correct-password")
    monkeypatch.setattr(auth, "_LOGIN_FAILURES", {})

    for _ in range(5):
        assert client.post("/api/v2/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.post("/api/v2/auth/login", json={"username": "admin", "password": "correct-password"}).status_code == 429


def test_operator_token_can_unlock_browser_login(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "operator-secret")

    response = client.post(
        "/api/v2/auth/login", json={"username": "admin", "password": "operator-secret"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"] == "operator-secret"


def test_auth_refuses_to_sign_with_missing_secret(monkeypatch):
    monkeypatch.setattr(auth, "SECRET_KEY", "")
    with pytest.raises(RuntimeError):
        auth.create_access_token(1, "admin")
    assert auth.decode_token("anything") is None


def test_websocket_token_uses_jwt_when_auth_enabled(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "SECRET_KEY", "test-jwt-secret")
    monkeypatch.setattr(auth, "ADMIN_USER", "admin")
    token = auth.create_access_token(1, "admin")

    assert auth.check_access_token(token) is True
    assert auth.check_access_token("wrong") is False
