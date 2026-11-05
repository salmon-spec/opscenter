"""Dashboard lifecycle tests use an isolated in-memory database, never production."""
from contextlib import contextmanager
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth, screen_boards as boards


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in [boards.ScreenBoard, boards.ScreenPublication, boards.ScreenDefault]:
        model.__table__.create(engine)
    session = sessionmaker(bind=engine)

    @contextmanager
    def database():
        with session() as db:
            yield db

    monkeypatch.setattr(boards, "get_db", database)
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "MONITOR_ALLOW_ANONYMOUS", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "test-screen-token")
    app = FastAPI()
    app.include_router(boards.router)
    with TestClient(app, headers={"Authorization": "Bearer test-screen-token"}) as client:
        yield client
    engine.dispose()


BASE = "/api/v2/screen-boards"


def test_module_styles_survive_publish_and_restore(client):
    types = ['electricity', 'logs', 'services', 'automation', 'usage', 'oss', 'opencode',
             'sms', 'password', 'subscriptions', 'inventory', 'tasks', 'modules']
    config = deepcopy(boards.BUILTIN)
    config['theme']['style'] = 'neon'
    config['widgets'] = [dict(id=kind, type=kind, y=i*4, w=12, h=4,
                              variant=['detail', 'cards', 'compact'][i%3], accent='#ab77ff')
                         for i, kind in enumerate(types)]
    row = create(client, config=config)
    assert len(row['config']['widgets']) == len(types)
    published = publish(client, row)
    version = client.get(f"{BASE}/{row['id']}/versions/1").json()
    assert version['config']['theme']['style'] == 'neon'
    assert version['config']['widgets'][1]['variant'] == 'cards'
    bad = deepcopy(row['config']); bad['widgets'][0]['variant'] = 'arbitrary-css'
    assert save(client, published, config=bad).status_code == 422
    bad['widgets'][0]['variant'] = 'cards'; bad['widgets'][0]['accent'] = 'url(https://example.com)'
    assert save(client, published, config=bad).status_code == 422
    restored = client.post(f"{BASE}/{row['id']}/versions/1/restore", json={'revision': published['revision']})
    assert restored.status_code == 200
    assert restored.json()['config'] == version['config']


def create(client, **overrides):
    response = client.post(BASE, json={"name": "测试大屏", **overrides})
    assert response.status_code == 200, response.text
    return response.json()


def save(client, row, **overrides):
    payload = {key: row[key] for key in ["name", "config", "revision", "is_template"]}
    return client.put(f"{BASE}/{row['id']}", json={**payload, **overrides})


def publish(client, row):
    response = client.post(f"{BASE}/{row['id']}/publish", json={"revision": row["revision"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_auth_covers_all_reads_and_writes(client):
    row = create(client)
    client.headers.clear()
    for method, path, body in [
        ("GET", "", None), ("GET", "/default", None), ("POST", "", {"name": "x"}),
        ("POST", "/default", {"board_id": None}), ("GET", f"/{row['id']}", None),
        ("PUT", f"/{row['id']}", {}), ("POST", f"/{row['id']}/publish", {"revision": 1}),
        ("GET", f"/{row['id']}/versions", None), ("GET", f"/{row['id']}/versions/1", None),
        ("POST", f"/{row['id']}/versions/1/restore", {"revision": 1}),
        ("DELETE", f"/{row['id']}?revision=1", None),
    ]:
        assert client.request(method, BASE + path, json=body).status_code == 401


def test_draft_publish_default_restore_and_delete(client):
    assert client.get(BASE + "/default").json()["id"] == "builtin"
    row = create(client, is_template=True)
    board_id = row["id"]
    assert client.post(BASE + "/default", json={"board_id": board_id}).status_code == 409
    row = publish(client, row)
    assert client.post(BASE + "/default", json={"board_id": board_id}).status_code == 200
    row = client.get(f"{BASE}/{board_id}").json()
    old_config = deepcopy(row["config"])
    row["config"]["title"] = "草稿新标题"
    row = save(client, row).json()
    assert client.get(BASE + "/default").json()["config"] == old_config
    row = publish(client, row)
    assert client.get(BASE + "/default").json()["config"]["title"] == "草稿新标题"
    assert client.get(f"{BASE}/{board_id}/versions/1").json()["config"] == old_config
    row = client.post(f"{BASE}/{board_id}/versions/1/restore", json={"revision": row["revision"]}).json()
    assert row["config"] == old_config
    assert client.get(BASE + "/default").json()["version"] == 2
    row = publish(client, row)
    assert row["published_version"] == 3
    assert client.get(BASE + "/default").json()["config"] == old_config
    assert len(client.get(f"{BASE}/{board_id}/versions").json()["items"]) == 3
    assert client.delete(f"{BASE}/{board_id}", params={"revision": row["revision"]}).status_code == 409
    assert client.post(BASE + "/default", json={"board_id": None}).status_code == 200
    assert client.delete(f"{BASE}/{board_id}", params={"revision": row["revision"]}).status_code == 200
    assert client.get(f"{BASE}/{board_id}").status_code == 404
    assert client.get(f"{BASE}/{board_id}/versions/1").status_code == 404


def test_stale_edits_publish_restore_and_delete_conflict(client):
    original = create(client)
    current = publish(client, original)
    assert save(client, original, name="stale").status_code == 409
    for path in ["publish", "versions/1/restore"]:
        assert client.post(f"{BASE}/{original['id']}/{path}", json={"revision": original["revision"]}).status_code == 409
    assert client.delete(f"{BASE}/{original['id']}", params={"revision": original["revision"]}).status_code == 409
    assert client.get(f"{BASE}/{original['id']}").json() == current


@pytest.mark.parametrize("mutation", [
    lambda c: c["theme"].update(background="url(javascript:alert(1))"),
    lambda c: c["theme"].update(font_size=100),
    lambda c: c["widgets"][0].update(type="html"),
    lambda c: c["widgets"][0].update(x=11),
    lambda c: c["widgets"][0].update(h=100),
    lambda c: c["widgets"].append(deepcopy(c["widgets"][0])),
    lambda c: c["widgets"][1].update(y=0),
    lambda c: c.update(script="alert(1)"),
])
def test_invalid_configs_rejected(client, mutation):
    config = deepcopy(boards.BUILTIN)
    mutation(config)
    assert client.post(BASE, json={"name": "invalid", "config": config}).status_code == 422
    assert client.get(BASE).json()["items"] == []


def test_empty_draft_allowed_but_empty_publication_rejected(client):
    config = deepcopy(boards.BUILTIN)
    config["widgets"] = []
    row = create(client, config=config)
    assert client.post(f"{BASE}/{row['id']}/publish", json={"revision": 1}).status_code == 422
    assert client.post(BASE, json={"name": "   "}).status_code == 422


def test_missing_resources_and_independent_copies(client):
    first = create(client)
    second = create(client, config=first["config"])
    first["config"]["theme"]["surface"] = "#aabbcc"
    assert save(client, first).status_code == 200
    assert client.get(f"{BASE}/{second['id']}").json()["config"]["theme"]["surface"] != "#aabbcc"
    assert client.get(f"{BASE}/missing").status_code == 404
    assert client.get(f"{BASE}/{second['id']}/versions/999").status_code == 404
