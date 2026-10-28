"""Independent automation module. Semaphore owns execution, credentials and schedules."""
import hmac
import os
import secrets
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import Semaphore
from .store import Store, now, actor
from .scripts import Scripts

VERSION = "0.3.1"
HERE = Path(__file__).parent


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Template(Input):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    playbook: str = Field(min_length=1, max_length=250)
    inventory_id: int = Field(gt=0)
    repository_id: int = Field(gt=0)
    environment_id: int = Field(gt=0)
    app: Literal["ansible"] = "ansible"

    @field_validator("playbook")
    @classmethod
    def repository_path(cls, value):
        if value.startswith(("/", "-")) or "\\" in value or ":" in value or ".." in PurePosixPath(value).parts or any(ord(c) < 32 for c in value):
            raise ValueError("使用仓库内的相对 playbook 路径")
        return value


class Inventory(Input):
    name: str = Field(min_length=1, max_length=100)
    inventory: str = Field(min_length=1, max_length=32000)
    ssh_key_id: int = Field(gt=0)
    type: Literal["static", "static-yaml"] = "static"


class Schedule(Input):
    name: str = Field(min_length=1, max_length=100)
    template_id: int = Field(gt=0)
    cron_format: str = Field(min_length=1, max_length=120)
    active: bool = False
    type: Literal[""] = ""

    @field_validator("cron_format")
    @classmethod
    def five_fields(cls, value):
        if len(value.split()) != 5:
            raise ValueError("请输入五段 Cron 表达式；真实模式由 Semaphore 进一步校验")
        return value


class Run(Input):
    template_id: int = Field(gt=0)
    dry_run: bool = True
    message: str = Field(default="", max_length=300)


class Active(Input):
    active: bool


class ScriptEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=250)
    content: str = Field(min_length=1, max_length=128000)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def create_app(data_dir=None, mode=None, engine=None):
    mode = mode or os.getenv("AUTOMATION_MODE", "demo")
    if mode not in {"demo", "semaphore"}:
        raise ValueError("AUTOMATION_MODE must be demo or semaphore")
    store = Store(data_dir or os.getenv("AUTOMATION_DATA_DIR", str(HERE / ".local")))
    store.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_path = Path(os.getenv("AUTOMATION_TOKEN_FILE", str(store.directory / "admin-token")))
    if not token_path.exists():
        token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with token_path.open("x", encoding="utf-8") as token_file:
            token_file.write(secrets.token_urlsafe(32))
        token_path.chmod(0o600)
    if mode == "demo":
        store.seed()
    else:
        engine = engine or Semaphore(os.getenv("SEMAPHORE_URL", "http://127.0.0.1:3000"),
                                    int(os.getenv("SEMAPHORE_PROJECT_ID", "1")), os.getenv("SEMAPHORE_TOKEN_FILE", ""))

    app = FastAPI(title="OpsCenter 自动化作业", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.mode, app.state.engine = store, mode, engine
    workbench = bool(os.getenv("OPS_JWT_PUBLIC_KEY_FILE"))
    if workbench:
        from runtime.auth import SessionVerifier
        verifier = SessionVerifier()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.getenv(
        "AUTOMATION_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(","))

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
            if workbench:
                try:
                    user = verifier.verify(supplied)
                    if user["role"] != "admin":
                        raise HTTPException(403, "自动化作业需要管理员权限")
                except HTTPException as exc:
                    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
                actor.set(user["username"])
            else:
                try:
                    expected = token_path.read_text(encoding="utf-8").strip()
                except OSError:
                    expected = ""
                if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
                    return JSONResponse({"detail": "请使用本模块访问令牌登录"}, status_code=401)
            if request.method not in {"GET", "HEAD"}:
                origin = request.headers.get("origin")
                if origin and origin != str(request.base_url).rstrip("/"):
                    return JSONResponse({"detail": "请求来源不匹配"}, status_code=403)
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return JSONResponse({"detail": "仅接受 JSON 请求"}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Pydantic's raw input can contain inventory secrets; return field locations only.
        return JSONResponse({"detail": "输入无效，请检查字段类型、长度与格式", "fields": [".".join(map(str, e["loc"])) for e in exc.errors()]}, status_code=422)

    def items(kind):
        return store.list(kind) if mode == "demo" else engine.list(kind)

    def find(kind, item_id):
        row = next((r for r in items(kind) if r["id"] == item_id), None)
        if row is None:
            raise HTTPException(404, "记录不存在或不属于当前项目")
        return row

    def save(kind, value, item_id=None):
        store.audit(f"save_{kind}", item_id or "new", "requested")
        try:
            result = store.save(kind, value, item_id) if mode == "demo" else engine.save(kind, value, item_id)
        except HTTPException as exc:
            store.audit(f"save_{kind}", item_id or "new", f"http_{exc.status_code}")
            raise
        store.audit(f"save_{kind}", result.get("id"), "accepted")
        return result

    @app.get("/")
    @app.get("/ui")
    @app.get("/ui/")
    def ui():
        return HTMLResponse((HERE / "static" / "index.html").read_text(encoding="utf-8"))

    @app.get("/health/live")
    def live():
        return {"status": "ok", "module": "automation", "version": VERSION, "mode": mode}

    @app.get("/health/ready")
    def ready():
        with store.connect() as db:
            db.execute("SELECT 1")
        if mode == "semaphore":
            try:
                engine.list("templates")
            except HTTPException:
                raise HTTPException(503, "Semaphore 执行引擎暂不可用") from None
        return {"status": "ok", "mode": mode}

    @app.get("/api/v1/module")
    def module():
        return {"key": "automation", "version": VERSION, "mode": mode, "capabilities": ["templates", "tasks", "inventories", "schedules"]}

    @app.get("/api/v1/state")
    def state():
        return {"mode": mode, "version": VERSION, "collected_at": now(),
                **{kind: items(kind) for kind in ("templates", "tasks", "inventories", "schedules", "repositories", "environments", "keys")}}

    @app.get("/api/v1/summary")
    def summary():
        try:
            tasks = items("tasks")
            return {"status": "demo" if mode == "demo" else "ok", "collected_at": now(), "data": {
                "mode": mode, "tasks": len(tasks), "failed": sum(t["status"] == "error" for t in tasks),
                "running": sum(t["status"] in {"running", "waiting"} for t in tasks)}}
        except HTTPException:
            return JSONResponse({"status": "unavailable", "collected_at": now(), "data": {}, "error": "Semaphore 暂不可用"}, status_code=503)

    @app.get("/api/v1/findings")
    def findings():
        tasks = items("tasks")
        return {"status": "demo" if mode == "demo" else "ok", "collected_at": now(), "items": [
            {"id": str(t["id"]), "title": f"作业 #{t['id']} 执行失败", "severity": "warning"}
            for t in tasks if t["status"] == "error"]}

    def validate_template(body):
        for kind, key in (("inventories", "inventory_id"), ("repositories", "repository_id"), ("environments", "environment_id")):
            find(kind, getattr(body, key))

    def scripts_for(item_id):
        template = find("templates", item_id)
        root = os.getenv("AUTOMATION_SCRIPTS_DIR", "")
        repository_id = os.getenv("AUTOMATION_SCRIPTS_REPOSITORY_ID", "0")
        if not root or template.get("repository_id") != int(repository_id):
            raise HTTPException(409, "此模板使用外部仓库，请在其源仓库编辑；本地脚本仓库需由管理员配置")
        return Scripts(root, repository_id), template

    @app.get("/api/v1/templates/{item_id}/script")
    def read_script(item_id: int, path: str | None = None):
        scripts, template = scripts_for(item_id)
        return {**scripts.read(path or template["playbook"]), "files": scripts.files()}

    @app.post("/api/v1/templates/{item_id}/script")
    def edit_script(item_id: int, body: ScriptEdit):
        scripts, template = scripts_for(item_id)
        if any(t.get("status") in {"waiting", "starting", "running", "stopping"} for t in items("tasks")):
            raise HTTPException(409, "有作业正在排队或运行，请结束后再保存脚本")
        if len(body.content.encode("utf-8")) > 128000:
            raise HTTPException(413, "脚本超过 128 KB")
        store.audit("save_script", f"{item_id}:{body.path}", "requested")
        result = scripts.save(body.path, body.content, body.revision, actor.get())
        store.audit("save_script", f"{item_id}:{body.path}", result["commit"])
        return result

    @app.post("/api/v1/templates", status_code=201)
    def add_template(body: Template):
        validate_template(body)
        return save("templates", body.model_dump())

    @app.put("/api/v1/templates/{item_id}")
    @app.post("/api/v1/templates/{item_id}")
    def update_template(item_id: int, body: Template):
        original = find("templates", item_id)
        if original.get("app") != "ansible":
            raise HTTPException(409, "首版仅编辑 Ansible 模板，请在 Semaphore 管理其他类型")
        validate_template(body)
        if mode == "semaphore":
            # Fetch the full source so updating exposed fields preserves vaults, surveys and task settings.
            original = engine.request("GET", f"templates/{item_id}")
        return save("templates", {**original, **body.model_dump()}, item_id)

    @app.post("/api/v1/inventories", status_code=201)
    def add_inventory(body: Inventory):
        find("keys", body.ssh_key_id)
        return save("inventories", body.model_dump())

    @app.post("/api/v1/schedules", status_code=201)
    def add_schedule(body: Schedule):
        find("templates", body.template_id)
        return save("schedules", body.model_dump())

    @app.put("/api/v1/schedules/{item_id}/active")
    @app.post("/api/v1/schedules/{item_id}/active")
    def schedule_active(item_id: int, body: Active):
        original = find("schedules", item_id)
        if mode == "semaphore":
            original = engine.request("GET", f"schedules/{item_id}")
        return save("schedules", {**original, "active": body.active}, item_id)

    @app.post("/api/v1/tasks", status_code=201)
    def run(body: Run):
        template = find("templates", body.template_id)
        if template.get("app") != "ansible":
            raise HTTPException(409, "首版只运行 Ansible 模板")
        if mode == "semaphore":
            return save("tasks", body.model_dump())
        result = save("tasks", {**body.model_dump(), "status": "success", "created": now(), "start": now(), "end": now()})
        store.save("output", {"lines": ["[DEMO] " + template["name"], "本地演示：未执行脚本，未连接主机。",
                                       "dry_run=" + str(body.dry_run), "演示记录已持久化。"]}, result["id"])
        return result

    @app.post("/api/v1/tasks/{item_id}/stop")
    def stop(item_id: int, body: Input):
        task = find("tasks", item_id)
        if task["status"] not in {"running", "waiting", "starting"}:
            raise HTTPException(409, "任务已结束，请刷新状态")
        store.audit("stop_task", item_id, "requested")
        try:
            if mode == "semaphore":
                engine.request("POST", f"tasks/{item_id}/stop", {"force": False})
            else:
                store.save("tasks", {**task, "status": "stopped", "end": now()}, item_id)
        except HTTPException as exc:
            store.audit("stop_task", item_id, f"http_{exc.status_code}")
            raise
        store.audit("stop_task", item_id, "accepted")
        return {"accepted": True, "execution_may_continue": True}

    @app.get("/api/v1/tasks/{item_id}/output")
    def output(item_id: int):
        find("tasks", item_id)
        if mode == "demo":
            return {"lines": find("output", item_id)["lines"]}
        data = engine.request("GET", f"tasks/{item_id}/output")
        if not isinstance(data, list):
            raise HTTPException(502, "Semaphore 日志格式不兼容")
        return {"lines": [str(row.get("output", "")) for row in data if isinstance(row, dict)]}

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    return app
