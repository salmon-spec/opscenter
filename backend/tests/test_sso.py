"""v3.29 T5 SSO 测试：登录/登出/会话 + forward-auth + OIDC 全链路。

不依赖 PostgreSQL，TestClient 自组装 app 即可运行。
"""

import os
import json
from urllib.parse import parse_qs

os.environ.setdefault("OIDC_ISSUER", "http://127.0.0.1:9091")
os.environ.setdefault("OPS_AUTH_ENABLED", "false")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.sso import router as sso_router  # noqa: E402
from app import sso  # noqa: E402


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(sso_router)
    return app


app = _make_app()
# follow_redirects=False：authorize 的 302 目标为外部回调地址，测试不应跟随
client = TestClient(app, follow_redirects=False)


def _login():
    return client.post("/api/v2/auth/login", json={"username": "admin", "password": "OpsCenter@2026"})


# ── 登录 / 登出 / 会话 ──

def test_login_success_sets_cookie():
    r = _login()
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "ops_session" in r.cookies


def test_login_wrong_password():
    r = client.post("/api/v2/auth/login", json={"username": "admin", "password": "bad"})
    assert r.status_code == 401


def test_me_with_cookie():
    _login()
    r = client.get("/api/v2/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    assert r.json()["role"] == "admin"


def test_me_without_cookie():
    c = TestClient(_make_app(), follow_redirects=False)
    assert c.get("/api/v2/auth/me").status_code == 401


def test_logout_clears_cookie():
    _login()
    assert client.get("/api/v2/auth/me").status_code == 200
    assert client.post("/api/v2/auth/logout").status_code == 200
    assert client.get("/api/v2/auth/me").status_code == 401


def test_account_switch_ends_keycloak_session_and_clears_cookie():
    _login()
    r = client.get("/api/v2/sso/account-switch")
    assert r.status_code == 302
    assert r.headers["location"].startswith("http://10.66.66.6:8180/realms/ops/protocol/openid-connect/logout?")
    assert "client_id=opscenter" in r.headers["location"]
    assert "post_logout_redirect_uri=http%3A%2F%2F10.66.66.5%2F" in r.headers["location"]
    assert "ops_session=" in r.headers.get("set-cookie", "")


def test_reset_targets_are_controlled_and_contain_no_secrets():
    r = client.get("/api/v2/sso/reset-targets")
    assert r.status_code == 200
    targets = r.json()["targets"]
    assert [target["name"] for target in targets] == [
        "Gitea", "GitLab", "Jenkins", "Grafana", "PVE", "Nexus", "SonarQube"
    ]
    assert all(target["url"].startswith(("http://10.66.66.", "https://10.66.66.")) for target in targets)
    serialized = json.dumps(targets).lower()
    assert "password" not in serialized
    assert "token" not in serialized
    assert "secret" not in serialized


def test_pve_redirect_uri_has_no_trailing_slash(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"data": "http://10.66.66.6/authorize"}).encode()

    def fake_urlopen(request, **_kwargs):
        captured["body"] = parse_qs(request.data.decode())
        return FakeResponse()

    monkeypatch.setattr(sso, "urlopen", fake_urlopen)
    r = client.get("/api/v2/sso/pve")
    assert r.status_code == 302
    assert captured["body"]["redirect-url"] == ["https://10.66.66.3:8006"]


# ── Caddy forward_auth ──

def test_forward_auth_with_cookie():
    _login()
    r = client.get("/api/v2/auth/forward-auth")
    assert r.status_code == 200
    assert r.headers.get("Remote-User") == "admin"
    assert r.headers.get("Remote-Email") == "admin@sso.local"


def test_forward_auth_without_cookie():
    c = TestClient(_make_app(), follow_redirects=False)
    assert c.get("/api/v2/auth/forward-auth").status_code == 401


# ── OIDC Discovery / JWKS ──

def test_oidc_discovery():
    r = client.get("/api/v2/oidc/.well-known/openid-configuration")
    assert r.status_code == 200
    d = r.json()
    assert d["issuer"] == "http://127.0.0.1:9091"
    assert d["authorization_endpoint"].endswith("/api/v2/oidc/authorize")
    assert d["id_token_signing_alg_values_supported"] == ["HS256"]


def test_oidc_jwks():
    r = client.get("/api/v2/oidc/.well-known/jwks.json")
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert keys[0]["kty"] == "oct"
    assert keys[0]["alg"] == "HS256"


# ── OIDC 全链路 ──

def test_oidc_full_flow():
    _login()
    r = client.get(
        "/api/v2/oidc/authorize",
        params={
            "response_type": "code",
            "client_id": "grafana",
            "redirect_uri": "http://ops.salmon.xin:3000/login/generic_oauth",
            "scope": "openid profile email",
            "state": "xyz",
            "nonce": "n1",
        },
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert "code=" in loc
    assert "state=xyz" in loc
    code = loc.split("code=")[1].split("&")[0]

    r2 = client.post(
        "/api/v2/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "grafana",
            "redirect_uri": "http://ops.salmon.xin:3000/login/generic_oauth",
        },
    )
    assert r2.status_code == 200
    tok = r2.json()
    assert tok["token_type"] == "Bearer"
    assert tok["access_token"]
    assert tok["id_token"]

    r3 = client.get(
        "/api/v2/oidc/userinfo",
        headers={"Authorization": f"Bearer {tok['access_token']}"},
    )
    assert r3.status_code == 200
    assert r3.json()["sub"] == "admin"
    assert r3.json()["preferred_username"] == "admin"


def test_oidc_authorize_requires_login():
    c = TestClient(_make_app(), follow_redirects=False)
    r = c.get(
        "/api/v2/oidc/authorize",
        params={"response_type": "code", "client_id": "g", "redirect_uri": "http://x/cb"},
    )
    assert r.status_code == 302
    assert r.headers["location"].startswith("/#/login")


def test_oidc_code_single_use():
    _login()
    r = client.get(
        "/api/v2/oidc/authorize",
        params={"response_type": "code", "client_id": "g", "redirect_uri": "http://x/cb"},
    )
    code = r.headers["location"].split("code=")[1].split("&")[0]
    data = {"grant_type": "authorization_code", "code": code, "client_id": "g", "redirect_uri": "http://x/cb"}
    assert client.post("/api/v2/oidc/token", data=data).status_code == 200
    assert client.post("/api/v2/oidc/token", data=data).status_code == 400


def test_oidc_token_bad_grant():
    assert client.post(
        "/api/v2/oidc/token",
        data={"grant_type": "password", "code": "x"},
    ).status_code == 400
