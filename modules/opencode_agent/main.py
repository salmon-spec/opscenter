"""Workbench boundary for a dedicated OpenCode runner; never a general proxy."""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from collections import OrderedDict
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from runtime.app import create_module_app
from runtime.auth import SessionVerifier

VERSION = "0.2.0"
RUNNER_URL = os.getenv("OPENCODE_RUNNER_URL", "http://127.0.0.1:4096").rstrip("/")
WORKSPACE = os.getenv("OPENCODE_WORKSPACE", "/workspace")
DATA_DIR = Path(os.getenv("OPENCODE_GATEWAY_DATA", "/data"))
_lock = threading.Lock()
_schema_lock = threading.Lock()
_operations = {}
_long_operation = ContextVar("opencode_long_operation", default=False)
_workspace = ContextVar("opencode_workspace", default="default")
_history_lock = threading.Lock()
_catalog_lock = threading.Lock()
_catalog_cache = {}
_client_lock = threading.Lock()
_client = None
_schema_identity = None
_live_lock = threading.Lock()
_live_parts = OrderedDict()
_live_roles = OrderedDict()
_live_started = False
_live_revision = 0
_live_dirty = OrderedDict()


def directory():
    key = _workspace.get()
    return WORKSPACE if key == "default" else str(Path(WORKSPACE) / "projects" / key)



def admin(request: Request):
    return request.state.agent_user


@contextmanager
def connect():
    global _schema_identity
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(DATA_DIR / "gateway.sqlite", timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with _schema_lock:
            path = DATA_DIR / "gateway.sqlite"
            identity = (str(path), path.stat().st_ino)
            if _schema_identity != identity:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
                db.execute("""CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, actor TEXT, action TEXT, target TEXT,
                    outcome TEXT, created_at TEXT)""")
                if "archived" not in {r[1] for r in db.execute("PRAGMA table_info(sessions)")}:
                    db.execute("ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
                if "workspace" not in {r[1] for r in db.execute("PRAGMA table_info(sessions)")}:
                    db.execute("ALTER TABLE sessions ADD COLUMN workspace TEXT NOT NULL DEFAULT 'default'")
                db.execute("CREATE TABLE IF NOT EXISTS terminals(id TEXT PRIMARY KEY)")
                db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,title TEXT NOT NULL)")
                db.execute("INSERT OR IGNORE INTO workspaces VALUES ('default','默认工作区')")
                db.execute("CREATE TABLE IF NOT EXISTS preferences(workspace TEXT PRIMARY KEY,data TEXT NOT NULL)")
                db.commit()
                _schema_identity = identity
        db.execute("PRAGMA synchronous=FULL")
        with db:
            yield db
    finally:
        db.close()


def audit(user, action, target, outcome):
    with connect() as db:
        db.execute("INSERT INTO audit(actor,action,target,outcome,created_at) VALUES (?,?,?,?,?)",
                   (user["username"], action, target, outcome, datetime.now(timezone.utc).isoformat()))


def upstream(method, path, body=None, *, params=None):
    global _client
    password_path = os.getenv("OPENCODE_RUNNER_PASSWORD_FILE", "")
    try:
        password = Path(password_path).read_text().strip() if password_path else ""
    except OSError:
        raise HTTPException(503, "OpenCode 服务凭据不可用") from None
    if not password:
        raise HTTPException(503, "OpenCode 服务凭据尚未配置")
    try:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(base_url=RUNNER_URL, follow_redirects=False, trust_env=False,
                                       limits=httpx.Limits(max_connections=16, max_keepalive_connections=8))
        with _client.stream(method, path, auth=("opencode", password),
                            timeout=httpx.Timeout(20, read=1200) if _long_operation.get() else 20,
                            params={"directory": directory(), **(params or {})}, json=body) as r:
            if not 200 <= r.status_code < 300:
                code = r.status_code if r.status_code in {400, 404, 409, 422} else 502
                raise HTTPException(code, f"OpenCode 请求失败（HTTP {r.status_code}）")
            chunks, size = [], 0
            # One allowed attachment batch can exceed the ordinary API cap.
            cap = 32 * 1024 * 1024 if method == "GET" and re.fullmatch(r"/session/ses[a-zA-Z0-9_]+/message(?:/msg[a-zA-Z0-9_]+)?", path) else 8 * 1024 * 1024
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > cap:
                    raise HTTPException(502, "执行记录过大，请缩小查询范围")
                chunks.append(chunk)
            data = b"".join(chunks)
        return json.loads(data) if data else None
    except httpx.TimeoutException:
        raise HTTPException(504, "OpenCode 请求超时；操作结果可能已生效，请刷新状态后再操作") from None
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "OpenCode 执行服务暂不可用") from None


def mutate(user, action, target, method, path, body=None):
    # Audit acceptance before side effects, without retaining prompts or credentials.
    audit(user, action, target, "requested")
    try:
        result = upstream(method, path, body)
    except HTTPException as exc:
        audit(user, action, target, f"http_{exc.status_code}")
        raise
    audit(user, action, target, "accepted")
    return result


def session(session_id):
    if not re.fullmatch(r"ses[a-zA-Z0-9_]{1,100}", session_id):
        raise HTTPException(404, "会话不存在")
    with connect() as db:
        found = db.execute("SELECT id FROM sessions WHERE id=? AND workspace=?", (session_id, _workspace.get())).fetchone()
    if not found:
        raise HTTPException(404, "会话不存在")
    return session_id


def catalog(allow_stale=False):
    # Native /provider includes every model; retain only public connected metadata.
    key = directory()
    with _catalog_lock:
        cached = _catalog_cache.get(key)
        if cached and (allow_stale or time.monotonic() - cached[0] < 60):
            return cached[1]
        data = upstream("GET", "/provider")
        connected = set(data.get("connected", []))
        result = {"items": [], "models": []}
        for provider in data.get("all", []):
            pid = provider["id"]
            result["items"].append({"id": pid, "name": provider.get("name", pid), "connected": pid in connected})
            if pid not in connected: continue
            for mid, model in provider.get("models", {}).items():
                result["models"].append({"provider": pid, "id": mid, "name": model.get("name", mid),
                    "variants": list(model.get("variants", {})), "limit": model.get("limit"),
                    "capabilities": model.get("capabilities"), "cost": model.get("cost")})
        if len(_catalog_cache) >= 8: _catalog_cache.pop(next(iter(_catalog_cache)))
        _catalog_cache[key] = (time.monotonic(), result)
        return result


def models():
    # Sending a message must not rediscover every provider after an idle minute.
    return [{k: model[k] for k in ("provider", "id", "name")} for model in catalog(allow_stale=True)["models"]]


def invalidate_catalog():
    with _catalog_lock: _catalog_cache.clear()


def receive_live_event(event):
    global _live_revision
    directory_key = event.get("directory")
    payload = event.get("payload", {})
    properties = payload.get("properties", {})
    kind = payload.get("type")
    with _live_lock:
        if kind in {"session.status", "permission.asked", "permission.replied", "question.asked", "question.replied"}:
            _live_revision += 1
            _live_dirty[(directory_key, properties.get("sessionID"))] = _live_revision
            while len(_live_dirty) > 128: _live_dirty.popitem(last=False)
            return
        if kind == "message.updated":
            info = properties.get("info", {})
            _live_roles[info.get("id")] = info.get("role")
            if info.get("time", {}).get("completed"):
                for key in list(_live_parts):
                    if key[2] == info.get("id"): _live_parts.pop(key)
            while len(_live_roles) > 128: _live_roles.popitem(last=False)
            return
        part = properties.get("part", {}) if kind == "message.part.updated" else properties
        mid = part.get("messageID")
        if _live_roles.get(mid) != "assistant": return
        key = (directory_key, part.get("sessionID"), mid, part.get("id", part.get("partID")))
        if kind == "message.part.updated" and part.get("type") in {"text", "reasoning"}:
            _live_parts[key] = {k: part[k] for k in ("id", "messageID", "sessionID", "type", "text") if k in part}
        elif kind == "message.part.delta" and properties.get("field") == "text" and key in _live_parts:
            _live_parts[key]["text"] = _live_parts[key].get("text", "") + properties.get("delta", "")
        else: return
        _live_revision += 1
        _live_parts.move_to_end(key)
        # Bound in-memory stream data; persisted history remains authoritative.
        while len(_live_parts) > 64 or sum(len(p.get("text", "")) for p in _live_parts.values()) > 512 * 1024:
            _live_parts.popitem(last=False)


def start_live_updates():
    global _live_started
    with _live_lock:
        if _live_started: return
        _live_started = True
    def listen():
        while True:
            try:
                password = Path(os.environ["OPENCODE_RUNNER_PASSWORD_FILE"]).read_text().strip()
                with httpx.Client(base_url=RUNNER_URL, auth=("opencode", password), trust_env=False,
                                  timeout=httpx.Timeout(10, read=45)) as client:
                    with client.stream("GET", "/global/event") as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if line.startswith("data:") and len(line) < 512 * 1024:
                                try: receive_live_event(json.loads(line[5:]))
                                except (ValueError, TypeError, KeyError): pass
            except (httpx.HTTPError, OSError, KeyError): pass
            with _live_lock: _live_parts.clear();_live_roles.clear()
            time.sleep(1)
    threading.Thread(target=listen, daemon=True, name="opencode-live-text").start()


def merge_live_text(messages, sid):
    with _live_lock:
        parts = [dict(p) for (folder, session_id, _, _), p in _live_parts.items() if folder == directory() and session_id == sid]
    lookup = {m["info"]["id"]: m for m in messages if m.get("info", {}).get("id")}
    for part in parts:
        mid = part["messageID"]
        if mid not in lookup:
            lookup[mid] = {"info": {"id": mid, "role": "assistant"}, "parts": []}
            messages.append(lookup[mid])
        items = lookup[mid].setdefault("parts", [])
        old = next((p for p in items if p.get("id") == part.get("id")), None)
        if old is None: items.append(part)
        elif not old.get("time", {}).get("end") and len(part.get("text", "")) > len(old.get("text", "")):
            old["text"] = part["text"]
    return messages


def summary():
    try:
        health = upstream("GET", "/global/health")
        ready = bool(health.get("healthy"))
    except HTTPException:
        ready, health = False, {}
    return {"status": "ok" if ready else "unavailable", "collected_at": datetime.now(timezone.utc),
            "data": {"runner_ready": ready, "runner_version": health.get("version")}}


def runner_ready():
    if not upstream("GET", "/global/health").get("healthy"):
        raise RuntimeError("runner unavailable")


app = create_module_app("opencode-agent", VERSION, summary,
                        lambda: {"status": "ok", "items": []},
                        capabilities=["agent:execute", "agent:approve", "agent:stop"],
                        readiness_provider=runner_ready)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # FastAPI's default validation response includes the submitted secret value.
    return JSONResponse({"detail": "请求字段或格式无效，请检查必填项及长度"}, status_code=422)

# Unlike passive modules, all Agent APIs require admin authorization even if the
# shared optional session middleware is disabled in a development environment.
@app.middleware("http")
async def authorize(request: Request, call_next):
    if request.url.path.startswith("/api/v1/"):
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        try:
            if scheme.lower() != "bearer" or not token:
                raise HTTPException(401, "请先登录运维工作台")
            user = SessionVerifier().verify(token)
            if user["role"] != "admin":
                raise HTTPException(403, "Agent 执行环境需要管理员权限")
            key = request.query_params.get("workspace", "default")
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", key): raise HTTPException(422, "工作区名称无效")
            with connect() as db:
                if not db.execute("SELECT id FROM workspaces WHERE id=?", (key,)).fetchone(): raise HTTPException(404, "工作区不存在")
            request.state.agent_user = user
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        scope = _workspace.set(key)
        try: return await call_next(request)
        finally: _workspace.reset(scope)
    return await call_next(request)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NewSession(Input):
    title: str = Field(min_length=1, max_length=120)
    permission_mode: Literal["ask", "full"] = "ask"


class Attachment(Input):
    path: str = Field(min_length=1, max_length=500)


class Prompt(Input):
    agent: str = Field(default="build", pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    variant: str | None = Field(default=None, max_length=80)
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    text: str = Field(min_length=1, max_length=20000)
    provider: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    model: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9._:/-]+$")


class Credential(Input):
    api_key: str = Field(min_length=8, max_length=1024)


class PermissionReply(Input):
    reply: Literal["once", "always", "reject"]


class QuestionReply(Input):
    answers: list[list[str]] = Field(max_length=20)


@app.get("/ui", response_class=HTMLResponse)
@app.get("/ui/", response_class=HTMLResponse)
def page():
    here = Path(__file__).parent
    html = (here / "index.html").read_text(encoding="utf-8")
    for name in ("xterm.js", "addon-fit.js", "xterm.css", "marked.js", "purify.js"):
        html = html.replace("/* VENDOR:" + name + " */", (here / "vendor" / name).read_text(encoding="utf-8").replace("</script", "<\\/script"))
    return HTMLResponse(html, headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; form-action 'none'; frame-ancestors 'self'",
    })


@app.get("/api/v1/status")
def status(user=Depends(admin)):
    return {**summary()["data"], "models": models(), "workspace": directory(), "preferences": preferences(user)}


class Preferences(Input):
    provider: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    model: str = Field(min_length=1, max_length=160)
    agent: str = Field(default="build", pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    variant: str | None = Field(default=None, max_length=80)
    permission_mode: Literal["ask", "full"] = "ask"


@app.get("/api/v1/preferences")
def preferences(user=Depends(admin)):
    with connect() as db:
        row = db.execute("SELECT data FROM preferences WHERE workspace=?", (_workspace.get(),)).fetchone()
    return json.loads(row[0]) if row else {}


@app.post("/api/v1/preferences")
def save_preferences(body: Preferences, user=Depends(admin)):
    if not any(m["provider"] == body.provider and m["id"] == body.model for m in models()):
        raise HTTPException(422, "模型未连接")
    if preferences(user) == body.model_dump(): return {"saved": True}
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO preferences VALUES (?,?)", (_workspace.get(), json.dumps(body.model_dump())))
    return {"saved": True}


def access_rules(mode, agent=None):
    rules = [{"permission": "*", "pattern": "*", "action": "allow" if mode == "full" else "ask"}]
    if mode == "ask":
        rules += [{"permission": tool, "pattern": "*", "action": "allow"} for tool in ("read", "glob", "grep", "list")]
    if agent == "plan":
        rules += [{"permission": tool, "pattern": "*", "action": "deny"} for tool in ("edit", "bash", "shell", "task")]
    return rules


def access_mode(info):
    rules = info.get("permission", []) if isinstance(info, dict) else []
    wildcard = next((r for r in reversed(rules) if r.get("permission") == "*" and r.get("pattern") == "*"), {})
    return "full" if wildcard.get("action") == "allow" else "ask"


class Access(Input):
    mode: Literal["ask", "full"]


@app.post("/api/v1/sessions/{session_id}/access")
def set_access(session_id: str, body: Access, user=Depends(admin)):
    sid = session(session_id)
    with _lock:
        idle()
        mutate(user, "access." + body.mode, sid, "PATCH", f"/session/{sid}", {"permission": access_rules(body.mode)})
        # Native 'always' approvals are instance-wide; dispose revokes old grants.
        if body.mode == "ask": upstream("POST", "/instance/dispose")
    return {"mode": body.mode}


@app.post("/api/v1/config/{provider}")
def configure(provider: str, body: Credential, user=Depends(admin)):
    validate_provider(provider)
    with _lock:
        idle()
        mutate(user, "credential.update", provider, "PUT", f"/auth/{provider}", {"type": "api", "key": body.api_key})
        upstream("POST", "/instance/dispose")
        invalidate_catalog()
    return {"configured": True, "message": "Key 已保存；请选择模型并运行验证任务确认连接"}


@app.get("/api/v1/sessions")
def sessions(archived: bool = False, user=Depends(admin)):
    with connect() as db:
        rows = db.execute("SELECT id,title,archived FROM sessions WHERE archived=? AND workspace=? ORDER BY rowid DESC LIMIT 500", (int(archived), _workspace.get())).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/v1/sessions")
def create(body: NewSession, user=Depends(admin)):
    data = mutate(user, "session.create", "new", "POST", "/session", {"title": body.title, "permission": access_rules(body.permission_mode)})
    with connect() as db:
        db.execute("INSERT INTO sessions(id,title,workspace) VALUES (?,?,?)", (data["id"], body.title, _workspace.get()))
    return {"id": data["id"], "title": body.title, "permission_mode": body.permission_mode}


@app.get("/api/v1/sessions/{session_id}/state")
def state(session_id: str, limit: int = Query(default=50, ge=1, le=1000), details: bool = True, user=Depends(admin)):
    sid = session(session_id)
    start_live_updates()
    # Decode and discard inline file bodies serially to bound gateway memory.
    with _history_lock:
        effective = limit
        while True:
            try:
                messages = upstream("GET", f"/session/{sid}/message", params={"limit": effective})
                break
            except HTTPException as exc:
                if exc.detail != "执行记录过大，请缩小查询范围" or effective == 1: raise
                effective = max(1, effective // 2)
        for message in messages:
            for part in message.get("parts", []):
                if part.get("type") == "file" and str(part.get("url", "")).startswith("data:"):
                    part.pop("url", None)
    messages = merge_live_text(messages, sid)
    for message in messages:
        error = message.get("info", {}).get("error")
        if error:
            data = error.get("data", {})
            message["info"]["error"] = {"name": error.get("name"), "data": {
                "message": data.get("message", "模型执行失败"), "statusCode": data.get("statusCode")}}
    info = upstream("GET", f"/session/{sid}")
    return {"session": info, "permission_mode": access_mode(info),
            "status": upstream("GET", "/session/status").get(sid, {"type": "idle"}),
            "messages": messages,
            "permissions": [p for p in upstream("GET", "/permission") if p.get("sessionID") == sid],
            "questions": [q for q in upstream("GET", "/question") if q.get("sessionID") == sid],
            **({"diff": upstream("GET", f"/session/{sid}/diff"),
                "todo": upstream("GET", f"/session/{sid}/todo"),
                "children": upstream("GET", f"/session/{sid}/children")} if details else {}),
            "operation": _operations.get(sid), "history_limited": effective < limit}


def enforce_access(user, sid, agent):
    agents = upstream("GET", "/agent")
    if not any(a["name"] == agent and a.get("mode") != "subagent" and not a.get("hidden") for a in agents):
        raise HTTPException(422, "所选 Agent 不可用")
    info = upstream("GET", f"/session/{sid}")
    rules = access_rules(access_mode(info), agent)
    if isinstance(info, dict) and info.get("permission", [])[-len(rules):] != rules:
        mutate(user, "access.enforce", sid, "PATCH", f"/session/{sid}", {"permission": rules})


@app.post("/api/v1/sessions/{session_id}/prompt")
def prompt(session_id: str, body: Prompt, user=Depends(admin)):
    sid = session(session_id)
    start_live_updates()
    with _lock:
        # shortcut: sessions share one workspace; use separate runners for parallel work.
        idle()
        if not any(m["provider"] == body.provider and m["id"] == body.model for m in models()):
            raise HTTPException(422, "所选模型未连接，请先配置对应 API Key")
        enforce_access(user, sid, body.agent)
        parts = [{"type": "text", "text": body.text}]
        parts.extend(file_parts(body.attachments))
        mutate(user, "prompt.submit", sid, "POST", f"/session/{sid}/prompt_async",
               {"agent": body.agent, "model": {"providerID": body.provider, "modelID": body.model},
                **({"variant": body.variant} if body.variant else {}), "parts": parts})
    return {"accepted": True}


@app.post("/api/v1/sessions/{session_id}/stop")
def stop(session_id: str, user=Depends(admin)):
    sid = session(session_id)
    mutate(user, "session.stop", sid, "POST", f"/session/{sid}/abort")
    return {"stopped": True}


def pending(kind, sid, request_id):
    if not re.fullmatch(r"(?:per|que)[a-zA-Z0-9_]{1,100}", request_id):
        raise HTTPException(404, "请求不存在")
    items = upstream("GET", f"/{kind}")
    item = next((p for p in items if p.get("id") == request_id and p.get("sessionID") == sid), None)
    if not item:
        raise HTTPException(404, "请求已处理或不属于此会话")
    return item


@app.post("/api/v1/sessions/{session_id}/permissions/{request_id}")
def permission(session_id: str, request_id: str, body: PermissionReply, user=Depends(admin)):
    sid = session(session_id)
    pending("permission", sid, request_id)
    if body.reply == "always": raise HTTPException(422, "请使用任务权限设置选择完全控制；不再创建跨任务的临时批准")
    mutate(user, f"permission.{body.reply}", sid, "POST", f"/permission/{request_id}/reply", body.model_dump())
    return {"accepted": True}


@app.post("/api/v1/sessions/{session_id}/questions/{request_id}")
def question(session_id: str, request_id: str, body: QuestionReply, user=Depends(admin)):
    sid = session(session_id)
    item = pending("question", sid, request_id)
    if len(body.answers) != len(item.get("questions", [])) or any(
        len(a) > 20 or any(len(s) > 2000 for s in a) for a in body.answers
    ):
        raise HTTPException(422, "回答数量或长度无效")
    mutate(user, "question.reply", sid, "POST", f"/question/{request_id}/reply", body.model_dump())
    return {"accepted": True}


@app.get("/api/v1/audit")
def history(user=Depends(admin)):
    with connect() as db:
        rows = db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100").fetchall()
    return {"items": [dict(r) for r in rows]}


# All operations below keep the runner directory fixed; no raw upstream proxy.
def idle():
    if any(o["status"] == "running" for o in _operations.values()):
        raise HTTPException(409, "请等待当前操作完成，或停止任务")
    if any(s.get("type") != "idle" for s in upstream("GET", "/session/status").values()):
        raise HTTPException(409, "请先停止执行中的任务")


def workspace_file(value):
    root = Path(directory()).resolve()
    if "\\" in value or "\0" in value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise HTTPException(422, "仅允许工作区内的相对路径")
    target = (root / value).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(422, "文件路径超出工作区")
    return target


class SessionEdit(Input):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


class MessageAction(Input):
    message_id: str | None = Field(default=None, pattern=r"^msg[a-zA-Z0-9_]{1,100}$")


class ModelChoice(Input):
    provider: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    model: str = Field(min_length=1, max_length=160)


class Command(ModelChoice):
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    command: str = Field(min_length=1, max_length=80)
    arguments: str = Field(default="", max_length=20000)
    agent: str = Field(default="build", pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class FileWrite(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=3 * 1024 * 1024)
    encoding: Literal["text", "base64"] = "text"
    overwrite: bool = False


@app.get("/api/v1/capabilities")
def capabilities(user=Depends(admin)):
    providers = catalog()
    return {"agents": upstream("GET", "/agent"), "commands": upstream("GET", "/command"),
            "skills": upstream("GET", "/skill"), "mcp": upstream("GET", "/mcp"),
            "lsp": upstream("GET", "/lsp"), "formatters": upstream("GET", "/formatter"),
            "model_details": providers["models"]}


@app.post("/api/v1/config/{provider}/remove")
def remove_credential(provider: str, user=Depends(admin)):
    validate_provider(provider)
    with _lock:
        idle()
        mutate(user, "credential.remove", provider, "DELETE", f"/auth/{provider}")
        upstream("POST", "/instance/dispose")
        invalidate_catalog()
    return {"removed": True}


@app.post("/api/v1/sessions/{session_id}/edit")
def edit_session(session_id: str, body: SessionEdit, user=Depends(admin)):
    sid = session(session_id)
    with _lock:
        idle()
        update = {}
        if body.title is not None: update["title"] = body.title
        if body.archived is not None: update["time"] = {"archived": datetime.now(timezone.utc).timestamp()*1000 if body.archived else 0}
        mutate(user, "session.edit", sid, "PATCH", f"/session/{sid}", update)
        with connect() as db:
            if body.title is not None: db.execute("UPDATE sessions SET title=? WHERE id=?", (body.title, sid))
            if body.archived is not None: db.execute("UPDATE sessions SET archived=? WHERE id=?", (int(body.archived), sid))
    return {"updated": True}


@app.post("/api/v1/sessions/{session_id}/delete")
def delete_session(session_id: str, user=Depends(admin)):
    sid = session(session_id)
    with _lock:
        idle()
        mutate(user, "session.delete", sid, "DELETE", f"/session/{sid}")
        with connect() as db: db.execute("DELETE FROM sessions WHERE id=?", (sid,))
    return {"deleted": True}


@app.post("/api/v1/sessions/{session_id}/fork")
def fork_session(session_id: str, body: MessageAction, user=Depends(admin)):
    sid = session(session_id)
    with _lock:
        idle()
        if body.message_id: require_message(sid, body.message_id)
        data = mutate(user, "session.fork", sid, "POST", f"/session/{sid}/fork",
                      {"messageID": body.message_id} if body.message_id else {})
        with connect() as db:
            db.execute("INSERT INTO sessions(id,title,workspace) VALUES (?,?,?)", (data["id"], data["title"], _workspace.get()))
    return {"id": data["id"], "title": data["title"]}


@app.get("/api/v1/sessions/{session_id}/live")
def live_text(session_id: str, cursor: int = -1, user=Depends(admin)):
    sid = session(session_id)
    start_live_updates()
    with _live_lock:
        revision = _live_revision
        refresh = _live_dirty.get((directory(), sid), -1) > cursor
        parts = [dict(p) for (folder, session_id, _, _), p in _live_parts.items()
                 if folder == directory() and session_id == sid] if cursor != revision else []
    return {"cursor": revision, "parts": parts, "refresh": refresh}


def require_message(sid, message_id):
    return upstream("GET", f"/session/{sid}/message/{message_id}")


@app.post("/api/v1/sessions/{session_id}/revert")
def revert(session_id: str, body: MessageAction, user=Depends(admin)):
    sid = session(session_id)
    if not body.message_id: raise HTTPException(422, "请选择消息")
    with _lock:
        idle()
        require_message(sid, body.message_id)
        return mutate(user, "session.revert", sid, "POST", f"/session/{sid}/revert", {"messageID": body.message_id})


@app.post("/api/v1/sessions/{session_id}/unrevert")
def unrevert(session_id: str, user=Depends(admin)):
    sid = session(session_id)
    with _lock:
        idle()
        return mutate(user, "session.unrevert", sid, "POST", f"/session/{sid}/unrevert")


@app.post("/api/v1/sessions/{session_id}/compact")
def compact(session_id: str, body: ModelChoice, user=Depends(admin)):
    sid = session(session_id)
    with _lock:
        idle()
        check_model(body)
        return dispatch(user, "session.compact", sid, f"/session/{sid}/summarize",
                      {"providerID": body.provider, "modelID": body.model, "auto": False})


def check_model(body):
    if not any(m["provider"] == body.provider and m["id"] == body.model for m in models()):
        raise HTTPException(422, "所选模型未连接")


@app.post("/api/v1/sessions/{session_id}/command")
def command(session_id: str, body: Command, user=Depends(admin)):
    sid = session(session_id)
    with _lock:
        idle()
        check_model(body)
        if not any(c["name"] == body.command for c in upstream("GET", "/command")):
            raise HTTPException(422, "命令不存在")
        enforce_access(user, sid, body.agent)
        return dispatch(user, "command.run", sid, f"/session/{sid}/command",
                      {"command": body.command, "arguments": body.arguments,
                       "model": body.provider + "/" + body.model, "agent": body.agent, "parts": file_parts(body.attachments)})


@app.post("/api/v1/sessions/{session_id}/questions/{request_id}/reject")
def reject_question(session_id: str, request_id: str, user=Depends(admin)):
    sid = session(session_id)
    pending("question", sid, request_id)
    return mutate(user, "question.reject", sid, "POST", f"/question/{request_id}/reject")


@app.get("/api/v1/files")
def files(path: str = "", user=Depends(admin)):
    target = workspace_file(path)
    if not target.is_dir(): raise HTTPException(404, "目录不存在")
    return {"items": [{"name": p.name, "path": str(p.relative_to(Path(directory()).resolve())).replace("\\", "/"),
                       "type": "directory" if p.is_dir() else "file", "size": p.stat().st_size}
                      for p in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                      if not p.is_symlink()][:1000]}


@app.get("/api/v1/files/content")
def file_content(path: str, user=Depends(admin)):
    target = workspace_file(path)
    if not target.is_file(): raise HTTPException(404, "文件不存在")
    if target.stat().st_size > 2 * 1024 * 1024: raise HTTPException(413, "预览限 2 MiB")
    try: content = target.read_text(encoding="utf-8")
    except UnicodeError: raise HTTPException(422, "二进制文件可作为附件使用") from None
    return {"path": path, "content": content}


@app.post("/api/v1/files/write")
def file_write(body: FileWrite, user=Depends(admin)):
    with _lock:
        idle()
        target = workspace_file(body.path)
        if target.exists() and not body.overwrite: raise HTTPException(409, "文件已存在；勾选覆盖后再保存")
        try: content = base64.b64decode(body.content, validate=True) if body.encoding == "base64" else body.content.encode()
        except binascii.Error: raise HTTPException(422, "附件编码无效") from None
        if len(content) > 2 * 1024 * 1024: raise HTTPException(413, "文件限 2 MiB")
        audit(user, "file.write", body.path, "requested")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o770)
        # Exclusive create avoids accidental replacement unless overwrite was explicit.
        try:
            if body.overwrite:
                import tempfile
                fd, name = tempfile.mkstemp(dir=target.parent, prefix=".agent-")
                try:
                    with os.fdopen(fd, "wb") as file: file.write(content)
                    os.chmod(name, 0o660)
                    os.replace(name, target)
                finally: Path(name).unlink(missing_ok=True)
            else:
                with target.open("xb") as file: file.write(content)
                target.chmod(0o660)
        except FileExistsError: raise HTTPException(409, "文件已存在") from None
        audit(user, "file.write", body.path, "accepted")
        if body.path.startswith(".opencode/") or target.name in {"AGENTS.md", "opencode.json", "opencode.jsonc"}:
            upstream("POST", "/instance/dispose")
    return {"path": body.path, "size": len(content)}


@app.get("/api/v1/files/find")
def find_files(query: str = Query(min_length=1, max_length=300), content: bool = False, user=Depends(admin)):
    # Native search stays rooted in the configured runner workspace.
    return upstream("GET", "/find" if content else "/find/file",
                    params={"pattern": re.escape(query)} if content else {"query": query, "limit": 100})


@app.get("/api/v1/vcs")
def vcs(user=Depends(admin)):
    return {"project": upstream("GET", "/project/current"), "info": upstream("GET", "/vcs"), "status": upstream("GET", "/vcs/status"),
            "files": upstream("GET", "/file/status")}


@app.get("/api/v1/settings")
def settings(user=Depends(admin)):
    with connect() as db:
        row = db.execute("SELECT data FROM settings WHERE id=1").fetchone()
    # Return only our editable non-secret fields, never native config/provider keys.
    data = json.loads(row[0]) if row else {"agents": {}, "commands": {}, "mcp": {}}
    return {"plugins": [], **data}


class Extensions(Input):
    plugins: list[str] = Field(default_factory=list, max_length=20)
    agents: dict = Field(default_factory=dict)
    commands: dict = Field(default_factory=dict)
    mcp: dict = Field(default_factory=dict)


@app.post("/api/v1/settings")
def save_settings(body: Extensions, user=Depends(admin)):
    # Strict allowlists prevent extension settings from overriding authentication,
    # workspace routing, provider credentials, or the runner process environment.
    for plugin in body.plugins:
        if not re.fullmatch(r"(?:@[a-z0-9_-]+/)?[a-z0-9_.-]+(?:@[a-zA-Z0-9_.^~+-]+)?", plugin):
            raise HTTPException(422, "插件需要 npm 包名及可选版本号")
    for name, data in body.agents.items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name) or not isinstance(data, dict) or set(data) - {"description", "prompt", "mode", "disable", "steps"}:
            raise HTTPException(422, "Agent 配置字段无效")
        if data.get("mode", "primary") not in {"primary", "subagent", "all"}: raise HTTPException(422, "Agent 模式无效")
    for name, data in body.commands.items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name) or not isinstance(data, dict) or set(data) - {"description", "template", "agent", "subtask"} or not isinstance(data.get("template"), str):
            raise HTTPException(422, "命令配置字段无效")
    for name, data in body.mcp.items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name) or not isinstance(data, dict): raise HTTPException(422, "MCP 名称无效")
        allowed = {"type", "command", "enabled", "timeout"} if data.get("type") == "local" else {"type", "url", "enabled", "timeout", "oauth"}
        if set(data) - allowed: raise HTTPException(422, "MCP 仅支持无密钥配置；凭据请通过工作区专用文件提供")
        if data.get("type") == "remote":
            from urllib.parse import urlsplit
            try: url = urlsplit(data.get("url", ""))
            except ValueError: raise HTTPException(422, "MCP 地址无效") from None
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise HTTPException(422, "远程 MCP 必须使用不含凭据的 HTTPS 地址")
            if data.get("oauth", False) is not False: raise HTTPException(422, "当前模块不提供 OAuth 回调")
        elif data.get("type") == "local":
            if not isinstance(data.get("command"), list) or not data["command"] or not all(isinstance(c, str) for c in data["command"]):
                raise HTTPException(422, "本地 MCP 需要命令数组")
        else: raise HTTPException(422, "MCP 类型无效")
    if len(json.dumps(body.model_dump())) > 100000: raise HTTPException(413, "扩展配置过大")
    with _lock:
        idle()
        old = settings(user)
        config_path = os.getenv("OPENCODE_NATIVE_CONFIG_FILE")
        if not config_path: raise HTTPException(503, "原生配置目录尚未挂载")
        # Validate with OpenCode, then replace only the maps managed by this UI.
        # Native PATCH merges maps and otherwise leaves removed fields behind.
        mutate(user, "settings.update", "extensions", "PATCH", "/global/config",
               {"agent": body.agents, "command": body.commands, "mcp": body.mcp, "plugin": body.plugins})
        data = upstream("GET", "/global/config")
        for field, values in (("agents", body.agents), ("commands", body.commands), ("mcp", body.mcp)):
            key = {"agents": "agent", "commands": "command", "mcp": "mcp"}[field]
            original = data.get(key, {})
            updated = {name: value for name, value in original.items() if name not in old[field]}
            for name, value in values.items():
                secrets = {k: v for k, v in original.get(name, {}).items() if k in {"headers", "environment"}} if key == "mcp" else {}
                updated[name] = {**secrets, **value}
            data[key] = updated
        plugins = {(p if isinstance(p, str) else p[0]): p for p in data.get("plugin", []) if (p if isinstance(p, str) else p[0]) not in old.get("plugins", [])}
        plugins.update({p: p for p in body.plugins})
        data["plugin"] = list(plugins.values())
        import tempfile
        target = Path(config_path)
        schema = data.pop("$schema", "https://opencode.ai/config.json")
        fd, name = tempfile.mkstemp(dir=target.parent, prefix=".opencode-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file: json.dump(data, file, ensure_ascii=False, indent=2)
            os.chmod(name, 0o660)
            os.replace(name, target)
        finally: Path(name).unlink(missing_ok=True)
        # A real native patch invalidates its process-wide global config cache.
        upstream("PATCH", "/global/config", {"$schema": schema})
        upstream("POST", "/global/dispose")
        invalidate_catalog()
        with connect() as db: db.execute("INSERT OR REPLACE INTO settings VALUES (1,?)", (json.dumps(body.model_dump()),))
    return {"saved": True}


@app.post("/api/v1/mcp/{name}/{action}")
def mcp_connection(name: str, action: Literal["connect", "disconnect"], user=Depends(admin)):
    if name not in settings(user)["mcp"]: raise HTTPException(404, "MCP 不存在")
    with _lock:
        idle()
        return mutate(user, "mcp." + action, name, "POST", f"/mcp/{name}/{action}")


class TerminalInput(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    data: str = Field(min_length=1, max_length=10000)


class TerminalSize(Input):
    rows: int = Field(ge=5, le=200)
    cols: int = Field(ge=20, le=500)


def terminal_module():
    from opencode_agent import terminal
    return terminal


def terminal_id(value):
    if not re.fullmatch(r"pty[a-zA-Z0-9_]{1,100}", value): raise HTTPException(404, "终端不存在")
    with connect() as db:
        found = db.execute("SELECT id FROM terminals WHERE id=?", (value,)).fetchone()
    if not found: raise HTTPException(404, "终端不存在")
    if not any(p["id"] == value and p.get("cwd") == directory() for p in upstream("GET", "/pty")):
        raise HTTPException(404, "终端不属于当前工作区")
    return value


@app.get("/api/v1/terminals")
def terminals(user=Depends(admin)):
    with connect() as db:
        ids = {r[0] for r in db.execute("SELECT id FROM terminals")}
    return {"items": [p for p in upstream("GET", "/pty") if p["id"] in ids and p.get("cwd") == directory()]}


class TerminalCreate(Input):
    kind: Literal["shell", "opencode"] = "shell"
    session_id: str | None = None


@app.post("/api/v1/terminals")
def create_terminal(body: TerminalCreate | None = None, user=Depends(admin)):
    with _lock:
        if len(upstream("GET", "/pty")) >= 4: raise HTTPException(409, "执行环境最多创建四个终端，请关闭其他工作区不用的终端")
        native = body is not None and body.kind == "opencode"
        args = ["attach", RUNNER_URL, "--dir", directory()] if native else ["--noprofile", "--norc"]
        if native and body.session_id: args += ["--session", session(body.session_id)]
        value = mutate(user, "terminal.create", "opencode" if native else "workspace", "POST", "/pty",
                       {"command": os.getenv("OPENCODE_NATIVE_COMMAND", "opencode-runner") if native else os.getenv("OPENCODE_TERMINAL_COMMAND", "bash"),
                        "args": args, "cwd": directory(), "title": "原生 OpenCode" if native else "工作区终端"})
        with connect() as db:
            db.execute("INSERT INTO terminals(id) VALUES (?)", (value["id"],))
    return value


def terminal_stream(pty_id):
    return terminal_module().stream(terminal_id(pty_id), RUNNER_URL, directory(),
                                    os.getenv("OPENCODE_RUNNER_PASSWORD_FILE", ""))


@app.get("/api/v1/terminals/{pty_id}/output")
def terminal_output(pty_id: str, cursor: int = Query(default=0, ge=0), user=Depends(admin)):
    return terminal_stream(pty_id).poll(cursor)


@app.post("/api/v1/terminals/{pty_id}/input")
def terminal_input(pty_id: str, body: TerminalInput, user=Depends(admin)):
    audit(user, "terminal.input", terminal_id(pty_id), "requested")
    terminal_stream(pty_id).send(body.data)
    return {"accepted": True}


@app.post("/api/v1/terminals/{pty_id}/resize")
def resize_terminal(pty_id: str, body: TerminalSize, user=Depends(admin)):
    return mutate(user, "terminal.resize", terminal_id(pty_id), "PUT", f"/pty/{pty_id}", {"size": body.model_dump()})


@app.post("/api/v1/terminals/{pty_id}/close")
def close_terminal(pty_id: str, user=Depends(admin)):
    tid = terminal_id(pty_id)
    terminal_module().close(tid)
    result = mutate(user, "terminal.close", tid, "DELETE", f"/pty/{tid}")
    with connect() as db: db.execute("DELETE FROM terminals WHERE id=?", (tid,))
    return result


@app.post("/api/v1/sessions/{session_id}/children/{child_id}/open")
def open_child(session_id: str, child_id: str, user=Depends(admin)):
    sid = session(session_id)
    children = upstream("GET", f"/session/{sid}/children")
    child = next((c for c in children if c["id"] == child_id), None)
    if not child: raise HTTPException(404, "子任务不存在")
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO sessions(id,title,workspace) VALUES (?,?,?)", (child["id"], child["title"], _workspace.get()))
    return {"id": child["id"], "title": child["title"]}


@app.get("/api/v1/workspaces")
def workspaces(user=Depends(admin)):
    with connect() as db:
        return {"items": [dict(r) for r in db.execute("SELECT id,title FROM workspaces ORDER BY rowid")]}


class NewWorkspace(Input):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,40}$")
    title: str = Field(min_length=1, max_length=120)


@app.post("/api/v1/workspaces")
def create_workspace(body: NewWorkspace, user=Depends(admin)):
    with _lock:
        idle()
        with connect() as db:
            if db.execute("SELECT id FROM workspaces WHERE id=?", (body.name,)).fetchone(): raise HTTPException(409, "工作区已存在")
            root = Path(WORKSPACE).resolve()
            target = (root / "projects" / body.name).resolve()
            if not target.is_relative_to(root): raise HTTPException(422, "工作区路径无效")
            target.mkdir(parents=True, exist_ok=True, mode=0o770)
            target.chmod(0o770)
            db.execute("INSERT INTO workspaces VALUES (?,?)", (body.name, body.title))
        audit(user, "workspace.create", body.name, "accepted")
    return {"id": body.name, "title": body.title}


@app.get("/api/v1/providers")
def provider_catalog(user=Depends(admin)):
    return {"items": catalog()["items"]}


def validate_provider(provider):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", provider) or not any(p["id"] == provider for p in catalog()["items"]):
        raise HTTPException(422, "模型提供方不存在")



def dispatch(user, action, sid, path, body):
    # Native commands and compaction can wait for a full model turn.
    audit(user, action, sid, "queued")
    _operations[sid] = {"status": "running", "action": action}
    def execute():
        scope = _long_operation.set(True)
        try:
            mutate(user, action, sid, "POST", path, body)
            _operations[sid] = {"status": "completed", "action": action}
        except HTTPException as exc:
            _operations[sid] = {"status": "error", "action": action, "error": exc.detail}
        except Exception:
            _operations[sid] = {"status": "error", "action": action, "error": "操作失败，请刷新任务状态"}
        finally:
            _long_operation.reset(scope)
    threading.Thread(target=copy_context().run, args=(execute,), daemon=True).start()
    return {"accepted": True}


@app.post("/api/v1/sessions/import")
def import_native_sessions(user=Depends(admin)):
    items = upstream("GET", "/session", params={"scope": "project", "limit": 500})
    count = 0
    with connect() as db:
        for item in items:
            if os.path.normcase(os.path.normpath(item.get("directory", ""))) != os.path.normcase(os.path.normpath(directory())): continue
            if not re.fullmatch(r"ses[a-zA-Z0-9_]{1,100}", item.get("id", "")): continue
            db.execute("INSERT INTO sessions(id,title,archived,workspace) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,archived=excluded.archived,workspace=excluded.workspace",
                       (item["id"], item["title"], int(bool(item.get("time", {}).get("archived"))), _workspace.get()))
            count += 1
    return {"imported": count}


@app.post("/api/v1/vcs/init")
def init_git(user=Depends(admin)):
    with _lock:
        idle()
        target = Path(directory()).resolve()
        if not target.is_relative_to(Path(WORKSPACE).resolve()): raise HTTPException(422, "工作区路径无效")
        # Gateway-owned shared directories need an exact Git trust entry for runner UID.
        setup = mutate(user, "vcs.trust", _workspace.get(), "POST", "/pty", {
            "command": "git", "args": ["config", "--global", "--add", "safe.directory", str(target)],
            "cwd": str(target), "title": "Git workspace setup"})
        try:
            for _ in range(40):
                try: info = upstream("GET", f"/pty/{setup['id']}")
                except HTTPException as exc:
                    if exc.status_code == 404: break
                    raise
                if info.get("status") == "exited": break
                time.sleep(0.05)
            else: raise HTTPException(504, "Git 工作区配置超时，请刷新后重试")
        finally:
            try: upstream("DELETE", f"/pty/{setup['id']}")
            except HTTPException as exc:
                if exc.status_code != 404: raise
        mutate(user, "vcs.init", _workspace.get(), "POST", "/project/git/init")
        upstream("POST", "/instance/dispose")
        value = upstream("GET", "/project/current")
        if value.get("vcs") != "git": raise HTTPException(502, "Git 仓库尚未就绪，请检查执行器的工作区权限")
        return value



def file_parts(attachments):
    parts = []
    for attachment in attachments:
        file = workspace_file(attachment.path)
        if not file.is_file() or file.stat().st_size > 2 * 1024 * 1024:
            raise HTTPException(422, "附件不存在或超过 2 MiB")
        # Delegate reading/truncation and image preparation to native @file handling.
        parts.append({"type": "file", "filename": file.name, "mime": "text/plain", "url": file.as_uri()})
    return parts
