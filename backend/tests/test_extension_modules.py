"""Extension module registry, lifecycle, and OSS API tests.

The OSS SDK and network are replaced with fakes. These tests only exercise the
API contract, isolation guard, path validation, and lifecycle persistence.
"""

import pytest
from types import SimpleNamespace
from fastapi.testclient import TestClient

from app import auth
from app.database import Base, engine
from app.extension_modules import seed_extension_modules
from app.main import app
from app.services import aliyun_oss

client = TestClient(app)
BASE = "/api/v2/modules"


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "")
    seed_extension_modules()
    yield
    Base.metadata.drop_all(bind=engine)


def _fake_oss(monkeypatch):
    monkeypatch.setattr(aliyun_oss.aliyun_oss, "configured", lambda: True)
    monkeypatch.setattr(
        aliyun_oss.aliyun_oss,
        "status",
        lambda force=False: {
            "configured": True,
            "available": True,
            "bucket": "test-bucket",
            "prefix": "voice-workbench/source/",
            "endpoint": "https://oss.example",
            "mode": "readonly",
            "latency_ms": 8.5,
            "error": "",
            "checked_at": 1_700_000_000,
        },
    )
    monkeypatch.setattr(
        aliyun_oss.aliyun_oss,
        "list_objects",
        lambda prefix=None, marker=None, limit=100: {
            "prefix": prefix or "voice-workbench/source/",
            "items": [{
                "type": "text", "name": "README.md",
                "key": "voice-workbench/source/README.md",
                "size": 12, "last_modified": None,
                "storage_class": "Standard", "mime": "text/markdown",
            }],
            "next_marker": "",
            "truncated": False,
            "bucket": "test-bucket",
        },
    )
    monkeypatch.setattr(
        aliyun_oss.aliyun_oss,
        "preview_object",
        lambda key: {
            "key": key, "name": "README.md", "type": "text",
            "mime": "text/markdown", "size": 12, "previewable": True,
            "content": "hello module", "data_url": None,
            "truncated": False, "message": "",
        },
    )


def test_builtin_seed_and_lifecycle():
    items = client.get(BASE).json()["items"]
    assert [item["key"] for item in items] == ["oss-browser"]
    module = items[0]
    assert module["source"] == "builtin"
    assert module["running"] is False
    assert module["status"] == "stopped"
    assert module["isolation"] == "sandboxed-iframe"

    started = client.post(f"{BASE}/oss-browser/start")
    assert started.status_code == 200
    assert started.json()["running"] is True
    assert started.json()["status"] == "running"

    running = client.get(BASE, params={"running": True}).json()["items"]
    assert [item["key"] for item in running] == ["oss-browser"]

    stopped = client.post(f"{BASE}/oss-browser/stop")
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False


def test_registered_external_module_does_not_override_builtin():
    payload = {
        "key": "external-docs",
        "name": "外部文档",
        "entry_url": "https://docs.example/module/",
        "description": "test",
    }
    created = client.post(f"{BASE}/register", json=payload)
    assert created.status_code == 200
    assert created.json()["source"] == "registered"
    assert created.json()["running"] is False

    updated = client.post(
        f"{BASE}/register",
        json={**payload, "name": "外部文档 v2", "entry_url": "/v3/external-docs/"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "外部文档 v2"

    denied = client.post(
        f"{BASE}/register",
        json={"key": "oss-browser", "name": "覆盖", "entry_url": "/v3/evil/"},
    )
    assert denied.status_code == 409


def test_module_must_start_before_oss_calls(monkeypatch):
    _fake_oss(monkeypatch)
    response = client.get(f"{BASE}/oss-browser/objects")
    assert response.status_code == 409
    assert response.json()["detail"] == "模块未启动"


def test_oss_status_objects_preview_and_health(monkeypatch):
    _fake_oss(monkeypatch)
    client.post(f"{BASE}/oss-browser/start")

    status = client.get(f"{BASE}/oss-browser/status")
    assert status.status_code == 200
    assert status.json()["health"]["available"] is True
    assert status.json()["module"]["status"] == "running"

    objects = client.get(f"{BASE}/oss-browser/objects")
    assert objects.status_code == 200
    assert objects.json()["items"][0]["key"] == "voice-workbench/source/README.md"

    preview = client.get(
        f"{BASE}/oss-browser/preview",
        params={"key": "voice-workbench/source/README.md"},
    )
    assert preview.status_code == 200
    assert preview.json()["content"] == "hello module"

    health = client.post(f"{BASE}/oss-browser/health")
    assert health.status_code == 200
    assert health.json()["health"]["available"] is True


def test_list_objects_accepts_oss_timestamp(monkeypatch):
    reader = aliyun_oss.AliyunOssReader()
    monkeypatch.setattr(aliyun_oss, "OSS_PREFIX", "voice-workbench/source")
    monkeypatch.setattr(aliyun_oss, "OSS_BUCKET", "test-bucket")
    client = SimpleNamespace(
        list_objects=lambda **kwargs: SimpleNamespace(
            prefix_list=[],
            object_list=[SimpleNamespace(
                key="voice-workbench/source/README.md",
                last_modified=1789872000000,
                size=12,
                storage_class="Standard",
            )],
            next_marker="",
            is_truncated=False,
        )
    )
    monkeypatch.setattr(reader, "_client", lambda: client)

    result = reader.list_objects(prefix="voice-workbench/source/")
    assert result["items"][0]["last_modified"].startswith("2026-")

def test_audio_mime_types_are_explicit():
    reader = aliyun_oss.AliyunOssReader()
    assert reader._mime("voice.ogg") == "audio/ogg"
    assert reader._mime("voice.m4a") == "audio/mp4"
    assert reader._mime("voice.flac") == "audio/flac"
    assert reader._mime("voice.mp3") == "audio/mpeg"
    assert reader._mime("voice.wav") == "audio/wav"

def test_download_closes_oss_response(monkeypatch):
    reader = aliyun_oss.AliyunOssReader()
    monkeypatch.setattr(aliyun_oss, "OSS_PREFIX", "voice-workbench/source")

    class FakeResponse:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            yield b"hello "
            yield b"module"

        def close(self):
            self.closed = True

    response = FakeResponse()
    reader._client = lambda: SimpleNamespace(get_object=lambda key: response)
    chunks, filename, mime = reader.open_download("voice-workbench/source/a.txt")
    assert b"".join(chunks) == b"hello module"
    assert filename == "a.txt" and mime == "text/plain"
    assert response.closed is True

def test_oss_path_validation_rejects_escape(monkeypatch):
    reader = aliyun_oss.AliyunOssReader()
    monkeypatch.setattr(aliyun_oss, "OSS_PREFIX", "voice-workbench/source")

    assert reader.normalize_prefix("voice-workbench/source/2026-09/") == "voice-workbench/source/2026-09/"
    assert reader.normalize_key("voice-workbench/source/README.md") == "voice-workbench/source/README.md"

    for value in (
        "voice-workbench/source/../secret.txt",
        "voice-workbench/source2/file.txt",
        "/other-prefix/file.txt",
        "voice-workbench/source/bad\x00name",
    ):
        with pytest.raises(aliyun_oss.OssModuleError):
            reader.normalize_key(value)

    with pytest.raises(aliyun_oss.OssModuleError):
        reader.normalize_prefix("other-prefix/")


def test_module_routes_require_operator_when_configured(monkeypatch):
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "module-secret")
    assert client.get(BASE).status_code == 401
    assert client.post(f"{BASE}/oss-browser/start").status_code == 401

    headers = {"Authorization": "Bearer module-secret"}
    assert client.get(BASE, headers=headers).status_code == 200
    assert client.post(f"{BASE}/oss-browser/start", headers=headers).status_code == 200
