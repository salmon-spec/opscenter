"""Editable service-plaza profile and credential security contracts."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.credential_crypto import decrypt_secret
from app.main import Base, SessionLocal, app, engine
from app.models import PlazaServiceProfile


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def add_host(host="10.66.66.4"):
    response = client.post("/api/v2/servers", json={
        "name": "测试主机", "host": host, "ssh_port": 22, "auto_deploy_agent": False,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_catalog_profile_can_be_edited_and_password_is_encrypted():
    server_id = add_host()
    response = client.put("/api/v2/services/plaza/gitea", json={
        "server_id": server_id, "name": "内部 Gitea", "description": "研发代码托管",
        "category": "代码与CI/CD", "entry_url": "http://10.66.66.4:3000/",
        "health_url": "http://10.66.66.4:3000/api/healthz", "username": "dev-admin",
        "password": "plaza-secret", "login_notes": "使用本地账号登录",
        "owner": "研发平台组", "tags": ["代码", "核心"],
    })
    assert response.status_code == 200, response.text
    assert "plaza-secret" not in response.text

    with SessionLocal() as db:
        profile = db.query(PlazaServiceProfile).filter(PlazaServiceProfile.plaza_key == "gitea").first()
        assert profile.secret_ciphertext and "plaza-secret" not in profile.secret_ciphertext
        assert decrypt_secret(profile.secret_ciphertext) == "plaza-secret"

    detail_response = client.get("/api/v2/services/plaza/gitea/detail")
    detail = detail_response.json()
    assert detail_response.status_code == 200
    assert detail["name"] == "内部 Gitea"
    assert detail["server"]["id"] == server_id
    assert detail["credential_username"] == "dev-admin"
    assert detail["has_credentials"] is True
    assert detail["owner"] == "研发平台组"
    assert detail["tags"] == ["代码", "核心"]
    assert "password" not in detail and "secret_ciphertext" not in detail

    reveal = client.post("/api/v2/services/plaza/gitea/credentials/reveal")
    assert reveal.status_code == 200
    assert reveal.headers["cache-control"].startswith("no-store")
    assert reveal.json() == {"username": "dev-admin", "password": "plaza-secret"}


def test_blank_password_preserves_existing_and_explicit_clear_removes_it():
    add_host()
    assert client.put("/api/v2/services/plaza/gitea", json={"password": "keep-me"}).status_code == 200
    assert client.put("/api/v2/services/plaza/gitea", json={"description": "changed", "password": ""}).status_code == 200
    assert client.post("/api/v2/services/plaza/gitea/credentials/reveal").json()["password"] == "keep-me"
    assert client.put("/api/v2/services/plaza/gitea", json={"clear_password": True}).status_code == 200
    assert client.post("/api/v2/services/plaza/gitea/credentials/reveal").status_code == 404


def test_manual_service_creation_stores_credentials_in_profile_only():
    server_id = add_host("10.66.66.20")
    response = client.post(f"/api/v2/services?server_id={server_id}", json={
        "name": "内部 Wiki", "url": "http://10.66.66.20:8080/", "category": "应用服务",
        "account": "wiki-admin", "password": "wiki-secret",
    })
    assert response.status_code == 201, response.text
    service_id = response.json()["id"]
    with SessionLocal() as db:
        profile = db.query(PlazaServiceProfile).filter(
            PlazaServiceProfile.plaza_key == f"manual-{uuid.UUID(service_id)}",
        ).first()
        assert profile.username == "wiki-admin"
        assert decrypt_secret(profile.secret_ciphertext) == "wiki-secret"

    rows = client.get("/api/v2/services/plaza").json()
    row = next(item for item in rows if item["service_id"] == service_id)
    assert row["has_credentials"] is True
    assert "password" not in row and "credential_username" not in row
