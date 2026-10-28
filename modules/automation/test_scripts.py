import copy
import subprocess
import time

import pytest
from fastapi import HTTPException

from .scripts import Scripts
from .runbooks.sync_pve_hosts import plan
from .test_main import client


def repository(tmp_path):
    root = tmp_path / "runbooks"
    root.mkdir()
    (root / "check.yml").write_text("---\n# original\n", encoding="utf-8")
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    scripts = Scripts(root, 1)
    scripts.git("add", "check.yml")
    scripts.git("-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "Initial")
    return scripts


def test_script_edit_revision_and_paths(tmp_path):
    scripts = repository(tmp_path)
    original = scripts.read("check.yml")
    result = scripts.save("check.yml", "---\n# edited\n", original["revision"], "admin")
    assert scripts.git("show", "HEAD:check.yml") == "---\n# edited"
    assert result["revision"] != original["revision"]
    with pytest.raises(HTTPException) as error:
        scripts.save("check.yml", "---\n# stale\n", original["revision"], "admin")
    assert error.value.status_code == 409
    assert scripts.files() == ["check.yml"]
    for path in ("../x.py", "/etc/passwd", ".git/config", "a/.git/x.py", "-x.py", "C:/x.py", "a\\x.py"):
        with pytest.raises(HTTPException):
            scripts.read(path)
    assert not (scripts.root / ".opscenter-edit.lock").exists()


def test_script_api_scope_and_preserves_whitespace(client, tmp_path, monkeypatch):
    scripts = repository(tmp_path)
    monkeypatch.setenv("AUTOMATION_SCRIPTS_DIR", str(scripts.root))
    monkeypatch.setenv("AUTOMATION_SCRIPTS_REPOSITORY_ID", "1")
    store = client.app.state.store
    store.save("templates", {"name": "script", "playbook": "check.yml", "repository_id": 1}, 1)
    original = client.get("/api/v1/templates/1/script").json()
    payload = {"path": "check.yml", "revision": original["revision"], "content": "---\n# edited\n\n"}
    assert client.post("/api/v1/templates/1/script", json=payload).status_code == 200
    assert scripts.read("check.yml")["content"] == payload["content"]
    assert client.post("/api/v1/templates/1/script", json=payload).status_code == 409
    store.save("tasks", {"status": "running"}, 1)
    payload["revision"] = scripts.read("check.yml")["revision"]
    assert client.post("/api/v1/templates/1/script", json=payload).status_code == 409
    store.save("templates", {"repository_id": 2}, 2)
    assert client.get("/api/v1/templates/2/script").status_code == 409
    assert client.get("/api/v1/templates/1/script?path=../secret.py").status_code == 422


def test_sync_add_update_remove_and_idempotence():
    config = {"source_id": "test", "legacy_vmids": {"old": 100}}
    snapshot = {"complete": True, "source_id": "test", "collected_at": time.time(), "vms": [
        {"vmid": 110, "ips": ["192.0.2.10"], "name": "guest", "cpus": 4, "memory_mib": 4096, "status": "stopped"},
        {"vmid": 119, "ips": [], "name": "new", "cpus": 2, "memory_mib": 2048, "status": "running"}]}
    hosts = [{"id": "old", "name": "deleted", "tags": []},
             {"id": "cloud", "name": "cloud", "host": "192.0.2.1", "tags": []},
             {"id": "guest", "name": "My guest", "host": "192.0.2.10", "tags": ["K8s"], "remark": "keep"}]
    actions = plan(snapshot, hosts, config)
    assert [(a["action"], a["vmid"]) for a in actions] == [("update", 110), ("create", 119), ("archive", 100)]
    assert actions[0]["values"]["name"] == "My guest"
    assert actions[0]["values"]["remark"].startswith("keep\n")
    assert actions[1]["values"]["host"].endswith(".invalid")
    after = [{"id": a["id"], **a["values"]} for a in actions if a["action"] != "archive"] + [hosts[1]]
    assert plan(snapshot, after, config) == []
    for bad in ({"complete": False}, {"source_id": "wrong"}, {"collected_at": 0}, {"vms": []},
                {"vms": [snapshot["vms"][0]] * 2}):
        with pytest.raises(ValueError):
            plan({**snapshot, **bad}, hosts, config)
    # A stopped guest and a guest converted into a template still exist in PVE.
    converted = copy.deepcopy(snapshot)
    converted["vms"][0]["template"] = True
    assert not any(a["action"] == "archive" and a["vmid"] == 110 for a in plan(converted, after, config))
    with pytest.raises(ValueError):
        plan(snapshot, [*hosts, {**hosts[2], "id": "duplicate"}], config)
