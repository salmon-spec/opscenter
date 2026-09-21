"""Extension module registry, lifecycle, and OSS API tests.

The OSS SDK and network are replaced with fakes. These tests only exercise the
API contract, isolation guard, path validation, and lifecycle persistence.
"""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
import sys
from types import SimpleNamespace
from fastapi.testclient import TestClient

from app import extension_modules as extension_modules_service
from app import auth
from app.credential_crypto import decrypt_secret
from app.database import Base, engine, get_db
from app.extension_modules import seed_extension_modules
from app.main import app
from app.models import CommandCodeUsageSnapshot, ExtensionModule
from app.services import aliyun_oss
from app.services import command_code

client = TestClient(app)
BASE = "/api/v2/modules"


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    aliyun_oss.aliyun_oss.configure(None)
    command_code.command_code.configure(None)
    monkeypatch.setattr(command_code, "COMMAND_CODE_API_KEY", "")
    monkeypatch.setattr(command_code, "COMMAND_CODE_API_BASE", "https://api.commandcode.ai")
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "")
    seed_extension_modules()
    yield
    Base.metadata.drop_all(bind=engine)
    command_code.command_code.configure(None)


def _fake_oss(monkeypatch):
    monkeypatch.setattr(aliyun_oss.aliyun_oss, "configured", lambda: True)
    monkeypatch.setattr(
        aliyun_oss.aliyun_oss,
        "status",
        lambda force=False, bucket=None: {
            "configured": True,
            "available": True,
            "bucket": bucket or "test-bucket",
            "buckets": ["test-bucket", "alt-bucket"],
            "prefix": "voice-workbench/source/",
            "endpoint": "https://oss.example",
            "mode": "readonly",
            "latency_ms": 8.5,
            "error": "",
            "checked_at": 1_700_000_000,
        },
    )
    def _fake_list_objects(prefix=None, marker=None, limit=100, bucket=None):
        if bucket not in (None, "test-bucket", "alt-bucket"):
            raise aliyun_oss.OssModuleError("存储桶不在只读白名单内")
        return {
            "prefix": prefix or "voice-workbench/source/",
            "items": [{
                "type": "text", "name": "README.md",
                "key": "voice-workbench/source/README.md" if not bucket else "README.md",
                "size": 12, "last_modified": None,
                "storage_class": "Standard", "mime": "text/markdown",
            }],
            "next_marker": "",
            "truncated": False,
            "bucket": bucket or "test-bucket",
        }
    monkeypatch.setattr(aliyun_oss.aliyun_oss, "list_objects", _fake_list_objects)
    monkeypatch.setattr(
        aliyun_oss.aliyun_oss,
        "summary",
        lambda prefix=None, bucket=None, force=False: {
                "bucket": bucket or "test-bucket",
                "prefix": prefix or "voice-workbench/source/",
                "total_size": 12,
                "object_count": 1,
                "direct_object_count": 1,
                "folders": {},
                "pages": 1,
                "cached": False,
                "scanned_at": "2026-09-21T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        aliyun_oss.aliyun_oss,
        "preview_object",
        lambda key, bucket=None: {
            "key": key, "name": "README.md", "type": "text",
            "mime": "text/markdown", "size": 12, "previewable": True,
            "content": "hello module", "data_url": None,
            "truncated": False, "message": "",
        },
    )


def _fake_command_code(monkeypatch):
    calls = []

    def _request_json(endpoint, query=None, config=None):
        calls.append((endpoint, dict(query or {}), dict(config or {})))
        if endpoint == "/alpha/whoami":
            return {
                "success": True,
                "org": None,
                "user": {"id": "user-12345678", "userName": "ops-user", "email": "ops@example.com"},
                "orgLimits": [],
            }
        if endpoint == "/alpha/billing/credits":
            return {
                "credits": {
                    "planId": "individual-goat",
                    "monthlyCredits": 60.166169574,
                    "purchasedCredits": 0,
                    "freeCredits": 0,
                },
                "windowLimits": {
                    "limited": True,
                    "exceeded": None,
                    "fiveHour": {"used": 1.5, "cap": 14, "resetAt": 1789903865046, "exceeded": False},
                    "weekly": {"used": 9.8, "cap": 35, "resetAt": 1790230392477, "exceeded": False},
                },
            }
        if endpoint == "/alpha/billing/subscriptions":
            return {
                "success": True,
                "data": {
                    "planId": "individual-goat",
                    "status": "active",
                    "currentPeriodStart": "2026-09-17T06:06:46.000Z",
                    "currentPeriodEnd": "2026-10-17T06:06:46.000Z",
                    "cancelAtPeriodEnd": False,
                },
            }
        if endpoint == "/alpha/usage/summary":
            return {
                "averageCost": 0.0018815,
                "completedCount": 5171,
                "failedCount": 0,
                "periodBasis": "billing-period",
                "successRate": 100,
                "totalCost": 9.729477366,
                "totalCount": 5171,
                "totalCredits": 9.729477366,
                "totalTokens": 284920667,
                "totalTokensIn": 277062373,
                "totalTokensOut": 7858294,
            }
        raise command_code.CommandCodeUsageError("unexpected endpoint")

    monkeypatch.setattr(command_code.command_code, "_request_json", _request_json)
    return calls


def test_oss_module_config_is_encrypted_masked_and_resettable(monkeypatch):
    def _fake_discover(access_key_id, access_key_secret, endpoint=None):
        assert access_key_id == "LTAI-module-ak"
        assert access_key_secret == "module-secret-value"
        return {
            "endpoint": "https://oss-cn-wulanchabu.aliyuncs.com",
            "buckets": ["bucket-a", "bucket-b"],
            "bucket_endpoints": {
                "bucket-a": "https://oss-cn-wulanchabu.aliyuncs.com",
                "bucket-b": "https://oss-cn-beijing.aliyuncs.com",
            },
        }

    monkeypatch.setattr(aliyun_oss.aliyun_oss, "discover_buckets", _fake_discover)


    saved = client.post(
        f"{BASE}/oss-browser/config",
        json={
            "access_key_id": "LTAI-module-ak",
            "access_key_secret": "module-secret-value",
            "endpoint": "https://oss-cn-wulanchabu.aliyuncs.com",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["source"] == "module"
    assert saved.json()["access_key_masked"] == "LTAI-m...e-ak"
    assert saved.json()["bucket_count"] == 2
    assert aliyun_oss.aliyun_oss.allowed_buckets() == ["bucket-a", "bucket-b"]
    assert "access_key_secret" not in saved.json()

    with get_db() as db:
        row = db.query(ExtensionModule).filter(ExtensionModule.key == "oss-browser").first()
        assert row.config_ciphertext
        assert "module-secret-value" not in row.config_ciphertext
        decrypted = decrypt_secret(row.config_ciphertext)
        assert "module-secret-value" in decrypted

    current = client.get(f"{BASE}/oss-browser/config")
    assert current.status_code == 200
    assert current.json()["source"] == "module"
    assert "module-secret-value" not in current.text

    reset = client.post(f"{BASE}/oss-browser/config/reset")
    assert reset.status_code == 200
    assert reset.json()["source"] == "environment"
    with get_db() as db:
        row = db.query(ExtensionModule).filter(ExtensionModule.key == "oss-browser").first()
        assert row.config_ciphertext is None


def test_command_code_config_is_encrypted_masked_and_resettable(monkeypatch):
    _fake_command_code(monkeypatch)
    saved = client.post(
        f"{BASE}/command-code-usage/config",
        json={
            "api_key": "cmd_live_secret_key_123456",
            "api_base": "https://api.commandcode.ai",
            "org_id": "",
            "refresh_interval_seconds": 90,
            "timeout_seconds": 10,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["source"] == "module"
    assert saved.json()["api_key_masked"] == "cmd_li...3456"
    assert saved.json()["refresh_interval_seconds"] == 90
    assert "cmd_live_secret_key_123456" not in saved.text

    with get_db() as db:
        row = db.query(ExtensionModule).filter(ExtensionModule.key == "command-code-usage").first()
        assert row.config_ciphertext
        assert "cmd_live_secret_key_123456" not in row.config_ciphertext
        assert "cmd_live_secret_key_123456" in decrypt_secret(row.config_ciphertext)

    current = client.get(f"{BASE}/command-code-usage/config")
    assert current.status_code == 200
    assert current.json()["configured"] is True
    assert "cmd_live_secret_key_123456" not in current.text

    reset = client.post(f"{BASE}/command-code-usage/config/reset")
    assert reset.status_code == 200
    assert reset.json()["source"] == "environment"
    assert reset.json()["configured"] is False


def test_command_code_usage_normalizes_goat_metrics(monkeypatch):
    calls = _fake_command_code(monkeypatch)
    saved = client.post(
        f"{BASE}/command-code-usage/config",
        json={"api_key": "cmd_live_secret_key_123456", "timeout_seconds": 10},
    )
    assert saved.status_code == 200
    client.post(f"{BASE}/command-code-usage/start")
    status = client.get(f"{BASE}/command-code-usage/status", params={"force": True})
    assert status.status_code == 200
    health = status.json()["health"]
    assert health["available"] is True
    assert health["error"] == ""
    assert health["plan"]["id"] == "individual-goat"
    assert health["plan"]["name"] == "GOAT"
    assert health["plan"]["monthly_credits"] == 70
    assert health["credits"]["remaining_total"] == 60.166169574
    assert round(health["credits"]["usage_percent"], 2) == 14.05
    assert health["windows"]["five_hour"]["cap"] == 14
    assert health["windows"]["weekly"]["used"] == 9.8
    assert health["usage"]["total_count"] == 5171
    assert health["usage"]["total_tokens"] == 284920667
    assert health["history"]["available"] is True
    assert health["history"]["windows"]["hour"]["credits"] is None
    assert health["history"]["windows"]["month"]["credits"] == 9.729477366
    assert status.json()["module"]["status"] == "running"
    assert any(endpoint == "/alpha/usage/summary" for endpoint, _, _ in calls)


def test_command_code_invalid_key_is_not_persisted(monkeypatch):
    def _unauthorized(endpoint, query=None, config=None):
        raise command_code.CommandCodeUsageError("Command Code API Key 无效或已过期")

    monkeypatch.setattr(command_code.command_code, "_request_json", _unauthorized)
    response = client.post(
        f"{BASE}/command-code-usage/config",
        json={"api_key": "invalid-command-code-key"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Command Code API Key 无效或已过期"
    with get_db() as db:
        row = db.query(ExtensionModule).filter(ExtensionModule.key == "command-code-usage").first()
        assert row.config_ciphertext is None


def test_command_code_sampling_uses_environment_configuration(monkeypatch):
    _fake_command_code(monkeypatch)
    monkeypatch.setattr(command_code, "COMMAND_CODE_API_KEY", "env-command-code-key")
    with get_db() as db:
        row = db.query(ExtensionModule).filter(ExtensionModule.key == "command-code-usage").first()
        row.running = True
        db.commit()
        assert extension_modules_service._prepare_command_code_sampling(row) is True


def test_command_code_history_uses_snapshot_deltas():
    scope = "snapshot-test-scope"
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    with get_db() as db:
        db.add(CommandCodeUsageSnapshot(
            scope_key=scope,
            captured_at=(now - timedelta(hours=1)).replace(tzinfo=None),
            period_start="2026-09-17T06:06:46.000Z",
            total_credits=1.0,
            total_tokens=1000,
            total_count=10,
        ))
        db.add(CommandCodeUsageSnapshot(
            scope_key=scope,
            captured_at=(now - timedelta(minutes=30)).replace(tzinfo=None),
            period_start="2026-09-17T06:06:46.000Z",
            total_credits=2.0,
            total_tokens=2500,
            total_count=18,
        ))
        db.commit()

    result = {
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "plan": {
            "current_period_start": "2026-09-17T06:06:46.000Z",
        },
        "usage": {
            "total_credits": 3.0,
            "total_tokens": 4000,
            "total_count": 30,
        },
    }
    history = command_code.command_code._build_history(result, scope, now=now)
    hour = history["windows"]["hour"]
    assert hour["credits"] == 2.0
    assert hour["requests"] == 20
    assert hour["tokens"] == 3000
    assert hour["complete"] is True
    day = history["windows"]["day"]
    assert day["complete"] is False
    assert day["observed_seconds"] == 3600


def test_discover_buckets_derives_regional_endpoints(monkeypatch):
    class FakeService:
        def __init__(self, auth, endpoint):
            assert auth == "module-auth"
            assert endpoint == "https://oss-cn-wulanchabu.aliyuncs.com"

        def list_buckets(self):
            return SimpleNamespace(buckets=[
                SimpleNamespace(name="bucket-bj", location="oss-cn-beijing"),
                SimpleNamespace(name="bucket-hz", location="oss-cn-hangzhou"),
            ])

    monkeypatch.setitem(sys.modules, "oss2", SimpleNamespace(
        Auth=lambda access_key, secret_key: "module-auth",
        Service=FakeService,
    ))

    result = aliyun_oss.AliyunOssReader.discover_buckets(
        "module-ak", "module-secret", "https://oss-cn-wulanchabu.aliyuncs.com",
    )
    assert result["buckets"] == ["bucket-bj", "bucket-hz"]
    assert result["bucket_endpoints"]["bucket-bj"] == "https://oss-cn-beijing.aliyuncs.com"
    assert result["bucket_endpoints"]["bucket-hz"] == "https://oss-cn-hangzhou.aliyuncs.com"


def test_builtin_seed_and_lifecycle():
    items = client.get(BASE).json()["items"]
    assert [item["key"] for item in items] == ["oss-browser", "command-code-usage"]
    module = items[0]
    assert module["source"] == "builtin"
    assert module["running"] is False
    assert module["status"] == "stopped"
    assert module["isolation"] == "sandboxed-iframe"
    assert items[1]["source"] == "builtin"
    assert items[1]["name"] == "模型用量"
    assert items[1]["permissions"] == ["command-code:read"]
    assert items[1]["manifest"]["readonly"] is True
    assert items[1]["running"] is False

    started = client.post(f"{BASE}/oss-browser/start")
    assert started.status_code == 200
    assert started.json()["running"] is True
    assert started.json()["status"] == "running"

    command_code_started = client.post(f"{BASE}/command-code-usage/start")
    assert command_code_started.status_code == 200
    assert command_code_started.json()["running"] is True

    running = client.get(BASE, params={"running": True}).json()["items"]
    assert [item["key"] for item in running] == ["oss-browser", "command-code-usage"]

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
    assert status.json()["health"]["buckets"] == ["test-bucket", "alt-bucket"]
    assert status.json()["module"]["status"] == "running"

    objects = client.get(f"{BASE}/oss-browser/objects")
    assert objects.status_code == 200
    assert objects.json()["items"][0]["key"] == "voice-workbench/source/README.md"

    summary = client.get(f"{BASE}/oss-browser/summary")
    assert summary.status_code == 200
    assert summary.json()["total_size"] == 12
    assert summary.json()["object_count"] == 1

    alt_status = client.get(
        f"{BASE}/oss-browser/status", params={"bucket": "alt-bucket"},
    )
    assert alt_status.status_code == 200
    assert alt_status.json()["health"]["bucket"] == "alt-bucket"

    alt_objects = client.get(
        f"{BASE}/oss-browser/objects", params={"bucket": "alt-bucket"},
    )
    assert alt_objects.status_code == 200
    assert alt_objects.json()["bucket"] == "alt-bucket"
    assert alt_objects.json()["items"][0]["key"] == "README.md"

    denied = client.get(
        f"{BASE}/oss-browser/objects", params={"bucket": "not-allowed"},
    )
    assert denied.status_code == 400
    assert denied.json()["detail"] == "存储桶不在只读白名单内"

    preview = client.get(
        f"{BASE}/oss-browser/preview",
        params={"key": "voice-workbench/source/README.md", "bucket": "test-bucket"},
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
    monkeypatch.setattr(reader, "_client", lambda bucket=None: client)

    result = reader.list_objects(prefix="voice-workbench/source/")
    assert result["items"][0]["last_modified"].startswith("2026-")


def test_oss_summary_paginates_and_counts_first_level_directories(monkeypatch):
    reader = aliyun_oss.AliyunOssReader()
    reader.configure({
        "access_key_id": "test-ak",
        "access_key_secret": "test-secret",
        "buckets": ["test-bucket"],
        "bucket_endpoints": {"test-bucket": "https://oss.example"},
        "bucket_prefixes": {"test-bucket": "root/"},
    })
    pages = [
        SimpleNamespace(
            object_list=[
                SimpleNamespace(key="root/a.txt", size=10),
                SimpleNamespace(key="root/b/c.txt", size=20),
            ],
            is_truncated=True,
            next_continuation_token="next",
        ),
        SimpleNamespace(
            object_list=[
                SimpleNamespace(key="root/d.txt", size=5),
                SimpleNamespace(key="root/empty/", size=0),
            ],
            is_truncated=False,
            next_continuation_token="",
        ),
    ]
    calls = []

    class FakeClient:
        def list_objects_v2(self, **kwargs):
            calls.append(kwargs)
            return pages[len(calls) - 1]

    monkeypatch.setattr(reader, "_client", lambda bucket=None: FakeClient())
    result = reader.summary(prefix="root/", bucket="test-bucket")
    assert result["total_size"] == 35
    assert result["object_count"] == 3
    assert result["direct_object_count"] == 2
    assert result["folders"]["root/b/"]["count"] == 1
    assert result["folders"]["root/b/"]["size"] == 20
    assert calls[1]["continuation_token"] == "next"
    cached = reader.summary(prefix="root/", bucket="test-bucket")
    assert cached["cached"] is True
    assert len(calls) == 2


def test_oss_summary_does_not_hold_lock_during_network_scan(monkeypatch):
    reader = aliyun_oss.AliyunOssReader()
    reader.configure({
        "access_key_id": "test-ak",
        "access_key_secret": "test-secret",
        "buckets": ["test-bucket"],
        "bucket_endpoints": {"test-bucket": "https://oss.example"},
        "bucket_prefixes": {},
    })
    barrier = threading.Barrier(2, timeout=2)

    class FakeClient:
        def list_objects_v2(self, prefix=None, **kwargs):
            barrier.wait()
            return SimpleNamespace(
                object_list=[SimpleNamespace(key=f"{prefix}file.txt", size=1)],
                is_truncated=False,
                next_continuation_token="",
            )

    monkeypatch.setattr(reader, "_client", lambda bucket=None: FakeClient())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(reader.summary, "one/", "test-bucket")
        second = pool.submit(reader.summary, "two/", "test-bucket")
        assert first.result(timeout=3)["object_count"] == 1
        assert second.result(timeout=3)["object_count"] == 1

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
    monkeypatch.setattr(aliyun_oss, "OSS_BUCKET", "test-bucket")

    class FakeResponse:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            yield b"hello "
            yield b"module"

        def close(self):
            self.closed = True

    response = FakeResponse()
    reader._client = lambda bucket=None: SimpleNamespace(get_object=lambda key: response)
    chunks, filename, mime = reader.open_download("voice-workbench/source/a.txt")
    assert b"".join(chunks) == b"hello module"
    assert filename == "a.txt" and mime == "text/plain"
    assert response.closed is True

def test_oss_path_validation_rejects_escape(monkeypatch):
    reader = aliyun_oss.AliyunOssReader()
    monkeypatch.setattr(aliyun_oss, "OSS_PREFIX", "voice-workbench/source")
    monkeypatch.setattr(aliyun_oss, "OSS_BUCKET", "test-bucket")
    monkeypatch.setattr(aliyun_oss, "OSS_BUCKETS", "")
    monkeypatch.setattr(aliyun_oss, "OSS_BUCKET_PREFIXES", "")

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


def test_oss_bucket_whitelist_and_per_bucket_prefix(monkeypatch):
    reader = aliyun_oss.AliyunOssReader()
    monkeypatch.setattr(aliyun_oss, "OSS_BUCKET", "primary-bucket")
    monkeypatch.setattr(aliyun_oss, "OSS_BUCKETS", "alt-bucket,empty-prefix-bucket")
    monkeypatch.setattr(aliyun_oss, "OSS_PREFIX", "voice-workbench/source")
    monkeypatch.setattr(
        aliyun_oss,
        "OSS_BUCKET_ENDPOINTS",
        '{"primary-bucket":"https://oss-cn-wulanchabu.aliyuncs.com","alt-bucket":"https://oss-cn-hangzhou.aliyuncs.com"}',
    )
    monkeypatch.setattr(
        aliyun_oss,
        "OSS_BUCKET_PREFIXES",
        '{"primary-bucket":"voice-workbench/source/","empty-prefix-bucket":""}',
    )

    assert reader.allowed_buckets() == [
        "primary-bucket", "alt-bucket", "empty-prefix-bucket",
    ]
    assert reader.resolve_bucket() == "primary-bucket"
    assert reader.resolve_bucket("alt-bucket") == "alt-bucket"
    assert reader._endpoint_for("primary-bucket").startswith("https://oss-cn-wulanchabu")
    assert reader._endpoint_for("alt-bucket").startswith("https://oss-cn-hangzhou")
    with pytest.raises(aliyun_oss.OssModuleError):
        reader._endpoint_for("empty-prefix-bucket")
    assert reader.prefix_for("alt-bucket") == ""
    assert reader.prefix_for("primary-bucket") == "voice-workbench/source/"
    assert reader.normalize_prefix("logs/", "alt-bucket") == "logs/"
    assert reader.normalize_key("README.md", "alt-bucket") == "README.md"

    with pytest.raises(aliyun_oss.OssModuleError):
        reader.resolve_bucket("not-allowed")
    with pytest.raises(aliyun_oss.OssModuleError):
        reader.normalize_key("README.md", "primary-bucket")
    with pytest.raises(aliyun_oss.OssModuleError):
        reader.normalize_key("oss://alt-bucket/README.md", "primary-bucket")


def test_module_routes_require_operator_when_configured(monkeypatch):
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "module-secret")
    assert client.get(BASE).status_code == 401
    assert client.post(f"{BASE}/oss-browser/start").status_code == 401

    headers = {"Authorization": "Bearer module-secret"}
    assert client.get(BASE, headers=headers).status_code == 200
    assert client.post(f"{BASE}/oss-browser/start", headers=headers).status_code == 200
