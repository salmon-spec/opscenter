"""Security and execution-contract checks; run separately from other modules."""
import importlib.util
import ast
import shutil
import subprocess
import re
from html.parser import HTMLParser
import json
import sys
import time
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
spec = importlib.util.spec_from_file_location("opencode_gateway", HERE / "main.py")
main = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = main
spec.loader.exec_module(main)


@pytest.fixture(autouse=True)
def reset_transport_and_catalog(monkeypatch):
    main.invalidate_catalog()
    monkeypatch.setattr(main, "_client", None)
    monkeypatch.setattr(main, "start_live_updates", lambda: None)
    main._live_parts.clear();main._live_roles.clear()
    main._live_dirty.clear();main._live_revision=0
    yield
    if main._client is not None: main._client.close()


@pytest.fixture
def env(monkeypatch, tmp_path):
    private = Ed25519PrivateKey.generate()
    public = tmp_path / "public.pem"
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv("OPS_JWT_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("OPS_ADMIN_USER", "admin")
    monkeypatch.setenv("MODULE_AUTH_REQUIRED", "false")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path / "data")
    def token(role="admin"):
        value = jwt.encode({"sub": "1", "username": "admin", "role": role, "exp": time.time()+300,
                            "iss": "opscenter", "aud": "opscenter-modules"}, private, algorithm="EdDSA")
        return {"Authorization": "Bearer " + value}
    native_config = tmp_path / "native-config" / "opencode.jsonc"
    native_config.parent.mkdir()
    monkeypatch.setenv("OPENCODE_NATIVE_CONFIG_FILE", str(native_config))
    calls = []
    def upstream(method, path, body=None, *, params=None):
        calls.append((method, path, body))
        if path == "/global/config" and method == "GET": return json.loads(native_config.read_text()) if native_config.exists() else {}
        if path == "/provider":
            return {"connected": ["deepseek"], "all": [
                {"id": "deepseek", "key": "never-expose", "models": {"model-test": {"name": "Test", "secret": "hidden"}}},
                {"id": "another", "models": {"x": {}}}]}
        if path == "/project/current":return {"vcs":"git"}
        if path == "/pty" and method == "POST":return {"id":"pty_setup"}
        if path.startswith("/pty/") and method == "GET":return {"status":"exited"}
        if path == "/pty": return [{"id":"pty_test","cwd":main.directory()}]
        if path == "/agent": return [{"name":"build","mode":"primary"},{"name":"plan","mode":"primary"}]
        if path == "/session/status": return {}
        if path == "/session": return {"id": "ses_test", "title": body["title"]}
        if path == "/global/health": return {"healthy": True, "version": "1.19.3"}
        if path in {"/permission", "/question"}: return []
        return True
    monkeypatch.setattr(main, "upstream", upstream)
    with main.connect() as db:
        db.execute("INSERT INTO sessions(id,title) VALUES ('ses_test','Test')")
    return TestClient(main.app), token, calls, upstream


def test_all_business_routes_fail_closed_even_without_optional_middleware(env, monkeypatch):
    client, token, calls, _ = env
    for path in ["/api/v1/status", "/api/v1/sessions", "/api/v1/module", "/api/v1/summary", "/api/v1/audit"]:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=token("viewer")).status_code == 403
    assert not calls
    assert client.get("/health/live").status_code == 200
    monkeypatch.delenv("OPS_JWT_PUBLIC_KEY_FILE")
    monkeypatch.delenv("OPS_JWT_SECRET", raising=False)
    assert client.get("/api/v1/status", headers=token()).status_code == 503


def test_provider_allowlist_and_secret_free_status_and_audit(env):
    client, token, calls, _ = env
    status = client.get("/api/v1/status", headers=token())
    assert status.status_code == 200
    assert status.json()["models"] == [{"provider": "deepseek", "id": "model-test", "name": "Test"}]
    key = "test-secret-no-record"
    result = client.post("/api/v1/config/deepseek", headers=token(), json={"api_key": key})
    assert result.status_code == 200
    assert ("PUT", "/auth/deepseek", {"type": "api", "key": key}) in calls
    assert calls[calls.index(("PUT", "/auth/deepseek", {"type": "api", "key": key}))+1] == ("POST", "/instance/dispose", None)
    assert key not in result.text + client.get("/api/v1/audit", headers=token()).text
    assert "never-expose" not in status.text
    assert client.post("/api/v1/config/deepseek", headers=token(), json={"api_key": key, "base_url": "http://evil"}).status_code == 422
    invalid = client.post("/api/v1/config/deepseek", headers=token(), json={"api_key": "tiny"})
    assert invalid.status_code == 422 and "tiny" not in invalid.text
    assert client.post("/api/v1/config/unknown", headers=token(), json={"api_key": key}).status_code == 422


def test_catalog_single_fetch_scoped_to_workspace_and_invalidated_after_key_change(env):
    client, token, calls, _ = env
    headers = token()
    for path in ["/status", "/providers", "/capabilities", "/status"]:
        assert client.get("/api/v1"+path, headers=headers).status_code == 200
    assert sum(path == "/provider" for _, path, _ in calls) == 1
    assert "never-expose" not in json.dumps(main._catalog_cache)
    key=main.directory();_,data=main._catalog_cache[key];main._catalog_cache[key]=(time.monotonic()-120,data)
    assert main.models()
    assert sum(path == "/provider" for _, path, _ in calls) == 1
    assert client.get("/api/v1/providers",headers=headers).status_code==200
    assert sum(path == "/provider" for _, path, _ in calls) == 2
    with main.connect() as db: db.execute("INSERT INTO workspaces VALUES ('cache-test','Cache test')")
    assert client.get("/api/v1/status?workspace=cache-test", headers=headers).status_code == 200
    assert sum(path == "/provider" for _, path, _ in calls) == 3
    assert client.post("/api/v1/config/deepseek", headers=headers, json={"api_key":"test-secret-cache"}).status_code == 200
    assert client.get("/api/v1/status", headers=headers).status_code == 200
    assert sum(path == "/provider" for _, path, _ in calls) == 4


def test_light_state_omits_expensive_panels_and_does_not_take_mutation_lock(env, monkeypatch):
    client, token, calls, original = env
    def upstream(method,path,body=None,**kwargs):
        if path.endswith("/message"): return []
        return original(method,path,body,**kwargs)
    monkeypatch.setattr(main,"upstream",upstream)
    class NoLock:
        def __enter__(self): raise AssertionError("Polling must not block execution controls")
        def __exit__(self,*args): pass
    monkeypatch.setattr(main,"_lock",NoLock())
    r=client.get("/api/v1/sessions/ses_test/state?details=false",headers=token())
    assert r.status_code == 200
    assert not {"diff","todo","children"} & set(r.json())
    assert not any(path.endswith(("/diff","/todo","/children")) for _,path,_ in calls)


def test_transport_reuses_client_but_reloads_rotated_password(monkeypatch,tmp_path):
    password=tmp_path/"password";password.write_text("first-password")
    monkeypatch.setenv("OPENCODE_RUNNER_PASSWORD_FILE",str(password))
    real_client=httpx.Client;created=[];headers=[]
    def respond(request):headers.append(request.headers["authorization"]);return httpx.Response(200,json={})
    def factory(**kwargs):
        created.append(True)
        return real_client(**kwargs,transport=httpx.MockTransport(respond))
    monkeypatch.setattr(main.httpx,"Client",factory)
    main.upstream("GET","/global/health");password.write_text("second-password");main.upstream("GET","/global/health")
    assert len(created)==1 and headers[0]!=headers[1]


def test_live_text_merges_native_deltas_without_cross_workspace_or_attachment_data(env):
    def event(kind,properties,folder=None):
        main.receive_live_event({'directory':folder or main.directory(),'payload':{'type':kind,'properties':properties}})
    event('message.updated',{'info':{'id':'msg_stream','role':'assistant'}})
    part={'id':'prt_live','messageID':'msg_stream','sessionID':'ses_test','type':'text','text':''}
    event('message.part.updated',{'part':part})
    event('message.part.delta',{'partID':'prt_live','messageID':'msg_stream','sessionID':'ses_test','field':'text','delta':'Hello'})
    messages=main.merge_live_text([], 'ses_test')
    assert messages[0]['parts'][0]['text']=='Hello'
    assert not main.merge_live_text([], 'ses_other')
    scope=main._workspace.set('another')
    try:assert not main.merge_live_text([], 'ses_test')
    finally:main._workspace.reset(scope)
    event('message.part.updated',{'part':{**part,'id':'private','type':'file','url':'data:secret'}})
    assert 'secret' not in json.dumps(list(main._live_parts.values()))
    event('message.updated',{'info':{'id':'msg_stream','role':'assistant','time':{'completed':1}}})
    assert not main._live_parts


def test_gateway_database_uses_wal_without_reducing_durability(env):
    with main.connect() as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
        assert db.execute('PRAGMA synchronous').fetchone()[0]==2


def test_native_task_access_full_then_ask_is_scoped_and_revokes_instance_grants(env,monkeypatch):
    client,token,calls,original=env;permissions=[]
    def upstream(method,path,body=None,**kwargs):
        nonlocal permissions
        if path=='/session' and method=='POST':
            permissions=body['permission'];return {'id':'ses_full','title':body['title']}
        if path=='/session/ses_full' and method=='PATCH':permissions+=body['permission']
        if path=='/session/ses_full' and method=='GET':return {'id':'ses_full','permission':permissions}
        return original(method,path,body,**kwargs)
    monkeypatch.setattr(main,'upstream',upstream)
    response=client.post('/api/v1/sessions',headers=token(),json={'title':'Full test','permission_mode':'full'})
    assert response.status_code==200 and permissions==main.access_rules('full')
    assert client.post('/api/v1/sessions/ses_other/access',headers=token(),json={'mode':'full'}).status_code==404
    assert client.post('/api/v1/sessions/ses_full/access',headers=token(),json={'mode':'ask'}).status_code==200
    assert main.access_mode({'permission':permissions})=='ask'
    assert permissions[-len(main.access_rules('ask')):]==main.access_rules('ask')
    assert client.post('/api/v1/sessions/ses_full/prompt',headers=token(),json={'provider':'deepseek','model':'model-test','agent':'build','text':'Next command'}).status_code==200
    assert main.access_mode({'permission':permissions})=='ask'
    assert ('POST','/instance/dispose',None) in calls
    assert client.post('/api/v1/sessions/ses_full/access',json={'mode':'full'}).status_code==401


def test_live_channel_does_not_fetch_history_and_is_session_bound(env):
    client,token,calls,_=env
    folder=main.directory()
    main.receive_live_event({'directory':folder,'payload':{'type':'message.updated','properties':{'info':{'id':'msg_live','role':'assistant'}}}})
    main.receive_live_event({'directory':folder,'payload':{'type':'message.part.updated','properties':{'part':{'id':'prt_live','messageID':'msg_live','sessionID':'ses_test','type':'text','text':'streaming'}}}})
    r=client.get('/api/v1/sessions/ses_test/live',headers=token());assert r.status_code==200
    assert r.json()['parts'][0]['text']=='streaming' and not calls
    assert not client.get('/api/v1/sessions/ses_test/live?cursor='+str(r.json()['cursor']),headers=token()).json()['parts']
    assert client.get('/api/v1/sessions/ses_other/live',headers=token()).status_code==404


def test_preferences_remember_model_agent_and_access_with_workspace_isolation(env):
    client,token,_,_=env;headers=token();body={'provider':'deepseek','model':'model-test','agent':'build','permission_mode':'full'}
    assert client.post('/api/v1/preferences',headers=headers,json=body).status_code==200
    assert client.get('/api/v1/preferences',headers=headers).json()['permission_mode']=='full'
    with main.connect() as db:db.execute("INSERT INTO workspaces VALUES ('prefs-test','Preferences')")
    assert client.get('/api/v1/preferences?workspace=prefs-test',headers=headers).json()=={}
    assert client.post('/api/v1/preferences',headers=headers,json={**body,'provider':'unknown'}).status_code==422
    assert client.post('/api/v1/preferences',headers=headers,json={**body,'api_key':'private'}).status_code==422


def test_plan_has_write_and_terminal_denials_even_with_full_access():
    rules=main.access_rules('full','plan')
    assert main.access_mode({'permission':rules})=='full'
    assert {r['permission'] for r in rules if r['action']=='deny'}=={'edit','bash','shell','task'}


def test_prompt_is_async_audited_and_cannot_override_execution_scope(env):
    client, token, calls, _ = env
    body = {"text": "private-task-text", "provider": "deepseek", "model": "model-test"}
    result = client.post("/api/v1/sessions/ses_test/prompt", headers=token(), json=body)
    assert result.status_code == 200
    sent = next(c for c in calls if c[1].endswith("prompt_async"))
    assert sent[2]["parts"] == [{"type": "text", "text": body["text"]}]
    assert body["text"] not in client.get("/api/v1/audit", headers=token()).text
    assert client.post("/api/v1/sessions/ses_test/prompt", headers=token(), json={**body, "directory": "/etc"}).status_code == 422
    assert client.post("/api/v1/sessions/ses_other/prompt", headers=token(), json=body).status_code == 404
    assert client.post("/api/v1/sessions/ses_test/prompt", headers=token(), json={**body, "model": "unconnected"}).status_code == 422
    assert client.get("/api/v1/proxy/config", headers=token()).status_code == 404


def test_busy_session_rejects_duplicate_submission_and_credential_change(env, monkeypatch):
    client, token, calls, original = env
    monkeypatch.setattr(main, "upstream", lambda method,path,body=None,**kwargs:
                        {"ses_test": {"type": "busy"}} if path == "/session/status" else original(method,path,body,**kwargs))
    assert client.post("/api/v1/sessions/ses_test/prompt", headers=token(), json={"text":"test","provider":"deepseek","model":"model-test"}).status_code == 409
    assert client.post("/api/v1/config/deepseek", headers=token(), json={"api_key":"test-secret"}).status_code == 409
    assert client.post("/api/v1/sessions/ses_test/access", headers=token(), json={"mode":"full"}).status_code == 409
    assert not any(c[0] in {"POST", "PUT"} for c in calls)


def test_permission_is_session_bound(env, monkeypatch):
    client, token, calls, original = env
    monkeypatch.setattr(main, "upstream", lambda method,path,body=None,**kwargs:
        [{"id":"per_test","sessionID":"ses_test"}] if path == "/permission" else original(method,path,body,**kwargs))
    path = "/api/v1/sessions/ses_test/permissions/"
    assert client.post(path+"per_test", headers=token(), json={"reply":"always"}).status_code == 422
    assert client.post(path+"per_other", headers=token(), json={"reply":"once"}).status_code == 404
    assert client.post(path+"per_test", headers=token(), json={"reply":"once"}).status_code == 200
    assert ("POST", "/permission/per_test/reply", {"reply":"once"}) in calls
    assert client.post("/api/v1/sessions/ses_test/stop", headers=token()).status_code == 200
    assert ("POST", "/session/ses_test/abort", None) in calls


def test_runner_errors_do_not_leak_response_or_credentials(monkeypatch, tmp_path):
    password = tmp_path / "password"
    password.write_text("sensitive-runner-password")
    monkeypatch.setenv("OPENCODE_RUNNER_PASSWORD_FILE", str(password))
    real_client = httpx.Client
    def factory(**kwargs):
        return real_client(**kwargs, transport=httpx.MockTransport(lambda req: httpx.Response(500, text="secret-error-details")))
    monkeypatch.setattr(main.httpx, "Client", factory)
    with pytest.raises(HTTPException) as caught:
        main.upstream("GET", "/provider")
    assert caught.value.status_code == 502
    assert "secret" not in caught.value.detail


def test_state_sanitizes_provider_error_and_filters_pending_requests(env, monkeypatch):
    client, token, _, original = env
    def upstream(method, path, body=None, **kwargs):
        if path.endswith("/message"):
            return [{"info":{"role":"assistant","error":{"name":"APIError","data":{
                "message":"Key expired","statusCode":401,"responseHeaders":{"secret":"hidden"},"responseBody":"private"}}},"parts":[]}]
        if path == "/permission":
            return [{"id":"per_a","sessionID":"ses_test"},{"id":"per_b","sessionID":"ses_other"}]
        if path.endswith("/diff"):return []
        return original(method, path, body, **kwargs)
    monkeypatch.setattr(main, "upstream", upstream)
    result = client.get("/api/v1/sessions/ses_test/state", headers=token())
    assert result.status_code == 200
    assert "private" not in result.text and "hidden" not in result.text
    assert [p["id"] for p in result.json()["permissions"]] == ["per_a"]


def test_question_requires_matching_session_and_answer_count(env, monkeypatch):
    client, token, calls, original = env
    monkeypatch.setattr(main, "upstream", lambda method,path,body=None,**kwargs:
        [{"id":"que_test","sessionID":"ses_test","questions":[{"question":"Which?"}]}] if path == "/question" else original(method,path,body,**kwargs))
    path = "/api/v1/sessions/ses_test/questions/que_test"
    assert client.post(path, headers=token(), json={"answers":[]}).status_code == 422
    assert client.post(path, headers=token(), json={"answers":[["A"]]}).status_code == 200
    assert ("POST", "/question/que_test/reply", {"answers":[["A"]]}) in calls


def test_page_and_runner_defaults():
    config = json.loads((HERE / "opencode.json").read_text(encoding="utf-8"))
    assert config["permission"]["*"] == "ask"
    assert config["permission"]["external_directory"] == "deny"
    assert config["share"] == "disabled"
    html = (HERE / "index.html").read_text(encoding="utf-8")
    assert "innerHTML" not in html and "textContent" in html
    assert "data.nonce===nonce" in html
    response = TestClient(main.app).get("/ui/")
    assert "form-action 'none'" in response.headers["content-security-policy"]


def test_workspace_page_wiring_and_form_structure():
    class Page(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []
            self.parents = {}
            self.stack = []
        def handle_starttag(self, tag, attributes):
            attrs = dict(attributes)
            if attrs.get("id"):
                self.ids.append(attrs["id"])
                self.parents[attrs["id"]] = list(self.stack)
            if tag not in {"meta", "input", "br"}:
                self.stack.append(attrs.get("id") or tag)
        def handle_endtag(self, tag):
            if self.stack:self.stack.pop()
    html = (HERE / "index.html").read_text(encoding="utf-8")
    page = Page()
    page.feed(html)
    assert len(page.ids) == len(set(page.ids))
    assert set(re.findall(r"\$\('([^']+)'\)", html)) <= set(page.ids)
    assert "thread" not in page.parents["composer"]
    assert "settings" in page.parents["config"]
    assert "inspector" in page.parents["diff"]


def test_registry_and_runtime_contract_match():
    root = HERE.parent.parent
    tree = ast.parse((root / "backend/app/extension_modules.py").read_text(encoding="utf-8"))
    registry = next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "BUILTIN_MODULES" for t in node.targets))
    entry = registry["opencode-agent"]
    assert entry["version"] == main.VERSION
    assert entry["entry_url"] == "/modules/opencode-agent/ui/"
    assert entry["manifest"]["api_prefixes"] == ["/modules/opencode-agent/api/v1/"]
    assert entry["manifest"]["sandbox"] == ["allow-scripts", "allow-forms"]
    dockerfile = (root / "deploy/docker/opencode-runner.Dockerfile").read_text()
    assert "OPENCODE_VERSION=1.19.3" in dockerfile and "USER node" in dockerfile


def test_runner_entrypoint_fails_closed_without_password(tmp_path):
    shell = shutil.which("sh") or shutil.which("bash")
    if not shell:
        pytest.skip("POSIX shell is required for the runner entrypoint check")
    dockerfile = (HERE.parent.parent / "deploy/docker/opencode-runner.Dockerfile").read_text()
    command = json.loads(next(line[4:] for line in dockerfile.splitlines() if line.startswith("CMD ")))[2]
    password = tmp_path / "password"
    command = command.replace("/run/secrets/runner_password", "'" + password.as_posix() + "'")
    command = command[:command.index("exec opencode-runner")] + "printf runner-started"
    for content in (None, "", "test-runner-password"):
        if content is not None:
            password.write_text(content)
        result = subprocess.run([shell, "-c", command], capture_output=True, text=True)
        assert (result.returncode == 0) == bool(content)
        assert ("runner-started" in result.stdout) == bool(content)


def test_idle_status_entries_allow_key_update(env, monkeypatch):
    client, token, _, original = env
    monkeypatch.setattr(main, "upstream", lambda method,path,body=None,**kwargs:
        {"ses_test":{"type":"idle"}} if path == "/session/status" else original(method,path,body,**kwargs))
    assert client.post("/api/v1/config/deepseek", headers=token(), json={"api_key":"test-secret"}).status_code == 200


def test_runner_failure_marks_readiness_unavailable(env, monkeypatch):
    client, token, _, _ = env
    def unavailable(*args, **kwargs):
        raise HTTPException(502, "unavailable")
    monkeypatch.setattr(main, "upstream", unavailable)
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503
    assert client.get("/api/v1/summary", headers=token()).json()["status"] == "unavailable"
    assert client.get("/api/v1/status", headers=token()).status_code == 502


@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout("private-details"),504),
                                       (httpx.ConnectError("private-details"),502)])
def test_transport_errors_are_bounded_and_sanitized(monkeypatch, tmp_path, error, code):
    password = tmp_path / "password"
    password.write_text("test-password")
    monkeypatch.setenv("OPENCODE_RUNNER_PASSWORD_FILE", str(password))
    real_client = httpx.Client
    def fail(request):raise error
    monkeypatch.setattr(main.httpx, "Client", lambda **kwargs:
        real_client(**kwargs, transport=httpx.MockTransport(fail)))
    with pytest.raises(HTTPException) as caught:
        main.upstream("POST", "/session")
    assert caught.value.status_code == code and "private-details" not in caught.value.detail



def test_session_edit_archive_and_delete_keep_native_and_index_in_sync(env):
    client, token, calls, _ = env
    path = "/api/v1/sessions/ses_test"
    assert client.post(path+"/edit", headers=token(), json={"title":"Renamed","archived":True}).status_code == 200
    assert client.get("/api/v1/sessions", headers=token()).json()["items"] == []
    assert client.get("/api/v1/sessions?archived=true", headers=token()).json()["items"][0]["title"] == "Renamed"
    assert client.post(path+"/edit", headers=token(), json={"archived":False}).status_code == 200
    assert client.post(path+"/delete", headers=token()).status_code == 200
    assert ("DELETE", "/session/ses_test", None) in calls
    assert client.post(path+"/stop", headers=token()).status_code == 404


def test_files_preserve_whitespace_and_reject_traversal_and_symlinks(env, monkeypatch, tmp_path):
    client, token, _, _ = env
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(main, "WORKSPACE", str(workspace))
    text = "  first\n    second\n"
    body = {"path":"nested/a.txt", "content":text}
    assert client.post("/api/v1/files/write", headers=token(), json=body).status_code == 200
    assert client.get("/api/v1/files/content?path=nested/a.txt", headers=token()).json()["content"] == text
    assert client.post("/api/v1/files/write", headers=token(), json=body).status_code == 409
    assert client.post("/api/v1/files/write", headers=token(), json={**body,"overwrite":True}).status_code == 200
    for path in ("../secret", "/etc/passwd", "a/../../secret", "a\\secret"):
        assert client.get("/api/v1/files/content", params={"path":path}, headers=token()).status_code == 422
    try: (workspace / "outside").symlink_to(tmp_path, target_is_directory=True)
    except OSError: pass
    else: assert client.get("/api/v1/files", params={"path":"outside"}, headers=token()).status_code == 422


def test_prompt_supports_plan_and_file_context_without_overriding_directory(env, monkeypatch, tmp_path):
    client, token, calls, _ = env
    monkeypatch.setattr(main, "WORKSPACE", str(tmp_path))
    (tmp_path / "notes.txt").write_text("context", encoding="utf-8")
    response = client.post("/api/v1/sessions/ses_test/prompt", headers=token(), json={
        "text":"review", "provider":"deepseek", "model":"model-test", "agent":"plan", "attachments":[{"path":"notes.txt"}]})
    assert response.status_code == 200
    payload = next(c[2] for c in calls if c[1].endswith("prompt_async"))
    assert payload["agent"] == "plan"
    assert payload["parts"][1] == {"type":"file","filename":"notes.txt","mime":"text/plain","url":(tmp_path/"notes.txt").as_uri()}
    assert "directory" not in payload


def test_extension_config_uses_global_config_and_does_not_accept_security_overrides(env):
    client, token, calls, _ = env
    body = {"agents":{"reviewer":{"description":"Review","prompt":"Review only","mode":"primary"}},
            "commands":{"review":{"template":"Review $ARGUMENTS"}},
            "mcp":{"local":{"type":"local","command":["python3","server.py"],"enabled":False}}}
    assert client.post("/api/v1/settings", headers=token(), json=body).status_code == 200
    assert any(c[0] == "PATCH" and c[1] == "/global/config" for c in calls)
    assert client.get("/api/v1/settings", headers=token()).json() == {"plugins":[],**body}
    for invalid in ({"provider":{"evil":{}}}, {"agents":{"evil":{"permission":"allow"}}},
                    {"mcp":{"evil":{"type":"remote","url":"https://user:secret@example.com"}}},
                    {"mcp":{"evil":{"type":"local","command":["sh"],"environment":{"KEY":"secret"}}}}):
        assert client.post("/api/v1/settings", headers=token(), json=invalid).status_code == 422


def test_fork_is_registered_and_unknown_message_cannot_cross_session(env, monkeypatch):
    client, token, _, original = env
    def native(method,path,body=None,**kwargs):
        if path.endswith("/fork"): return {"id":"ses_fork","title":"Fork"}
        if path.endswith("/message/msg_unknown"):raise HTTPException(404,"missing")
        return original(method,path,body,**kwargs)
    monkeypatch.setattr(main,"upstream",native)
    assert client.post("/api/v1/sessions/ses_test/fork",headers=token(),json={}).json()["id"] == "ses_fork"
    assert client.post("/api/v1/sessions/ses_fork/stop",headers=token()).status_code == 200
    assert client.post("/api/v1/sessions/ses_test/revert",headers=token(),json={"message_id":"msg_unknown"}).status_code == 404


def test_terminal_input_keeps_newline_and_requires_registered_terminal(env, monkeypatch):
    client, token, _, _ = env
    received=[]
    class Stream:
        def send(self,data):received.append(data)
    def stream(terminal_id):
        main.terminal_id(terminal_id)
        return Stream()
    monkeypatch.setattr(main,"terminal_stream",stream)
    with main.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS terminals(id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO terminals VALUES ('pty_test')")
    assert client.post("/api/v1/terminals/pty_test/input",headers=token(),json={"data":"echo hi\r"}).status_code == 200
    assert received == ["echo hi\r"]
    assert client.post("/api/v1/terminals/pty_other/input",headers=token(),json={"data":"x"}).status_code == 404
    assert "echo hi" not in client.get("/api/v1/audit",headers=token()).text


def test_new_routes_require_admin_and_reject_raw_proxy(env):
    client, token, _, _ = env
    for method,path in [("GET","/api/v1/files"),("POST","/api/v1/files/write"),("GET","/api/v1/capabilities"),
                        ("GET","/api/v1/settings"),("POST","/api/v1/settings"),("POST","/api/v1/terminals")]:
        assert client.request(method,path,json={}).status_code == 401
        assert client.request(method,path,json={},headers=token("viewer")).status_code == 403
    assert client.get("/api/v1/global/config",headers=token()).status_code == 404



def test_workspace_context_scopes_sessions_and_resets_after_request(env, monkeypatch, tmp_path):
    client, token, _, original = env
    monkeypatch.setattr(main,"WORKSPACE",str(tmp_path))
    assert client.post("/api/v1/workspaces",headers=token(),json={"name":"scripts","title":"Scripts"}).status_code == 200
    assert client.post("/api/v1/sessions/ses_test/stop?workspace=scripts",headers=token()).status_code == 404
    assert client.get("/api/v1/sessions?workspace=scripts",headers=token()).json()["items"] == []
    observed=[]
    def native(method,path,body=None,**kwargs):
        observed.append(main.directory())
        return original(method,path,body,**kwargs)
    monkeypatch.setattr(main,"upstream",native)
    assert client.get("/api/v1/status?workspace=scripts",headers=token()).status_code == 200
    assert all(Path(p)==tmp_path/'projects'/'scripts' for p in observed)
    assert main.directory() == str(tmp_path)
    assert client.get("/api/v1/status?workspace=../etc",headers=token()).status_code == 422
    assert client.get("/api/v1/status?workspace=unknown",headers=token()).status_code == 404


def test_legacy_session_database_migration_preserves_history(env, monkeypatch, tmp_path):
    import sqlite3
    client, token, _, _ = env
    path=tmp_path/'legacy';path.mkdir()
    with sqlite3.connect(path/'gateway.sqlite') as db:
        db.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT NOT NULL)")
        db.execute("INSERT INTO sessions VALUES ('ses_legacy','Original')")
    monkeypatch.setattr(main,"DATA_DIR",path)
    result=client.get("/api/v1/sessions",headers=token())
    assert result.status_code == 200 and result.json()["items"] == [{"id":"ses_legacy","title":"Original","archived":0}]


def test_background_command_holds_submission_lock_until_native_finishes(env, monkeypatch):
    import threading
    client, token, _, original = env
    entered, release=threading.Event(),threading.Event()
    def native(method,path,body=None,**kwargs):
        if path=="/command":return [{"name":"check"}]
        if path.endswith("/command"):
            entered.set();release.wait(2);return {"done":True}
        return original(method,path,body,**kwargs)
    monkeypatch.setattr(main,"upstream",native)
    monkeypatch.setattr(main,"_operations",{})
    body={"command":"check","provider":"deepseek","model":"model-test"}
    try:
        result=client.post("/api/v1/sessions/ses_test/command",headers=token(),json=body)
        assert result.status_code==200 and result.json()["accepted"]
        assert entered.wait(1)
        assert client.post("/api/v1/sessions/ses_test/compact",headers=token(),json={"provider":"deepseek","model":"model-test"}).status_code==409
    finally:release.set()



def test_native_ui_attaches_existing_engine_without_sending_password_to_browser(env, monkeypatch):
    client, token, calls, original = env
    def native(method,path,body=None,**kwargs):
        if path=="/pty" and method=="GET":return []
        if path=="/pty" and method=="POST":
            assert body["args"]==["attach",main.RUNNER_URL,"--dir",main.directory(),"--session","ses_test"]
            assert body["command"]=="opencode-runner" and "env" not in body
            return {"id":"pty_native","title":body["title"],"cwd":body["cwd"]}
        return original(method,path,body,**kwargs)
    monkeypatch.setattr(main,"upstream",native)
    response=client.post("/api/v1/terminals",headers=token(),json={"kind":"opencode","session_id":"ses_test"})
    assert response.status_code==200
    assert response.json()["id"]=="pty_native"
    assert client.post("/api/v1/terminals",headers=token(),json={"kind":"opencode","session_id":"ses_other"}).status_code==404
    assert "password" not in response.text.lower()


def test_native_history_import_rejects_foreign_directories(env, monkeypatch, tmp_path):
    client, token, _, original = env
    monkeypatch.setattr(main,"WORKSPACE",str(tmp_path/'workspace'))
    def native(method,path,body=None,**kwargs):
        if path=="/session" and method=="GET":return [
            {"id":"ses_native","title":"Native task","directory":main.directory(),"time":{}},
            {"id":"ses_foreign","title":"Other","directory":str(tmp_path/'other'),"time":{}}]
        return original(method,path,body,**kwargs)
    monkeypatch.setattr(main,"upstream",native)
    assert client.post("/api/v1/sessions/import",headers=token()).json()=={"imported":1}
    ids={s['id'] for s in client.get("/api/v1/sessions",headers=token()).json()["items"]}
    assert "ses_native" in ids and "ses_foreign" not in ids



def test_extension_replacement_removes_stale_fields_and_reenables_readded_agent(env):
    client, token, calls, _ = env
    first={"agents":{"reviewer":{"mode":"primary","prompt":"Review","disable":True}},"commands":{"review":{"template":"Review"}}}
    assert client.post("/api/v1/settings",headers=token(),json=first).status_code==200
    assert client.post("/api/v1/settings",headers=token(),json={}).status_code==200
    assert client.post("/api/v1/settings",headers=token(),json={"agents":{"reviewer":{"prompt":"Review again"}}}).status_code==200
    import os
    native=Path(os.environ["OPENCODE_NATIVE_CONFIG_FILE"])
    data=json.loads(native.read_text())
    assert data['agent']['reviewer']=={"prompt":"Review again"} and not data['command']
    assert native.name=="opencode.jsonc" and not native.with_suffix('.json').exists()
    assert any(c[1]=='/global/dispose' for c in calls)
    assert ('PATCH','/global/config',{'$schema':'https://opencode.ai/config.json'}) in calls



def test_terminal_capacity_applies_across_workspaces(env, monkeypatch):
    client, token, _, original = env
    monkeypatch.setattr(main,"upstream",lambda method,path,body=None,**kwargs:
        [{"id":"pty_"+str(i),"cwd":"/other-workspace"} for i in range(4)] if path=="/pty" else original(method,path,body,**kwargs))
    assert client.post("/api/v1/terminals",headers=token(),json={}).status_code==409



def test_git_initialization_uses_fixed_native_route_and_respects_busy_state(env, monkeypatch):
    client, token, calls, original = env
    assert client.post("/api/v1/vcs/init",headers=token()).status_code==200
    assert ("POST","/project/git/init",None) in calls
    setup=next(c[2] for c in calls if c[0]=='POST' and c[1]=='/pty')
    assert setup['command']=='git' and setup['args'][:4]==['config','--global','--add','safe.directory']
    assert setup['args'][4]==str(Path(main.WORKSPACE).resolve()) and '*' not in setup['args'][4]
    assert ('DELETE','/pty/pty_setup',None) in calls
    assert ("POST","/instance/dispose",None) in calls
    monkeypatch.setattr(main,"upstream",lambda method,path,body=None,**kwargs:
        {"ses_test":{"type":"busy"}} if path=="/session/status" else original(method,path,body,**kwargs))
    before=len([c for c in calls if c[1]=="/project/git/init"])
    assert client.post("/api/v1/vcs/init",headers=token()).status_code==409
    assert len([c for c in calls if c[1]=="/project/git/init"])==before



def test_background_native_calls_allow_long_reads_but_keep_control_calls_bounded(monkeypatch,tmp_path):
    password=tmp_path/'password';password.write_text('test-password')
    monkeypatch.setenv('OPENCODE_RUNNER_PASSWORD_FILE',str(password))
    observed=[];real=httpx.Client
    def receive(request):
        observed.append(request.extensions['timeout'])
        return httpx.Response(200,json=True)
    monkeypatch.setattr(main.httpx,'Client',lambda **kwargs:real(**kwargs,transport=httpx.MockTransport(receive)))
    main.upstream('GET','/global/health')
    token=main._long_operation.set(True)
    try:main.upstream('POST','/session/ses_test/command',{})
    finally:main._long_operation.reset(token)
    assert observed[0]['read']==20 and observed[1]['read']==1200
    assert observed[1]['connect']==20 and observed[1]['write']==20



def test_state_bounds_attachment_history_and_removes_inline_file_bodies(env, monkeypatch):
    client, token, _, original = env
    limits=[]
    def native(method,path,body=None,**kwargs):
        if path.endswith('/message'):
            size=kwargs['params']['limit'];limits.append(size)
            if size>2:raise HTTPException(502,'执行记录过大，请缩小查询范围')
            return [{"info":{"role":"user"},"parts":[{"type":"file","filename":"image.png","url":"data:image/png;base64,private-content"}]}]
        if path.endswith('/diff') or path.endswith('/todo') or path.endswith('/children'):return []
        return original(method,path,body,**kwargs)
    monkeypatch.setattr(main,'upstream',native)
    result=client.get('/api/v1/sessions/ses_test/state',headers=token())
    assert result.status_code==200 and result.json()['history_limited']
    assert limits[0]==50 and limits[-1]<=2
    assert 'private-content' not in result.text and result.json()['messages'][0]['parts'][0]['filename']=='image.png'



def test_slash_command_passes_native_file_reference(env, monkeypatch, tmp_path):
    client, token, _, original = env
    monkeypatch.setattr(main,'WORKSPACE',str(tmp_path));(tmp_path/'context.txt').write_text('attachment')
    monkeypatch.setattr(main,'upstream',lambda method,path,body=None,**kwargs:[{"name":"check"}] if path=='/command' else original(method,path,body,**kwargs))
    sent=[]
    monkeypatch.setattr(main,'dispatch',lambda user,action,sid,path,body:sent.append(body) or {'accepted':True})
    response=client.post('/api/v1/sessions/ses_test/command',headers=token(),json={'provider':'deepseek','model':'model-test','command':'check','attachments':[{'path':'context.txt'}]})
    assert response.status_code==200
    assert sent[0]['parts']==[{'type':'file','mime':'text/plain','filename':'context.txt','url':(tmp_path/'context.txt').as_uri()}]


def test_reads_do_not_wait_for_sqlite_writer(env):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    client, token, _, _ = env
    with main.connect() as db:
        path = main.DATA_DIR / "gateway.sqlite"
    writer = sqlite3.connect(path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor() as pool:
            future = pool.submit(lambda: client.get("/api/v1/preferences", headers=token()))
            try:
                assert future.result(timeout=2).status_code == 200
            finally:
                writer.rollback()
    finally:
        writer.close()


def test_plan_command_enforces_session_denials(env, monkeypatch):
    client,token,calls,original=env
    def upstream(method,path,body=None,**kwargs):
        if path=="/command": return [{"name":"check"}]
        if path=="/session/ses_test" and method=="GET": return {"permission":main.access_rules("full")}
        return original(method,path,body,**kwargs)
    monkeypatch.setattr(main,"upstream",upstream)
    monkeypatch.setattr(main,"dispatch",lambda *args: {"accepted":True})
    result=client.post("/api/v1/sessions/ses_test/command",headers=token(),json={"provider":"deepseek","model":"model-test","agent":"plan","command":"check"})
    assert result.status_code==200
    rules=next(body["permission"] for method,path,body in calls if method=="PATCH")
    assert rules==main.access_rules("full","plan")
