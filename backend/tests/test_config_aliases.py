from app.config import Settings


def test_auth_settings_accept_documented_ops_prefix(monkeypatch):
    monkeypatch.setenv("OPS_AUTH_ENABLED", "true")
    monkeypatch.setenv("OPS_JWT_SECRET", "j" * 32)
    monkeypatch.setenv("OPS_ADMIN_USER", "operator")
    monkeypatch.setenv("OPS_ADMIN_PASSWORD", "secret")

    settings = Settings()
    assert settings.auth_enabled is True
    assert settings.jwt_secret == "j" * 32
    assert settings.admin_user == "operator"
    assert settings.admin_password == "secret"
