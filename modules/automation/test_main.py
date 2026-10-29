import concurrent.futures
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from .engine import Semaphore
from .main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOMATION_TOKEN_FILE", raising=False)
    app = create_app(tmp_path, mode="demo")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "admin-token").read_text()
        yield client


def template(**kwargs):
    return {"name": "巡检", "playbook": "playbooks/check.yml", "inventory_id": 1,
            "repository_id": 1, "environment_id": 1, "app": "ansible", **kwargs}


def test_authentication_and_no_secret_echo(client):
    credential = client.headers.pop("Authorization")
    for path in ("state", "summary", "findings", "module"):
        assert client.get("/api/v1/" + path).status_code == 401
    assert client.post("/api/v1/tasks", json={"template_id": 1}).status_code == 401
    assert client.get("/").status_code == 200
    assert client.get("/health/live").status_code == 200
    client.headers["Authorization"] = credential
    assert credential.split()[1] not in client.get("/api/v1/state").text


def test_demo_crud_and_restart_persistence(client, tmp_path):
    response = client.post("/api/v1/templates", json=template(name="新增巡检"))
    assert response.status_code == 201
    item_id = response.json()["id"]
    assert client.put(f"/api/v1/templates/{item_id}", json=template(name="已修改")).status_code == 200
    task = client.post("/api/v1/tasks", json={"template_id": item_id, "dry_run": True}).json()
    assert task["status"] == "success"
    output = client.get(f"/api/v1/tasks/{task['id']}/output").json()
    assert any("未执行脚本" in line for line in output["lines"])
    with TestClient(create_app(tmp_path, mode="demo")) as restarted:
        restarted.headers["Authorization"] = client.headers["Authorization"]
        data = restarted.get("/api/v1/state").json()
        assert next(t for t in data["templates"] if t["id"] == item_id)["name"] == "已修改"
        assert len(data["tasks"]) == 1
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 6


def test_inventory_and_schedule(client):
    response = client.post("/api/v1/inventories", json={"name": "新清单", "inventory": "[demo]\nexample ansible_host=192.0.2.11", "ssh_key_id": 1})
    assert response.status_code == 201
    schedule = client.post("/api/v1/schedules", json={"name": "每日", "template_id": 1, "cron_format": "0 9 * * *"}).json()
    assert not schedule["active"]
    assert client.put(f"/api/v1/schedules/{schedule['id']}/active", json={"active": True}).json()["active"]
    assert client.get("/api/v1/state").json()["tasks"] == []  # Demo schedules never execute.


@pytest.mark.parametrize("body", [template(playbook="../escape.yml"), template(playbook="-i evil"),
    template(playbook="C:/escape.yml"), template(name=""), template(inventory_id=0), template(app="terraform"),
    template(secret="do-not-echo")])
def test_template_trust_boundary(client, body):
    response = client.post("/api/v1/templates", json=body)
    assert response.status_code == 422
    assert "do-not-echo" not in response.text


def test_invalid_reference_and_origin_and_host(client):
    assert client.post("/api/v1/tasks", json={"template_id": 900}).status_code == 404
    assert client.post("/api/v1/templates", json=template(repository_id=800)).status_code == 404
    assert client.post("/api/v1/tasks", json={"template_id": 1}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/v1/state", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/v1/tasks", content='{"template_id":1}', headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/v1/tasks/1/stop", json={}).status_code == 404


def test_terminal_task_cannot_be_stopped(client):
    task = client.post("/api/v1/tasks", json={"template_id": 1}).json()
    assert client.post(f"/api/v1/tasks/{task['id']}/stop", json={}).status_code == 409


def test_store_concurrent_ids(client):
    store = client.app.state.store
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: store.save("tasks", {"name": str(i)}), range(16)))
    assert len({r["id"] for r in results}) == 16


def make_engine(tmp_path, handler):
    token_file = tmp_path / "engine-token"
    token_file.write_text("private-engine-token")
    return Semaphore("https://semaphore.example", 7, str(token_file), httpx.Client(transport=httpx.MockTransport(handler)))


def test_adapter_project_scope_and_secret_filter(tmp_path):
    def handler(request):
        assert request.url.path == "/api/project/7/tasks"
        assert request.headers["Authorization"] == "Bearer private-engine-token"
        return httpx.Response(200, json=[{"id": 1, "status": "success", "secret": "sensitive", "environment": "secret vars"}])
    result = make_engine(tmp_path, handler).list("tasks")
    assert result == [{"id": 1, "status": "success"}]


@pytest.mark.parametrize("response", [httpx.Response(302, headers={"Location": "https://evil.example"}),
    httpx.Response(401, text="private-engine-token"), httpx.Response(200, text="not-json"), httpx.Response(200, json={"wrong": []})])
def test_adapter_rejects_redirect_errors_and_invalid_json(tmp_path, response):
    with pytest.raises(HTTPException) as exc:
        make_engine(tmp_path, lambda request: response).list("tasks")
    assert exc.value.status_code == 502
    assert "private-engine-token" not in exc.value.detail


def test_timeout_never_retries(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout")
    with pytest.raises(HTTPException) as exc:
        make_engine(tmp_path, handler).save("tasks", {"template_id": 1, "dry_run": True})
    assert exc.value.status_code == 504
    assert len(calls) == 1


def test_parameters_reach_ansible_environment_without_shell_args(tmp_path):
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(201, json={'id': 4, 'template_id': 1, 'status': 'waiting'})
    parameters = {'server_id': 'host-uuid', 'message': 'value with spaces; $(touch /tmp/unsafe)', 'count': 3}
    make_engine(tmp_path, handler).save('tasks', {'template_id': 1, 'dry_run': True, 'parameters': parameters})
    assert json.loads(captured[0]['environment']) == parameters
    assert captured[0]['params']['dry_run'] is True
    assert 'arguments' not in captured[0] and 'parameters' not in captured[0]


def test_run_parameters_are_bounded_scalars(client):
    assert client.post('/api/v1/tasks', json={'template_id': 1, 'parameters': {'version': '1.0', 'count': 2}}).status_code == 201
    for parameters in ({'bad-key': 1}, {'nested': {'a': 1}}, {'long': 'a'*16001}):
        assert client.post('/api/v1/tasks', json={'template_id': 1, 'parameters': parameters}).status_code == 422


def test_managed_host_choices_use_allowed_web_gateway_and_exclude_credentials(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.delenv('AUTOMATION_TOKEN_FILE', raising=False)
    app = create_app(tmp_path, mode='semaphore', engine=SimpleNamespace())
    class Source:
        def __init__(self, **kwargs): assert kwargs['trust_env'] is False
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, url, headers):
            assert url == 'http://web/modules/host-inventory/api/v1/hosts'
            assert headers['Authorization'].startswith('Bearer ')
            return httpx.Response(200, request=httpx.Request('GET', url), json={'items': [
                {'id':'host-uuid', 'name':'host', 'host':'192.0.2.1', 'wireguard_ip':'', 'ssh_key':'private-material'}]})
    with TestClient(app) as client:
        monkeypatch.setattr(httpx, 'Client', Source)
        response = client.get('/api/v1/hosts', headers={'Authorization':'Bearer '+(tmp_path/'admin-token').read_text()})
    assert response.status_code == 200
    assert response.json()['items'][0]['id'] == 'host-uuid'
    assert 'private-material' not in response.text and 'ssh_key' not in response.text


def test_live_mode_preserves_hidden_template_and_schedule_fields(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOMATION_TOKEN_FILE", raising=False)
    requests = []
    def handler(request):
        path = request.url.path.rsplit("/", 1)[-1]
        if request.method == "PUT":
            body = json.loads(request.content)
            requests.append(body)
            return httpx.Response(204)
        if path in {"inventory", "repositories", "environment"}:
            return httpx.Response(200, json=[{"id": 1}])
        if path == "templates":
            return httpx.Response(200, json=[{**template(), "id": 1}])
        if path == "schedules":
            return httpx.Response(200, json=[{"id": 1, "template_id": 1, "active": False}])
        if "/templates/" in request.url.path:
            return httpx.Response(200, json={**template(), "id": 1, "vaults": [{"vault_key_id": 77}], "survey_vars": [{"name": "keep"}]})
        return httpx.Response(200, json={"id": 1, "template_id": 1, "task_params": {"environment": "keep"}})
    app = create_app(tmp_path, mode="semaphore", engine=make_engine(tmp_path, handler))
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "admin-token").read_text()
        assert client.put("/api/v1/templates/1", json=template(name="Edited")).status_code == 200
        assert client.put("/api/v1/schedules/1/active", json={"active": True}).status_code == 200
    assert requests[0]["vaults"] == [{"vault_key_id": 77}]
    assert requests[0]["survey_vars"] == [{"name": "keep"}]
    assert requests[1]["task_params"] == {"environment": "keep"}
    assert requests[1]["project_id"] == 7


def test_bad_configuration(tmp_path):
    for url in ("file:///secret", "http://remote.example", "https://user:pass@host", "https://host?token=secret"):
        with pytest.raises(ValueError):
            Semaphore(url, 1, "")
    with pytest.raises(ValueError):
        create_app(tmp_path, mode="typo")


def test_live_run_and_stop_stay_in_project(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOMATION_TOKEN_FILE", raising=False)
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            data = json.loads(request.content)
            if request.url.path.endswith("/stop"):
                assert data == {"force": False}
                return httpx.Response(204)
            assert data["template_id"] == 1 and data["params"]["dry_run"] is True
            assert "dry_run" not in data
            return httpx.Response(201, json={"id": 3, "template_id": 1, "status": "waiting", "secret": "hidden", "params": {"dry_run": True, "password": "hidden"}})
        if request.url.path.endswith("/templates"):
            return httpx.Response(200, json=[{**template(), "id": 1}])
        return httpx.Response(200, json=[{"id": 3, "template_id": 1, "status": "running"}])
    app = create_app(tmp_path, mode="semaphore", engine=make_engine(tmp_path, handler))
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "admin-token").read_text()
        response = client.post("/api/v1/tasks", json={"template_id": 1})
        assert response.status_code == 201 and response.json()["status"] == "waiting"
        assert response.json()["dry_run"] is True
        assert "hidden" not in response.text
        stopped = client.post("/api/v1/tasks/3/stop", json={})
        assert stopped.status_code == 200
        assert stopped.json() == {"accepted": True, "execution_may_continue": True}
    assert all(path.startswith("/api/project/7/") for _, path in seen)


def test_live_outage_does_not_fall_back_to_demo(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOMATION_TOKEN_FILE", raising=False)
    app = create_app(tmp_path, mode="semaphore", engine=make_engine(tmp_path, lambda request: httpx.Response(503)))
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "admin-token").read_text()
        assert client.get("/api/v1/state").status_code == 502
        response = client.get("/api/v1/summary")
        assert response.status_code == 503 and response.json()["status"] == "unavailable"
        assert client.get("/health/ready").status_code == 503
        assert app.state.store.list("templates") == []


def test_adapter_response_limit(tmp_path):
    with pytest.raises(HTTPException) as exc:
        make_engine(tmp_path, lambda request: httpx.Response(200, content=b" " * (4 * 1024 * 1024 + 1))).list("tasks")
    assert exc.value.status_code == 502


def test_workbench_auth_roles_and_audit(tmp_path, monkeypatch):
    import sys
    import time
    import jwt
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    key = Ed25519PrivateKey.generate()
    public = tmp_path / 'public.pem'
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv('OPS_JWT_PUBLIC_KEY_FILE', str(public))
    monkeypatch.delenv('AUTOMATION_TOKEN_FILE', raising=False)
    app = create_app(tmp_path, mode='demo')
    with TestClient(app) as client:
        standalone = (tmp_path / 'admin-token').read_text()
        assert client.get('/api/v1/state', headers={'Authorization': 'Bearer ' + standalone}).status_code == 401
        for role, status in [('viewer', 403), ('editor', 403), ('admin', 200)]:
            token = jwt.encode({'sub':'1','username':'admin','role':role,'iss':'opscenter',
                                'aud':'opscenter-modules','exp':time.time()+60}, key, algorithm='EdDSA')
            client.headers['Authorization'] = 'Bearer ' + token
            assert client.get('/api/v1/state').status_code == status
        assert client.post('/api/v1/templates/1', json=template(name='JWT 编辑')).status_code == 200
        assert client.post('/api/v1/schedules/1/active', json={'active':True}).status_code == 200
        with app.state.store.connect() as db:
            assert {r[0] for r in db.execute('SELECT DISTINCT actor FROM audit')} == {'admin'}
        assert '../static/app.js' in client.get('/ui/').text


@pytest.mark.parametrize("app,suffix", [("python", ".py"), ("bash", ".sh")])
def test_native_script_template_edit_run_and_schedule(client, app, suffix):
    body = template(app=app, playbook="scripts/example" + suffix)
    body.pop("inventory_id")
    response = client.post("/api/v1/templates", json=body)
    assert response.status_code == 201
    row = response.json(); item_id = row["id"]
    assert row["inventory_id"] is None and row["allow_override_args_in_task"] is True
    assert client.put(f"/api/v1/templates/{item_id}", json={**body, "name": "Edited"}).status_code == 200
    assert client.post("/api/v1/tasks", json={"template_id": item_id}).status_code == 422
    assert client.post("/api/v1/tasks", json={"template_id": item_id, "dry_run": False, "parameters": {"text": "hello"}}).status_code == 201
    assert client.post("/api/v1/schedules", json={"name": "paused", "template_id": item_id, "cron_format": "0 9 * * *"}).status_code == 201
    assert client.put(f"/api/v1/templates/{item_id}", json=template()).status_code == 409


@pytest.mark.parametrize("app,path", [("python", "script.sh"), ("bash", "script.py"), ("ansible", "script.py")])
def test_script_type_requires_matching_file_extension(client, app, path):
    assert client.post("/api/v1/templates", json=template(app=app, playbook=path)).status_code == 422


@pytest.mark.parametrize("app", ["python", "bash"])
def test_native_parameters_are_one_json_argument(tmp_path, app):
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 9})
    parameters = {"text": "空格 ; $(touch /tmp/unwanted) \" quotes", "count": 2, "flag": False}
    make_engine(tmp_path, handler).save("tasks", {"template_id": 7, "dry_run": False, "parameters": parameters}, app_type=app)
    assert json.loads(seen[0]["arguments"]) == [json.dumps(parameters, ensure_ascii=False)]
    assert "environment" not in seen[0] and "parameters" not in seen[0]
