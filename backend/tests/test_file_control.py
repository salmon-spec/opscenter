"""File manager API behavior and safety checks."""
import base64
import os
import uuid

from fastapi.testclient import TestClient
import pytest

from app import control, file_control
from app.main import Base, SessionLocal, app, engine
from app.models import Server


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    control._CONTAINER_CACHE.clear()
    yield


def local_host() -> str:
    row_id = uuid.uuid4()
    row = Server(id=row_id, name="local-files", host="127.0.0.1", agent_type="local", status="online")
    with SessionLocal() as db:
        db.add(row)
        db.commit()
    return str(row_id)


def test_local_file_lifecycle_is_recoverable(tmp_path, monkeypatch):
    server_id = local_host()
    root = tmp_path / "files"
    root.mkdir()
    trash_home = tmp_path / "home"
    trash_home.mkdir()
    monkeypatch.setattr(file_control, "_trash_home", lambda _remote, _sftp: str(trash_home))

    created = client.post(f"/api/v2/servers/{server_id}/files/directories", json={"parent": str(root), "name": "docs"})
    assert created.status_code == 201, created.text
    uploaded = client.post(f"/api/v2/servers/{server_id}/files/upload", json={
        "parent": str(root / "docs"), "name": "hello.txt",
        "content_base64": base64.b64encode(b"hello").decode(),
    })
    assert uploaded.status_code == 201, uploaded.text
    file_path = root / "docs" / "hello.txt"

    listed = client.get(f"/api/v2/servers/{server_id}/files", params={"path": str(root / "docs")})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["name"] == "hello.txt"
    assert listed.json()["source"] == "local"

    content = client.get(f"/api/v2/servers/{server_id}/files/content", params={"path": str(file_path)}).json()
    saved = client.put(
        f"/api/v2/servers/{server_id}/files/content",
        params={"path": str(file_path)},
        json={"content": "updated", "expected_mtime": content["mtime"]},
    )
    assert saved.status_code == 200, saved.text
    assert file_path.read_text() == "updated"

    renamed_path = root / "docs" / "renamed.txt"
    renamed = client.post(f"/api/v2/servers/{server_id}/files/move", json={"source": str(file_path), "target": str(renamed_path)})
    assert renamed.status_code == 200
    downloaded = client.get(f"/api/v2/servers/{server_id}/files/download", params={"path": str(renamed_path)}).json()
    assert base64.b64decode(downloaded["content_base64"]) == b"updated"

    trashed = client.post(f"/api/v2/servers/{server_id}/files/trash", json={"path": str(renamed_path), "confirm_name": "renamed.txt"})
    assert trashed.status_code == 200, trashed.text
    assert trashed.json()["recoverable"] is True
    assert not renamed_path.exists()
    assert os.path.exists(trashed.json()["trash_path"])


def test_file_safety_conflict_duplicate_and_root_protection(tmp_path):
    server_id = local_host()
    target = tmp_path / "config.txt"
    target.write_text("one")
    before = target.stat().st_mtime
    os.utime(target, (before + 5, before + 5))
    conflict = client.put(
        f"/api/v2/servers/{server_id}/files/content",
        params={"path": str(target)}, json={"content": "two", "expected_mtime": before},
    )
    assert conflict.status_code == 409

    duplicate = client.post(f"/api/v2/servers/{server_id}/files/upload", json={
        "parent": str(tmp_path), "name": "config.txt",
        "content_base64": base64.b64encode(b"replacement").decode(),
    })
    assert duplicate.status_code == 409
    assert target.read_text() == "one"

    root_path = os.path.abspath(os.path.sep)
    root_delete = client.post(f"/api/v2/servers/{server_id}/files/trash", json={"path": root_path, "confirm_name": os.path.basename(root_path)})
    assert root_delete.status_code == 400
