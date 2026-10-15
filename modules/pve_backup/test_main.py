from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main


class FakePve:
    def __init__(self, now):
        self.now = now

    def get(self, path, params=None):
        if path == "/cluster/resources" and params == {"type": "vm"}:
            return [
                {"type": "qemu", "vmid": 100, "name": "test-vm", "node": "pve", "status": "running"},
                {"type": "lxc", "vmid": 110, "name": "test-ct", "node": "pve", "status": "stopped"},
            ]
        if path == "/cluster/resources" and params == {"type": "node"}:
            return [{"node": "pve", "status": "online"}]
        if path == "/nodes/pve/storage":
            return [
                {"storage": "local", "type": "dir", "status": "available", "content": "backup,iso",
                 "shared": False, "active": 1},
                {"storage": "local-lvm", "type": "lvmthin", "status": "available", "content": "images,rootdir",
                 "active": 1},
            ]
        if path == "/nodes/pve/storage/local/status":
            return {"total": 1000, "used": 300, "avail": 700}
        if path == "/nodes/pve/storage/local-lvm/status":
            return {"total": 2000, "used": 1000, "avail": 1000}
        if path == "/nodes/pve/storage/local/content":
            return [{"vmid": 100, "volid": "local:backup/vzdump-qemu-100-test.vma.zst",
                     "size": 500, "ctime": self.now.timestamp() - 3600, "format": "vma.zst"}]
        if path == "/nodes/pve/tasks":
            return [
                {"upid": "UPID:pve:1", "status": "ERROR", "starttime": self.now.timestamp()},
                {"upid": "UPID:pve:2", "status": "OK", "starttime": self.now.timestamp() - 86400},
            ]
        if path == "/cluster/backup":
            return [{"id": "job-1", "enabled": 1, "schedule": "sun 02:00", "storage": "local", "vmid": "100,110"}]
        raise AssertionError((path, params))


def test_report_counts_storage_backup_coverage_and_unverified_restore():
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    report = main.collect_report(FakePve(now), now, external_backups=[], external_collected_at=now)
    data = report["data"]
    assert report["status"] == "degraded"
    assert data["vm_total"] == 2 and data["vm_running"] == 1
    assert data["storage_total_bytes"] == 3000 and data["storage_used_bytes"] == 1300
    assert data["backup_coverage_current"] == 1 and data["backup_coverage_missing"] == 1
    assert all(item["recovery_test"] == "unverified" for item in data["guests"])
    assert data["jobs"][0]["id"] == "job-1"

    monkeypatched = main.get_report
    main.get_report = lambda: report
    try:
        findings = main.findings_provider()["items"]
    finally:
        main.get_report = monkeypatched
    assert {item["id"] for item in findings} == {
        "pve-backup-coverage:110", "pve-restore-verification:unverified", "pve-backup-task:UPID:pve:1",
    }


def test_oss_manifest_prevents_false_missing_and_missing_manifest_is_unknown():
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    unknown = main.collect_report(FakePve(now), now)
    assert unknown["data"]["backup_coverage_unknown"] == 1
    assert unknown["data"]["backup_coverage_missing"] == 0
    remote = {"vmid": "110", "node": "", "storage": "OSS", "volid": "oss://example/vzdump-qemu-110",
              "size_bytes": 100, "created_at": now, "format": "vma.zst", "source": "oss"}
    report = main.collect_report(FakePve(now), now, external_backups=[remote], external_collected_at=now)
    assert report["data"]["backup_coverage_current"] == 2
    assert report["data"]["backup_coverage_missing"] == 0
    assert report["data"]["backup_oss_total"] == 1
    assert report["data"]["guests"][1]["backup_source"] == "oss"


def test_oss_listing_parser_rejects_empty_and_parses_size():
    from import_oss_manifest import parse

    with pytest.raises(ValueError):
        parse([])
    line = ("2026-10-14 02:50:13 +0800 CST  45848698494 Standard ETAG "
            "oss://pve-backup-qiushui/pve/2026-10-13/vzdump-qemu-100-2026_10_13-22_00_02.vma.zst")
    with pytest.raises(ValueError):
        parse([line])
    with pytest.raises(ValueError):
        parse([line, "Object Number is: 2"])
    rows = parse([line, "Object Number is: 1"])
    assert rows[0]["vmid"] == "100"
    assert rows[0]["size_bytes"] == 45848698494


def test_unconfigured_module_reports_unavailable_without_secrets(monkeypatch):
    monkeypatch.setattr(main, "get_report", lambda: {
        "status": "unavailable", "collected_at": None, "data": {}, "error": "PVE 只读 API 尚未配置",
    })
    result = TestClient(main.app).get("/api/v1/summary").json()
    assert result["status"] == "unavailable"
    assert result["error"] == "PVE 只读 API 尚未配置"
    assert "token" not in result["error"].lower()


def test_pve_client_requires_verified_https_origin(monkeypatch):
    with pytest.raises(main.PveConfigurationError):
        main.PveClient("http://pve.invalid:8006", "opscenter@pve!reader", "secret")
    with pytest.raises(main.PveConfigurationError):
        main.PveClient("https://user:password@pve.invalid:8006", "opscenter@pve!reader", "secret")
    assert main.PveClient("https://pve.invalid", "opscenter@pve!reader", "secret").origin == "https://pve.invalid:8006"


def test_pve_client_sends_read_only_get_token_and_sanitizes_response(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return b'{"data":[]}'

    class Opener:
        def open(self, request, timeout):
            seen["method"] = request.get_method()
            seen["auth"] = request.get_header("Authorization")
            seen["timeout"] = timeout
            return Response()

    monkeypatch.setattr(main, "build_opener", lambda *_handlers: Opener())
    client = main.PveClient("https://pve.invalid:8006", "opscenter@pve!reader", "opaque-secret")
    assert client.get("/cluster/resources", {"type": "vm"}) == []
    assert seen == {
        "method": "GET", "auth": "PVEAPIToken=opscenter@pve!reader=opaque-secret", "timeout": 4,
    }


def test_shared_storage_capacity_is_not_counted_once_per_node():
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)

    class Cluster(FakePve):
        def get(self, path, params=None):
            if path == "/cluster/resources" and params == {"type": "vm"}:
                return [{"type": "qemu", "vmid": 100, "name": "vm", "node": "pve", "status": "running"}]
            if path == "/cluster/resources" and params == {"type": "node"}:
                return [{"node": "pve"}, {"node": "pve2"}]
            if path.startswith("/nodes/") and path.endswith("/storage"):
                return [{"storage": "shared-backup", "status": "available", "content": "backup",
                         "active": 1, "shared": True}]
            if path.endswith("/storage/shared-backup/status"):
                return {"total": 1000, "used": 400, "avail": 600}
            if path.endswith("/storage/shared-backup/content"):
                return [{"vmid": 100, "volid": "shared-backup:backup/vzdump-qemu-100-test.vma.zst",
                         "size": 300, "ctime": now.timestamp(), "format": "vma.zst"}]
            if path.endswith("/tasks"):
                return []
            if path == "/cluster/backup":
                return []
            raise AssertionError((path, params))

    report = main.collect_report(Cluster(now), now)
    assert report["data"]["storage_total_bytes"] == 1000
    assert report["data"]["storage_used_bytes"] == 400


def test_missing_storage_status_is_not_reported_as_zero_capacity():
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)

    class MissingStatus(FakePve):
        def get(self, path, params=None):
            if path.endswith("/storage/local/status"):
                raise main.PveApiError("unavailable")
            return super().get(path, params)

    report = main.collect_report(MissingStatus(now), now)
    assert report["status"] == "degraded"
    assert report["data"]["storages"][0]["total_bytes"] is None
    assert report["data"]["storage_total_bytes"] is None
    assert report["data"]["storage_used_percent"] is None
