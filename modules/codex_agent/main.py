"""Administrator-only Codex module; native app-server owns agent execution/history."""
import base64
import binascii
import json
import hmac
import html
import math
import asyncio
import mimetypes
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager, asynccontextmanager, suppress
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.responses import StreamingResponse
from fastapi.responses import Response
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from runtime.app import create_module_app
from runtime.auth import SessionVerifier
from rpc import server
from provider import flatten, restore_event, restore_response, has_image, chat_request, chat_events
from images import generate as generate_image, MAX_IMAGE, raster_type
from changes import read_changes
import lifecycle
from conversation_ops import fork_conversation, cleanup_conversation
from gateway_history import copy_public_history, cleanup_public_history

VERSION = '0.7.4'
IMAGE_INSTRUCTIONS = ("Work in the current workspace and preserve user files. Reply in Chinese unless requested otherwise. "
    "For image creation requests, an installed image tool uses the configured Agent API. "
    "Run python /opt/module/codex_agent/image_tool.py --prompt '<image description>' using the terminal tool, "
    "optionally --ratio 16:9. Generate directly with reasonable defaults for short requests such as a dog or a forest. "
    "The generated image is attached to this conversation automatically; include its returned Markdown path in your answer. "
    "Use this installed tool rather than installing image SDKs or searching for credentials. "
    "Credentials are managed by the service: never read, print, or search secret files or authentication environment values. "
    "Report actual failures and respect rate limits; do not invent generated files. "
    "For multi-step work maintain a concise plan. Publish short Chinese progress updates describing the action, "
    "its purpose and verified result at meaningful stages. Keep private reasoning hidden; public updates should "
    "be brief and observable. Handle simple tasks directly without an unnecessary plan.")
ROOT = Path(os.getenv('CODEX_WORKSPACE', '/workspace'))
STATE = Path(os.getenv('CODEX_GATEWAY_DATA', '/state/gateway'))
workspace = ContextVar('workspace', default='default')
mutation = threading.RLock()
prompt_start_lock = threading.Lock()
operation_threads = set()
operation_projects = set()
RASTER = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}
provider_models = (0, [])
models_lock = threading.Lock()
database_lock = threading.Lock()
initialized_databases = set()
provider_client = httpx.AsyncClient(trust_env=False,timeout=httpx.Timeout(20,read=180))
# ponytail: one inference stream per saved key; raise only when upstream concurrency permits.
inference_gate = asyncio.Semaphore(1)
provider_last_error = None
provider_retry_at = 0


def image_model():
    host=(httpx.URL(os.getenv('CODEX_API_BASE','https://api.openai.com/v1')).host or '').lower()
    return os.getenv('CODEX_IMAGE_MODEL','agnes-image-2.5-flash' if host.endswith(('agnes-ai.cn','agnes-ai.com')) else '')


def redact(value):
    try: key=Path(os.environ['CODEX_API_KEY_FILE']).read_text().strip()
    except (KeyError,OSError): key=''
    def scrub(item):
        if isinstance(item,str): return item.replace(key,'[REDACTED]') if key else item
        if isinstance(item,list): return [scrub(x) for x in item]
        if isinstance(item,dict): return {k:scrub(v) for k,v in item.items()}
        return item
    return scrub(value)


def generated_images(ident):
    result=[]
    for p in (STATE/'images'/ident).glob('*.json'):
        try:
            item=json.loads(p.read_text(encoding='utf8'))
            target=Path(item['path']).resolve()
            if target.is_relative_to(directory()) and file_path(target.relative_to(directory()).as_posix()).is_file():
                result.append(item)
        except (ValueError,OSError,KeyError,HTTPException): continue
    return sorted(result,key=lambda i:i['created_at'])[-100:]


def attach_viewed_images(turn):
    """Promote real native image views into the same persistent-read image cards."""
    root=directory()
    seen={str(Path(image['path']).resolve()) for image in turn['images']}
    for item in turn.get('items',[]):
        if len(turn['images'])>=100:break
        if item.get('type')!='imageView' or not isinstance(item.get('path'),str):continue
        try:
            source=Path(item['path'])
            relative=source.resolve().relative_to(root).as_posix() if source.is_absolute() else item['path']
            target=file_path(relative)
            if str(target) in seen or not target.is_file():continue
            # Read only the raster signature; never load image bodies on polls.
            with os.fdopen(os.open(target,os.O_RDONLY|getattr(os,'O_NONBLOCK',0)),'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=MAX_IMAGE:continue
                mime,_=raster_type(stream.read(12))
            if mimetypes.guess_type(target.name)[0] not in RASTER:continue
            image={'id':'native-'+uuid.uuid5(uuid.NAMESPACE_URL,turn['id']+':'+str(target)).hex,
                   'turn_id':turn['id'],'path':str(target),'mime':mime,'source':'imageView',
                   'description':'对话图片'}
            turn['images'].append(image);seen.add(str(target))
        except (ValueError,OSError,HTTPException):continue


def provider_state():
    return {'retry_at':provider_retry_at,'retry_after':max(0,math.ceil(provider_retry_at-time.time())),
            'last_error':provider_last_error}


def retry_seconds(value):
    try: delay=float(value)
    except (TypeError,ValueError):
        try: delay=parsedate_to_datetime(value).timestamp()-time.time()
        except (TypeError,ValueError,OverflowError): delay=60
    return max(1,min(3600,math.ceil(delay))) if math.isfinite(delay) else 60


def provider_message(content):
    value=content
    for _ in range(3):
        if not isinstance(value,str): break
        try: value=json.loads(value)
        except ValueError: break
        if isinstance(value,dict): value=value.get('error',value)
        if isinstance(value,dict): value=value.get('message',content)
    return html.unescape(value if isinstance(value,str) else content)[:1200]


@contextmanager
def database():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(STATE/'gateway.sqlite', timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with database_lock:
            if STATE not in initialized_databases:
                db.execute('pragma journal_mode=WAL')
                db.execute('create table if not exists workspaces(id text primary key,title text not null)')
                db.execute("insert or ignore into workspaces values('default','默认工作区')")
                db.execute('create table if not exists threads(id text primary key,workspace text,title text,archived integer default 0)')
                db.execute('create table if not exists audit(id integer primary key,action text,target text,created_at text)')
                lifecycle.initialize(db)
                db.commit()
                initialized_databases.add(STATE)
        yield db
        db.commit()
    finally:
        db.close()


def audit(action, target=''):
    with database() as db:
        db.execute('insert into audit(action,target,created_at) values(?,?,?)',
                   (action, target, datetime.now(timezone.utc).isoformat()))


def directory():
    path = ROOT if workspace.get() == 'default' else ROOT/'projects'/workspace.get()
    if not path.resolve().is_relative_to(ROOT.resolve()):raise HTTPException(422,'项目目录超出工作区')
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def file_path(value):
    if not value or '\\' in value or '\0' in value or Path(value).is_absolute() or '..' in Path(value).parts:
        raise HTTPException(422, '仅允许当前工作区中的相对路径')
    root = directory()
    path = (root/value).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(422, '文件路径超出工作区')
    return path


def own(ident):
    with database() as db:
        row = db.execute('select * from threads where id=? and workspace=?', (ident,workspace.get())).fetchone()
    if row is None:
        raise HTTPException(404, '任务不存在')
    result=dict(row)
    with database() as db:result['deleted']=lifecycle.state(db,'thread',ident)['deleted']
    return result


def project_record(ident):
    with database() as db:
        row=db.execute('select * from workspaces where id=?',(ident,)).fetchone()
        if row is None:raise HTTPException(404,'项目不存在')
        flags=lifecycle.state(db,'workspace',ident)
    return {**dict(row),'archived':flags['archived'],'deleted':flags['deleted']}


def require_project_active(ident):
    row=project_record(ident)
    if row['deleted'] or row['archived']:raise HTTPException(409,'项目已归档或移入回收站，请先恢复项目')
    return row


def project_chats(ident):
    with database() as db:return [dict(row) for row in db.execute('select * from threads where workspace=?',(ident,))]


@contextmanager
def targeted_mutation(*,threads=(),project=None,whole_project=False):
    # Reserve only affected targets; never hold the global lock through RPC/I/O.
    with mutation:
        affected=set(threads)
        if whole_project:affected.update(row['id'] for row in project_chats(project))
        if affected.intersection(operation_threads) or project is not None and project in operation_projects:
            raise HTTPException(409,'该目标正在执行其他操作，请稍后重试')
        operation_threads.update(affected)
        if project is not None:operation_projects.add(project)
    try:yield
    finally:
        with mutation:
            operation_threads.difference_update(affected)
            if project is not None:operation_projects.discard(project)


@contextmanager
def prompt_submission():
    if not prompt_start_lock.acquire(blocking=False):raise HTTPException(409,'已有消息正在发送，请稍后重试')
    try:yield
    finally:prompt_start_lock.release()


def activity_snapshot():
    with server.lock:
        active=dict(server.active_turns)
        backgrounds={thread for (thread,turn),items in server.active_commands.items()
                     if items and (thread,turn) in server.completed_turns}
    with mutation:
        for ident in operation_threads:active.setdefault(ident,None)
        projects=set(operation_projects)
    return active,backgrounds,projects


def require_chat_editable(row):
    require_project_active(row['workspace'])
    if row.get('deleted'):raise HTTPException(409,'对话已移入回收站，请先恢复')
    lifecycle.require_idle(server,[row['id']])


def project_path(ident):
    root=ROOT.resolve();projects=root/'projects'
    if ident=='default':return root
    if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,40}',ident):raise HTTPException(422,'项目名称无效')
    target=projects/ident
    if projects.is_symlink() or target.is_symlink() or not target.resolve().is_relative_to(root):
        raise HTTPException(422,'项目目录边界无效')
    return target


def health():
    server.start()


def summary():
    return {'status':'ok', 'collected_at':datetime.now(timezone.utc),
            'data':{'version':VERSION, 'engine':'Codex app-server', 'ready':bool(server.process and server.process.poll() is None)}}


app = create_module_app('codex-agent', VERSION, summary, lambda:{'status':'ok','items':[]}, readiness_provider=health)


@asynccontextmanager
async def lifespan(app):
    try: yield
    finally: await provider_client.aclose()


app.router.lifespan_context = lifespan


@app.post('/provider/v1/responses')
async def provider_responses(request:Request):
    global provider_last_error,provider_retry_at
    key_file=os.getenv('CODEX_API_KEY_FILE','')
    if request.client.host not in {'127.0.0.1','::1'} or not key_file:
        raise HTTPException(404,'Not found')
    key=Path(key_file).read_text().strip()
    if not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+key):
        raise HTTPException(401,'Provider authentication required')
    raw=await request.body()
    if len(raw)>32*1024*1024:raise HTTPException(413,'Request too large')
    try:body,mapping=flatten(redact(json.loads(raw)))
    except (ValueError,KeyError,TypeError):raise HTTPException(422,'Invalid Responses request') from None
    client=provider_client
    image_compat=has_image(body)
    payload=chat_request(body) if image_compat else body
    await inference_gate.acquire()
    if provider_state()['retry_after']:
        inference_gate.release()
        return JSONResponse({'error':{'message':'模型服务限流冷却中，请稍后重试。'}},status_code=429,
                            headers={'Retry-After':str(provider_state()['retry_after'])})
    try:
        upstream=await client.send(client.build_request('POST',os.environ['CODEX_API_BASE'].rstrip('/')+('/chat/completions' if image_compat else '/responses'),
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},json=payload),stream=True)
    except httpx.HTTPError:
        inference_gate.release();raise HTTPException(502,'Model provider unavailable') from None
    except BaseException:
        inference_gate.release();raise
    if upstream.status_code!=200:
        try: content=(await upstream.aread())[:4000].decode(errors='replace').replace(key,'[redacted]')
        except httpx.HTTPError:
            await upstream.aclose();inference_gate.release()
            raise HTTPException(502,'Model provider error response unavailable') from None
        provider_last_error={'status':upstream.status_code,'message':redact(provider_message(content))}
        headers={}
        if upstream.status_code==429:
            delay=retry_seconds(upstream.headers.get('retry-after'));provider_retry_at=time.time()+delay
            headers['Retry-After']=str(delay)
        status=upstream.status_code;await upstream.aclose();inference_gate.release()
        return JSONResponse({'error':{'message':provider_last_error['message']}},status_code=status,headers=headers)
    provider_last_error=None;provider_retry_at=0
    if not body.get('stream'):
        try:return restore_response(json.loads(await upstream.aread()),mapping)
        finally:await upstream.aclose();inference_gate.release()
    async def stream():
        custom_ids=set()
        try:
            if image_compat:
                async for event in chat_events(upstream,body['model']):
                    event=restore_event(event,mapping,custom_ids)
                    if event is not None:yield 'event: '+event.get('type','message')+'\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\n'
                return
            async for line in upstream.aiter_lines():
                if not line.startswith('data:'):continue
                data=line[5:].strip()
                if data=='[DONE]':yield 'data: [DONE]\n\n';continue
                try:event=restore_event(json.loads(data),mapping,custom_ids)
                except ValueError:continue
                if event is not None:yield 'event: '+event.get('type','message')+'\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\n'
        finally:
            await upstream.aclose();inference_gate.release()
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store'})


@app.middleware('http')
async def authorize(request: Request, call_next):
    if request.url.path.startswith('/api/v1/'):
        try:
            scheme, _, token = request.headers.get('authorization','').partition(' ')
            if scheme.lower() != 'bearer' or not token:
                raise HTTPException(401, '请先登录运维工作台')
            user = SessionVerifier().verify(token)
            if user['role'] != 'admin':
                raise HTTPException(403, 'Codex 模块需要管理员权限')
            key = request.query_params.get('workspace','default')
            if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,40}',key):
                raise HTTPException(422,'工作区名称无效')
            with database() as db:
                if not db.execute('select 1 from workspaces where id=?',(key,)).fetchone():
                    raise HTTPException(404,'工作区不存在')
        except HTTPException as exc:
            return JSONResponse({'detail':exc.detail},status_code=exc.status_code)
        scope = workspace.set(key)
        try: return await call_next(request)
        finally: workspace.reset(scope)
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def invalid(request, exc):
    return JSONResponse({'detail':'字段或格式无效，请检查输入'},status_code=422)


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('title','name',mode='before',check_fields=False)
    @classmethod
    def clean_display_name(cls,value):
        return value.strip() if isinstance(value,str) else value


class NewWorkspace(Input):
    name: str | None = Field(default=None,pattern='^[a-z0-9][a-z0-9_-]{0,40}$')
    title: str = Field(min_length=1,max_length=100)


class WorkspaceEdit(Input):
    title: str | None = Field(default=None,min_length=1,max_length=100)
    archived: bool | None = None


class WorkspaceCopy(Input):
    title: str | None = Field(default=None,min_length=1,max_length=100)
    include_files: bool = True
    include_threads: bool = True


class ThreadCopy(Input):
    title: str | None = Field(default=None,min_length=1,max_length=120)


class ThreadFork(ThreadCopy):
    turn_id: str | None = Field(default=None,pattern='^[A-Za-z0-9_-]{1,100}$')


class ThreadCreate(Input):
    title: str = Field(default='新任务',min_length=1,max_length=120)
    model: str | None = Field(default=None,max_length=120)


class ThreadEdit(Input):
    title: str | None = Field(default=None,min_length=1,max_length=120)
    archived: bool | None = None


class Prompt(Input):
    text: str = Field(min_length=1,max_length=20000)
    model: str = Field(min_length=1,max_length=120)
    effort: Literal['minimal','low','medium','high','xhigh','max'] = 'medium'
    mode: Literal['ask','full','plan'] = 'ask'
    attachments: list[str] = Field(default_factory=list,max_length=10)


class Approval(Input):
    decision: Literal['accept','decline','cancel'] | None = None
    answers: dict[str,list[str]] | None = None


class FileWrite(Input):
    path: str = Field(min_length=1,max_length=500)
    content: str = Field(max_length=2800000)
    encoding: Literal['utf8','base64'] = 'utf8'
    overwrite: bool = False


class Login(Input):
    type: Literal['apiKey','chatgptDeviceCode']
    api_key: str = Field(default='',max_length=4096)


class Command(Input):
    command: str = Field(min_length=1,max_length=10000)


class ImageRequest(Input):
    thread_id: str = Field(pattern='^[A-Za-z0-9_-]{1,100}$')
    call_id: str = Field(min_length=1,max_length=200)
    prompt: str = Field(min_length=1,max_length=4000)
    ratio: Literal['1:1','3:4','4:3','16:9','9:16','2:3','3:2','21:9'] = '1:1'


@app.post('/provider/v1/images')
async def provider_images(request:Request,body:ImageRequest):
    global provider_last_error,provider_retry_at
    key_file=os.getenv('CODEX_API_KEY_FILE','')
    if request.client.host not in {'127.0.0.1','::1'} or not key_file: raise HTTPException(404,'Not found')
    key=Path(key_file).read_text().strip()
    if not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+key): raise HTTPException(401,'Unauthorized')
    with database() as db: row=db.execute('select workspace from threads where id=?',(body.thread_id,)).fetchone()
    if row is None: raise HTTPException(404,'对话不存在')
    with server.lock:
        turn_id=server.active_turns.get(body.thread_id)
    def require_active():
        # Caller holds server.lock: bind every phase to the original tool and turn.
        pair=(body.thread_id,turn_id)
        if (not turn_id or server.active_turns.get(body.thread_id)!=turn_id
            or pair in server.cancelled_turns or body.call_id not in server.active_calls.get(pair,set())):
            raise HTTPException(409,'生成任务已停止或工具轮次已变化')
        if server.turn_modes.get(pair) not in {'ask','full'}:
            raise HTTPException(403,'当前权限不允许生成文件，请在询问或完全控制模式中重试')
    with server.lock: require_active()
    model=image_model()
    if not model: raise HTTPException(503,'当前 API 尚未配置图片生成模型')
    async with inference_gate:
        with server.lock: require_active()
        delay=provider_state()['retry_after']
        if delay: raise HTTPException(429,f'模型服务限流，请等待 {delay} 秒')
        server.emit('item/image/generating',{'threadId':body.thread_id,'turnId':turn_id})
        generation=asyncio.create_task(generate_image(os.environ['CODEX_API_BASE'],key,model,redact(body.prompt),body.ratio))
        try:
            while not generation.done():
                await asyncio.wait({generation},timeout=0.25)
                with server.lock:require_active()
            result=await generation
        except (httpx.HTTPError,ValueError,OSError,TimeoutError,KeyError,IndexError) as exc:
            raise HTTPException(502,str(redact(str(exc)))[:800]) from None
        finally:
            # Closing the local connection cannot promise cancellation of the
            # provider's already accepted compute or charge.
            if not generation.done():
                generation.cancel()
                with suppress(asyncio.CancelledError,httpx.HTTPError):await generation
            server.emit('item/image/finished',{'threadId':body.thread_id,'turnId':turn_id})
        if 'error' in result:
            provider_last_error={'status':result['status'],'message':redact(provider_message(result['error']))}
            if result['status']==429: provider_retry_at=time.time()+retry_seconds(result.get('retry_after'))
            raise HTTPException(result['status'],provider_last_error['message'])
        scope=workspace.set(row['workspace'])
        created=[]
        try:
            # Commit and publish under the same lock as stop/cancel; no late writes.
            with server.lock:
                require_active()
                ident=uuid.uuid4().hex
                path=file_path('output/imagegen/'+ident+result['suffix'])
                path.parent.mkdir(parents=True,exist_ok=True)
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                created.append(path)
                with os.fdopen(fd,'wb') as f: f.write(result['data'])
                item={'id':ident,'turn_id':turn_id,'path':str(path),'mime':result['mime'],'model':model,
                      'created_at':datetime.now(timezone.utc).isoformat(),'description':redact(body.prompt[:200])}
                folder=STATE/'images'/body.thread_id;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
                record=folder/(ident+'.json')
                fd=os.open(record,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                created.append(record)
                with os.fdopen(fd,'w',encoding='utf8') as f:json.dump(item,f,ensure_ascii=False)
                audit('image.generated',body.thread_id)
                provider_last_error=None;provider_retry_at=0
                server.emit('item/image/generated',{'threadId':body.thread_id,'turnId':turn_id,'image':item})
        except (OSError,sqlite3.Error):
            for created_path in reversed(created):
                try:created_path.unlink(missing_ok=True)
                except OSError:pass
            raise HTTPException(502,'图片已生成，但保存文件失败，请稍后重试') from None
        finally: workspace.reset(scope)
        return item


@app.get('/ui', response_class=HTMLResponse)
@app.get('/ui/', response_class=HTMLResponse)
def page():
    root = Path(__file__).parent
    html = (root/'index.html').read_text(encoding='utf8')
    vendor = root/'vendor' if (root/'vendor').exists() else root.parent/'opencode_agent'/'vendor'
    for name in ['marked.js','purify.js']:
        html = html.replace('/* VENDOR:'+name+' */',(vendor/name).read_text(encoding='utf8').replace('</script','<\\/script'))
    return HTMLResponse(html,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
      'Content-Security-Policy':"default-src 'none'; img-src data: https: http:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; form-action 'none'; frame-ancestors 'self'"})


@app.get('/api/v1/status')
def status():
    server.start()
    return {'version':VERSION,'engine_version':os.getenv('CODEX_VERSION','0.166.0'),
            'account':server.request('account/read',{}), 'generation':server.generation,
            'provider':{'base_url':os.getenv('CODEX_API_BASE','https://api.openai.com/v1'),
                        'configured':bool(os.getenv('CODEX_API_KEY_FILE')),
                        'default_model':os.getenv('CODEX_MODEL',''),**provider_state()},
            'image_generation':{'enabled':bool(image_model()),'model':image_model()},
            'workspace':str(directory())}


@app.get('/api/v1/models')
def models():
    custom = os.getenv('CODEX_MODEL','')
    if os.getenv('CODEX_API_BASE') and os.getenv('CODEX_API_KEY_FILE'):
        global provider_models
        with models_lock:
            if time.monotonic()-provider_models[0]>60:
                try:
                    key=Path(os.environ['CODEX_API_KEY_FILE']).read_text().strip()
                    response=httpx.get(os.environ['CODEX_API_BASE'].rstrip('/')+'/models',
                        headers={'Authorization':'Bearer '+key},timeout=15,trust_env=False)
                    response.raise_for_status()
                    names=[m['id'] for m in response.json().get('data',[]) if isinstance(m.get('id'),str)]
                    provider_models=(time.monotonic(),names)
                except (httpx.HTTPError,ValueError,KeyError):
                    provider_models=(time.monotonic(),[custom] if custom else [])
            names=list(dict.fromkeys(([custom] if custom else [])+provider_models[1]))
            names=[m for m in names if not re.search('image|video',m,re.I)]
        return {'data':[{'id':m,'model':m,'displayName':m,'isDefault':m==custom} for m in names]}
    result = server.request('model/list',{'limit':100})
    if custom and not any(m.get('model')==custom for m in result.get('data',[])):
        result.setdefault('data',[]).insert(0,{'id':custom,'model':custom,'displayName':custom,'isDefault':True})
    return result


@app.get('/api/v1/workspaces')
def workspaces(view:Literal['active','archived','deleted','all']='active'):
    active,_,busy_projects=activity_snapshot()
    with database() as db:
        items=[]
        for row in db.execute('select * from workspaces'):
            flags=lifecycle.state(db,'workspace',row['id'])
            if view=='deleted' and not flags['deleted']:continue
            if view=='archived' and (flags['deleted'] or not flags['archived']):continue
            if view=='active' and (flags['deleted'] or flags['archived']):continue
            running=[r[0] for r in db.execute('select id from threads where workspace=?',(row['id'],)) if r[0] in active]
            items.append({**dict(row),'archived':flags['archived'],'deleted':flags['deleted'],
                          'active':bool(running or row['id'] in busy_projects),'active_thread_ids':running})
    return {'items':items}


@app.post('/api/v1/workspaces')
def workspace_create(body:NewWorkspace):
    ident=body.name or 'project-'+uuid.uuid4().hex[:16]
    if ident=='default': raise HTTPException(409,'工作区已存在')
    with mutation:
        project_path(ident)
        with database() as db:
            try: db.execute('insert into workspaces values(?,?)',(ident,body.title))
            except sqlite3.IntegrityError: raise HTTPException(409,'工作区已存在') from None
        audit('workspace.create',ident)
    return {'id':ident,'title':body.title}


@app.post('/api/v1/workspaces/{ident}/edit')
def workspace_edit(ident:str,body:WorkspaceEdit):
    with targeted_mutation(project=ident,whole_project=True):
        row=project_record(ident);lifecycle.require_idle(server,[r['id'] for r in project_chats(ident)],check_goals=body.archived is True)
        if row['deleted']:raise HTTPException(409,'项目已移入回收站，请先恢复')
        if ident=='default' and body.archived is not None:raise HTTPException(409,'默认项目不能归档')
        with database() as db:
            if body.title is not None:db.execute('update workspaces set title=? where id=?',(body.title,ident))
            if body.archived is not None:lifecycle.update(db,'workspace',ident,archived=body.archived)
        audit('workspace.edit',ident)
    return {'updated':True,**project_record(ident)}


@app.post('/api/v1/workspaces/{ident}/delete')
def workspace_delete(ident:str):
    with targeted_mutation(project=ident,whole_project=True):
        project_record(ident)
        if ident=='default':raise HTTPException(409,'默认项目不能删除')
        lifecycle.require_idle(server,[r['id'] for r in project_chats(ident)],check_goals=True)
        with database() as db:lifecycle.update(db,'workspace',ident,deleted=True)
        audit('workspace.delete',ident)
    return {'id':ident,'deleted':True,'recoverable':True}


@app.post('/api/v1/workspaces/{ident}/restore')
def workspace_restore(ident:str):
    with targeted_mutation(project=ident,whole_project=True):
        project_record(ident);lifecycle.require_idle(server,[r['id'] for r in project_chats(ident)])
        with database() as db:lifecycle.update(db,'workspace',ident,deleted=False)
        audit('workspace.restore',ident)
    return {'restored':True,**project_record(ident)}


@app.get('/api/v1/threads')
def threads(archived:bool=False,deleted:bool=False):
    active,backgrounds,_=activity_snapshot()
    with database() as db:
        items=[]
        for row in db.execute('select * from threads where workspace=? order by rowid desc',(workspace.get(),)):
            removed=lifecycle.state(db,'thread',row['id'])['deleted']
            if removed!=deleted or not deleted and bool(row['archived'])!=archived:continue
            items.append({**dict(row),'archived':bool(row['archived']),'deleted':removed,
                          'active':row['id'] in active,'running_turn_id':active.get(row['id']),
                          'background_running':row['id'] in backgrounds})
    return {'items':items}


@app.post('/api/v1/threads')
def thread_create(body:ThreadCreate):
    with targeted_mutation(project=workspace.get()):
        require_project_active(workspace.get())
        result = server.request('thread/start',{'cwd':str(directory()),'model':body.model or os.getenv('CODEX_MODEL') or None,
            'approvalPolicy':'on-request','sandbox':'workspace-write',
            'developerInstructions':IMAGE_INSTRUCTIONS})
        ident = result['thread']['id']
        try:
            server.request('thread/name/set',{'threadId':ident,'name':body.title})
            with database() as db: db.execute('insert into threads(id,workspace,title) values(?,?,?)',(ident,workspace.get(),body.title))
        except BaseException:
            cleanup_conversation(server,ident);raise
        audit('thread.create',ident)
    return {'id':ident}


@app.get('/api/v1/threads/{ident}')
def thread_read(ident:str):
    row = own(ident)
    result = server.request('thread/read',{'threadId':ident,'includeTurns':True})
    with server.lock:
        active=server.active_turns.get(ident)
        native_finished=dict(server.completed_turns)
        commands={turn:{key:dict(item) for key,item in items.items()}
                  for (thread,turn),items in server.active_commands.items() if thread==ident}
        recent={(turn,key):dict(item) for (thread,turn,key),item in server.recent_commands.items() if thread==ident}
    if active:
        for turn in result['thread'].get('turns',[]):
            if turn['id']==active and (ident,active) not in native_finished:turn['status']='inProgress'
    turns=result['thread'].setdefault('turns',[])
    for turn_id in set(commands)|{turn_id for turn_id,_ in recent}:
        turn=next((turn for turn in turns if turn['id']==turn_id),None)
        if turn is None:
            turn={'id':turn_id,'status':native_finished.get((ident,turn_id),'inProgress'),'items':[],'runtimeOnly':True}
            turns.append(turn)
        items={item['id']:item for item in turn.get('items',[])}
        for (record_turn,key),item in recent.items():
            if record_turn==turn_id:items[key]=item
        items.update(commands.get(turn_id,{}));turn['items']=list(items.values())
    for turn in turns:
        turn['activeCommandIds']=list(commands.get(turn['id'],{}))
        turn['backgroundRunning']=bool(turn['activeCommandIds'] and (ident,turn['id']) in native_finished)
    result['running_turn_id']=active
    with database() as db:mapping=lifecycle.state(db,'thread',ident)['metadata'].get('path_map',{})
    if isinstance(mapping,dict):
        for turn in result['thread'].get('turns',[]):
            for item in turn.get('items',[]):
                if item.get('type')=='imageView' and isinstance(item.get('path'),str) and item['path'] in mapping:item['path']=mapping[item['path']]
                for part in item.get('content',[]) if isinstance(item.get('content'),list) else []:
                    if isinstance(part,dict) and isinstance(part.get('path'),str) and part['path'] in mapping:part['path']=mapping[part['path']]
                if item.get('type')=='agentMessage':
                    for old,new in mapping.items():
                        if not isinstance(old,str) or not isinstance(new,str):continue
                        item['text']=item.get('text','').replace(']('+old+')',']('+new+')').replace('](<'+old+'>)','](<'+new+'>)').replace('`'+old+'`','`'+new+'`')
    images=generated_images(ident)
    for turn in result['thread'].get('turns',[]):
        turn['images']=[i for i in images if i['turn_id']==turn['id']]
        attach_viewed_images(turn)
        turn.update(server.load_public(ident,turn['id']))
        turn['items']=[i for i in turn.get('items',[]) if i.get('type')!='reasoning']
    result['metadata'] = row
    return redact(result)


@app.post('/api/v1/threads/{ident}/edit')
def thread_edit(ident:str,body:ThreadEdit):
    with targeted_mutation(threads=[ident]):
        row=own(ident);require_chat_editable(row)
        if body.archived is True:lifecycle.require_idle(server,[ident],check_goals=True)
        if body.title is not None:server.request('thread/name/set',{'threadId':ident,'name':body.title})
        if body.archived is not None:server.request('thread/archive' if body.archived else 'thread/unarchive',{'threadId':ident})
        with database() as db:
            if body.title is not None:db.execute('update threads set title=? where id=?',(body.title,ident))
            if body.archived is not None:db.execute('update threads set archived=? where id=?',(int(body.archived),ident))
        audit('thread.edit',ident)
    return {'updated':True}


@app.post('/api/v1/threads/{ident}/delete')
def thread_delete(ident:str):
    with targeted_mutation(threads=[ident]):
        row=own(ident);lifecycle.require_idle(server,[ident],check_goals=True)
        with database() as db:lifecycle.update(db,'thread',ident,deleted=True)
        audit('thread.delete',ident)
    return {'id':ident,'deleted':True,'recoverable':True}


@app.post('/api/v1/threads/{ident}/restore')
def thread_restore(ident:str):
    with targeted_mutation(threads=[ident]):
        row=own(ident);lifecycle.require_idle(server,[ident])
        with database() as db:lifecycle.update(db,'thread',ident,deleted=False)
        audit('thread.restore',ident)
    return {'id':ident,'restored':True,'deleted':False,'archived':bool(row['archived'])}


def copied_instructions(target_root):
    return IMAGE_INSTRUCTIONS+' Current project cwd: '+str(target_root)+'. Absolute paths in copied history are source-project references. Perform all new writes only in the current project; do not modify the source project.'


def clone_native(row,title,target_root,turn_id=None):
    source=server.request('thread/read',{'threadId':row['id'],'includeTurns':True})['thread']
    retained=list(source.get('turns',[]))
    if turn_id is not None:
        index=next((i for i,t in enumerate(retained) if t['id']==turn_id),None)
        if index is None:raise HTTPException(422,'分支轮次不属于当前对话')
        retained=retained[:index+1]
    created=fork_conversation(server,row['id'],title,target_root,turn_id,copied_instructions(target_root))
    try:
        target=server.request('thread/read',{'threadId':created,'includeTurns':True})['thread']
        turns=target.get('turns',[])
        if len(turns)!=len(retained):raise HTTPException(502,'复制后的对话轮次不完整，已撤销复制')
        mapping={old['id']:new['id'] for old,new in zip(retained,turns)}
        return created,mapping,source
    except BaseException:
        cleanup_conversation(server,created);raise


def copy_chat(ident,body,turn_id=None):
    with targeted_mutation(threads=[ident]):
        row=own(ident);require_chat_editable(row)
        lifecycle.require_idle(server,[ident],check_goals=True)
        title=body.title or (row['title']+(' 分支' if isinstance(body,ThreadFork) else ' 副本'))[:120]
        created,turn_map,_=clone_native(row,title,directory(),turn_id)
        history_created=False
        try:
            with database() as db:metadata=lifecycle.state(db,'thread',ident)['metadata']
            metadata={**metadata,'source_id':ident,'source_root':str(directory())}
            copy_public_history(STATE,ident,created,turn_map)
            history_created=True
            with database() as db:
                db.execute('insert into threads(id,workspace,title) values(?,?,?)',(created,row['workspace'],title))
                lifecycle.update(db,'thread',created,metadata=metadata)
                db.execute('insert into audit(action,target,created_at) values(?,?,?)',
                    ('thread.fork' if isinstance(body,ThreadFork) else 'thread.copy',created,datetime.now(timezone.utc).isoformat()))
        except BaseException:
            history_cleaned=not history_created or cleanup_public_history(STATE,created)
            native_cleaned=cleanup_conversation(server,created)
            if not history_cleaned or not native_cleaned:raise HTTPException(502,'复制失败，部分新副本未能清理，请刷新检查') from None
            raise
    return {'id':created,'title':title}


@app.post('/api/v1/threads/{ident}/copy')
def thread_copy(ident:str,body:ThreadCopy):return copy_chat(ident,body)


@app.post('/api/v1/threads/{ident}/fork')
def thread_fork(ident:str,body:ThreadFork):return copy_chat(ident,body,body.turn_id)


@app.get('/api/v1/threads/{ident}/export')
def thread_export(ident:str):
    from urllib.parse import quote
    data=thread_read(ident);parts=['# '+data['metadata']['title']+'\n']
    for turn in data['thread'].get('turns',[]):
        for item in turn.get('items',[]):
            if item.get('type')=='userMessage':
                content=item.get('content',[])
                content=[content] if isinstance(content,str) else content if isinstance(content,list) else []
                text='\n'.join(c if isinstance(c,str) else c['text'] for c in content
                    if isinstance(c,str) or isinstance(c,dict) and c.get('type')=='text' and isinstance(c.get('text'),str))
                if text:parts.append('## 用户\n\n'+text+'\n')
                for part in content:
                    if isinstance(part,dict) and part.get('type') in {'localImage','image'}:
                        value=part.get('path') or part.get('url')
                        if isinstance(value,str):parts.append('![用户图片]('+value+')\n')
            elif item.get('type')=='agentMessage' and item.get('phase')!='analysis':
                text=re.sub(r'<(think|thinking|analysis)\b[^>]*>[\s\S]*?(?:</\1\s*>|$)','',item.get('text',''),flags=re.I).strip()
                if text:parts.append('## '+('进展' if item.get('phase')=='commentary' else 'Codex')+'\n\n'+text+'\n')
            elif item.get('type')=='plan' and item.get('text'):parts.append('## 建议计划\n\n'+item['text']+'\n')
        if turn.get('plan'):parts.append('## 执行计划\n\n'+'\n'.join('- '+str(p.get('step','')) for p in turn['plan'])+'\n')
        for image in turn.get('images',[]):parts.append('![对话图片]('+image['path']+')\n')
    filename=re.sub(r'[\x00-\x1f/\\]','_',data['metadata']['title'])[:80]+'.md'
    return Response('\n'.join(parts),media_type='text/markdown; charset=utf-8',
                    headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe='')})


def conversation_image_mapping(source_id,native,source_root,target_root,staging,include_files,budget):
    """Copy image attachments only when full files were not requested."""
    with database() as db:previous=lifecycle.state(db,'thread',source_id)['metadata'].get('path_map',{})
    if not isinstance(previous,dict):previous={}
    values=set()
    for turn in native.get('turns',[]):
        for item in turn.get('items',[]):
            if item.get('type')=='imageView' and isinstance(item.get('path'),str):values.add(item['path'])
            for part in item.get('content',[]) if isinstance(item.get('content'),list) else []:
                if isinstance(part,dict) and part.get('type') in {'localImage','image'} and isinstance(part.get('path'),str):values.add(part['path'])
    for record in (STATE/'images'/source_id).glob('*.json'):
        try:
            if record.stat().st_size>64*1024:continue
            value=json.loads(record.read_text(encoding='utf8')).get('path')
            if isinstance(value,str):values.add(value)
        except (OSError,ValueError,AttributeError):continue
    mapping={};copied=0
    for value in values:
        current=previous.get(value,value)
        try:
            path=Path(current);path=(path if path.is_absolute() else source_root/path).resolve()
            relative=path.relative_to(source_root)
            if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=MAX_IMAGE:continue
            if mimetypes.guess_type(path.name)[0] not in RASTER:continue
            with path.open('rb') as stream:raster_type(stream.read(12))
        except (ValueError,OSError):continue
        destination=staging/relative
        if not include_files and not destination.exists():
            lifecycle.copy_regular(path,destination,budget);copied+=1
        if not destination.is_file():raise HTTPException(409,'图片附件在复制期间发生变化，请重试')
        mapped=str((target_root/relative).resolve())
        mapping[value]=mapped;mapping[str(path)]=mapped
        for old,new in previous.items():
            if new==str(path):mapping[old]=mapped
    return mapping,copied


@app.post('/api/v1/workspaces/{ident}/copy')
def workspace_copy(ident:str,body:WorkspaceCopy):
    if not body.include_files and not body.include_threads:raise HTTPException(422,'至少选择对话或项目文件')
    with targeted_mutation(project=ident,whole_project=True):
        source=project_record(ident)
        if ident=='default':raise HTTPException(409,'默认项目包含其他项目，不能整体复制')
        if source['deleted']:raise HTTPException(409,'项目已移入回收站，请先恢复')
        chats=project_chats(ident);lifecycle.require_idle(server,[row['id'] for row in chats],check_goals=True)
        with database() as db:chats=[row for row in chats if not lifecycle.state(db,'thread',row['id'])['deleted']]
        source_root=project_path(ident).resolve();created='project-'+uuid.uuid4().hex[:16]
        target_root=project_path(created);projects=target_root.parent;projects.mkdir(parents=True,exist_ok=True)
        if target_root.exists():raise HTTPException(409,'目标项目目录已存在，请重试')
        staging=Path(tempfile.mkdtemp(prefix='.copy-',dir=projects));owned=staging
        title=body.title or (source['title']+' 副本')[:100]
        budget={'files':0,'bytes':0};copies=[];assets=0
        try:
            if body.include_files:lifecycle.copy_tree(source_root,staging,budget)
            os.replace(staging,target_root);owned=target_root
            if body.include_threads:
                for row in chats:
                    new_id,turn_map,native=clone_native(row,row['title'],target_root)
                    row['_history_created']=False
                    copies.append((new_id,row))
                    path_map,count=conversation_image_mapping(row['id'],native,source_root,target_root,target_root,body.include_files,budget)
                    assets+=count
                    copy_public_history(STATE,row['id'],new_id,turn_map,{old:new for old,new in path_map.items() if Path(old).is_absolute()})
                    row['_history_created']=True
                    if row['archived']:server.request('thread/archive',{'threadId':new_id})
                    row['_path_map']=path_map
            with database() as db:
                db.execute('insert into workspaces(id,title) values(?,?)',(created,title))
                for new_id,row in copies:
                    db.execute('insert into threads(id,workspace,title,archived) values(?,?,?,?)',(new_id,created,row['title'],row['archived']))
                    lifecycle.update(db,'thread',new_id,metadata={'source_id':row['id'],'source_root':str(source_root),'path_map':row['_path_map']})
                db.execute('insert into audit(action,target,created_at) values(?,?,?)',('workspace.copy',created,datetime.now(timezone.utc).isoformat()))
        except BaseException:
            cleaned=True
            for new_id,copied_row in reversed(copies):
                if copied_row['_history_created']:cleaned=cleanup_public_history(STATE,new_id) and cleaned
                cleaned=cleanup_conversation(server,new_id) and cleaned
            lifecycle.remove_created(owned,projects)
            if not cleaned:raise HTTPException(502,'项目复制失败，部分新对话未能清理，请刷新检查') from None
            raise
    return {'id':created,'title':title,'copied_threads':len(copies),'image_assets_copied':assets}


@app.post('/api/v1/threads/{ident}/prompt')
def prompt(ident:str,body:Prompt):
    with prompt_submission(),targeted_mutation(threads=[ident],project=workspace.get()):
        row=own(ident);require_chat_editable(row)
        if row['archived']:raise HTTPException(409,'对话已归档，请先恢复对话')
        server.start()
        retry_after=provider_state()['retry_after']
        if retry_after:
            raise HTTPException(429,f'模型服务正在限流，请等待 {retry_after} 秒后再试。',headers={'Retry-After':str(retry_after)})
        with server.lock:
            if server.active_turns:
                raise HTTPException(409,'已有任务正在执行，请等待完成或先停止该任务。')
        with server.lock: loaded=ident in server.loaded_threads
        if not loaded:
            with database() as db:copied=lifecycle.state(db,'thread',ident)['metadata'].get('source_id')
            server.request('thread/resume',{'threadId':ident,'cwd':str(directory()),'excludeTurns':True,
                                          'developerInstructions':copied_instructions(directory()) if copied else IMAGE_INSTRUCTIONS})
        inputs = [{'type':'text','text':body.text}]
        for value in body.attachments:
            path = file_path(value)
            if not path.is_file() or path.stat().st_size>2*1024*1024:
                raise HTTPException(422,'附件不存在或超过 2 MiB')
            if mimetypes.guess_type(path.name)[0] in RASTER:
                inputs.append({'type':'localImage','path':str(path)})
            else:
                inputs.append({'type':'text','text':'Attached workspace file: '+str(path)})
        policy = {'type':'dangerFullAccess'} if body.mode=='full' else {'type':'readOnly','networkAccess':True} if body.mode=='plan' else {'type':'workspaceWrite','writableRoots':[str(directory())],'networkAccess':True}
        audit('turn.start',ident)
        result = server.request('turn/start',{'threadId':ident,'input':inputs,'model':body.model,'effort':body.effort,
            'approvalPolicy':'never' if body.mode in {'full','plan'} else 'on-request','sandboxPolicy':policy,
            'collaborationMode':{'mode':'plan' if body.mode=='plan' else 'default',
                'settings':{'model':body.model,'reasoning_effort':body.effort,'developer_instructions':None}}})
        with server.lock:
            pair=(ident,result['turn']['id'])
            if result['turn'].get('status')=='inProgress' and (pair not in server.completed_turns or server.active_commands.get(pair)):
                server.active_turns[ident]=result['turn']['id']
                server.turn_modes[pair]=body.mode
        return result


@app.post('/api/v1/threads/{ident}/stop')
def stop(ident:str):
    own(ident)
    with targeted_mutation(threads=[ident]):
        data = server.request('thread/read',{'threadId':ident,'includeTurns':True})
        with server.lock:
            finished=set(server.completed_turns)
            live_pairs={pair:set(items) for pair,items in server.active_commands.items() if pair[0]==ident and items}
            live_id=server.active_turns.get(ident)
        active = next((t for t in reversed(data['thread'].get('turns',[]))
                       if t.get('status')=='inProgress' and (ident,t['id']) not in finished),None)
        if live_id and not active:active={'id':live_id,'items':[]}
        if active and (ident,active['id']) in finished:active=None
        # Native unified-exec sessions can outlive a turn interrupt. Scope cleanup
        # to the thread id Codex injects into its tool process environment.
        call_ids=[call for calls in live_pairs.values() for call in calls]
        call_ids.extend(i['id'] for i in (active or {}).get('items',[]) if i.get('type')=='commandExecution' and i.get('status')=='inProgress')
        if active:
            with server.lock:
                call_ids.extend(server.active_calls.get((ident,active['id']),set()))
        call_ids=list(set(call_ids))
        with server.lock:
            server.cancelled_turns.update(live_pairs)
            if active:server.cancelled_turns.add((ident,active['id']))
        if call_ids:
            # Separate watcher handles native late-spawn races. Native call ids
            # prevent it from affecting any subsequent turn in this thread.
            subprocess.Popen([sys.executable,str(Path(__file__).with_name('cancel_tools.py')),ident,json.dumps(call_ids)],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        if active:
            try:server.request('turn/interrupt',{'threadId':ident,'turnId':active['id']})
            except HTTPException:
                with server.lock:ended=(ident,active['id']) in server.completed_turns
                if not ended:raise
                active=None
    audit('turn.stop',ident)
    return {'interrupted':bool(active),'cancelled_tool_calls':len(call_ids),'background_stop_requested':bool(live_pairs)}



@app.get('/api/v1/events')
def events(cursor:int=0):
    with database() as db: owned={r[0] for r in db.execute('select id from threads where workspace=?',(workspace.get(),))}
    def permitted(message):
        if message.get('method','').startswith('item/reasoning/') or message.get('params',{}).get('item',{}).get('type')=='reasoning':
            return False
        params=message.get('params',{})
        ident=params.get('threadId') or params.get('thread',{}).get('id')
        return ident in owned
    active,backgrounds,_=activity_snapshot()
    with server.lock:
        return redact({'cursor':server.cursor,'generation':server.generation,'provider':provider_state(),
            'refresh':bool(server.events and cursor<server.events[0]['cursor']-1),
            'active_threads':[{'threadId':ident,'turnId':turn,'workspace':workspace.get(),'backgroundRunning':ident in backgrounds}
                              for ident,turn in active.items() if ident in owned],
            'background_turns':[{'threadId':thread,'turnId':turn} for (thread,turn),items in server.active_commands.items()
                                if thread in owned and items and (thread,turn) in server.completed_turns],
            'events':[e for e in server.events if e['cursor']>cursor and permitted(e)],
            'pending':[m for m in server.approvals.values() if permitted(m)]})


@app.post('/api/v1/approvals/{ident}')
def approval(ident:str,body:Approval):
    with server.lock: message=server.approvals.get(ident)
    if not message: raise HTTPException(409,'请求已结束')
    own(message.get('params',{}).get('threadId',''))
    method=message['method']
    if method in {'item/commandExecution/requestApproval','item/fileChange/requestApproval'}:
        if body.decision is None: raise HTTPException(422,'请选择批准或拒绝')
        result={'decision':body.decision}
    elif method=='item/tool/requestUserInput':
        result={'answers':{k:{'answers':v} for k,v in (body.answers or {}).items()}}
    elif method=='item/permissions/requestApproval':
        # Extended native permissions are never silently granted through a generic decision.
        result={'permissions':{},'scope':'turn'}
    elif method=='mcpServer/elicitation/request':
        result={'action':'decline','content':None}
    else: raise HTTPException(422,'不支持该原生请求，请停止任务并检查扩展')
    server.reply(ident,result)
    audit('approval.'+(body.decision or 'answer'),ident)
    return {'answered':True}


@app.get('/api/v1/files')
def files(path:str=''):
    folder=file_path(path) if path else directory()
    if not folder.is_dir(): raise HTTPException(404,'目录不存在')
    return {'items':[{'name':p.name,'path':p.relative_to(directory()).as_posix(),'directory':p.is_dir(),'size':p.stat().st_size}
        for p in sorted(folder.iterdir(),key=lambda p:(not p.is_dir(),p.name.lower())) if not p.is_symlink()][:1000]}


@app.get('/api/v1/files/content')
def file_content(path:str):
    target=file_path(path)
    if not target.is_file(): raise HTTPException(404,'文件不存在')
    mime=mimetypes.guess_type(target.name)[0]
    limit=MAX_IMAGE if mime in RASTER else 2*1024*1024
    if target.stat().st_size>limit: raise HTTPException(413,'文件超过预览大小限制')
    content=target.read_bytes()
    if mime in RASTER: return {'image':'data:'+mime+';base64,'+base64.b64encode(content).decode()}
    try: return {'content':content.decode('utf8')}
    except UnicodeError: raise HTTPException(422,'该二进制格式不支持预览') from None


@app.post('/api/v1/files/write')
def write(body:FileWrite):
    with targeted_mutation(project=workspace.get()):
        require_project_active(workspace.get())
        target=file_path(body.path)
        try: content=base64.b64decode(body.content,validate=True) if body.encoding=='base64' else body.content.encode()
        except (ValueError,binascii.Error): raise HTTPException(422,'文件编码无效') from None
        if len(content)>2*1024*1024: raise HTTPException(413,'单文件限 2 MiB')
        target.parent.mkdir(parents=True,exist_ok=True)
        try:
            if body.overwrite:
                fd,name=tempfile.mkstemp(dir=target.parent,prefix='.codex-')
                try:
                    with os.fdopen(fd,'wb') as f: f.write(content)
                    os.replace(name,target)
                finally: Path(name).unlink(missing_ok=True)
            else:
                with target.open('xb') as f: f.write(content)
        except FileExistsError: raise HTTPException(409,'文件已存在，请确认覆盖') from None
        audit('file.write',body.path)
    return {'saved':True}


@app.post('/api/v1/terminal')
def terminal(body:Command):
    with targeted_mutation(project=workspace.get()):
        require_project_active(workspace.get())
        audit('terminal.execute')
        try:
            # An administrator's direct terminal; bounded process group and output file.
            with tempfile.TemporaryFile() as output:
                proc=subprocess.Popen(['bash','-lc',body.command],cwd=directory(),stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
                try: proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    import signal
                    os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                    raise HTTPException(504,'命令超过 20 秒，已终止进程组') from None
                output.seek(0)
                return redact({'exit_code':proc.returncode,'output':output.read(256*1024).decode(errors='replace')})
        except FileNotFoundError: raise HTTPException(503,'终端不可用') from None


@app.get('/api/v1/extensions')
def extensions():
    return {'skills':server.request('skills/list',{'cwds':[str(directory())],'forceReload':True}),
            'mcp':server.request('mcpServerStatus/list',{'limit':100})}


@app.get('/api/v1/changes')
def changes(path:str | None=None):
    return redact(read_changes(directory(),path))


@app.get('/api/v1/audit')
def audits():
    with database() as db: return {'items':[dict(r) for r in db.execute('select * from audit order by id desc limit 100')]}


@app.post('/api/v1/account/login')
def login(body:Login):
    params={'type':body.type}
    if body.type=='apiKey':
        if not body.api_key: raise HTTPException(422,'请输入 API Key')
        params['apiKey']=body.api_key
    audit('account.login')
    return server.request('account/login/start',params)
