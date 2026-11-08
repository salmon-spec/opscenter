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
import httpx
from pydantic import BaseModel, ConfigDict, Field

from runtime.app import create_module_app
from runtime.auth import SessionVerifier
from rpc import server
from provider import flatten, restore_event, restore_response, has_image, chat_request, chat_events
from images import generate as generate_image, MAX_IMAGE
from changes import read_changes

VERSION = '0.6.0'
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
    return dict(row)


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
        provider_last_error={'status':upstream.status_code,'message':provider_message(content)}
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
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class NewWorkspace(Input):
    name: str = Field(pattern='^[a-z0-9][a-z0-9_-]{0,40}$')
    title: str = Field(min_length=1,max_length=100)


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
def workspaces():
    with database() as db: return {'items':[dict(r) for r in db.execute('select * from workspaces')]}


@app.post('/api/v1/workspaces')
def workspace_create(body:NewWorkspace):
    if body.name=='default': raise HTTPException(409,'工作区已存在')
    with database() as db:
        try: db.execute('insert into workspaces values(?,?)',(body.name,body.title))
        except sqlite3.IntegrityError: raise HTTPException(409,'工作区已存在') from None
    audit('workspace.create',body.name)
    return {'id':body.name}


@app.get('/api/v1/threads')
def threads(archived:bool=False):
    with database() as db:
        return {'items':[dict(r) for r in db.execute('select * from threads where workspace=? and archived=? order by rowid desc',(workspace.get(),int(archived)))]}


@app.post('/api/v1/threads')
def thread_create(body:ThreadCreate):
    result = server.request('thread/start',{'cwd':str(directory()),'model':body.model or os.getenv('CODEX_MODEL') or None,
        'approvalPolicy':'on-request','sandbox':'workspace-write',
        'developerInstructions':IMAGE_INSTRUCTIONS})
    ident = result['thread']['id']
    with database() as db: db.execute('insert into threads(id,workspace,title) values(?,?,?)',(ident,workspace.get(),body.title))
    server.request('thread/name/set',{'threadId':ident,'name':body.title})
    audit('thread.create',ident)
    return {'id':ident}


@app.get('/api/v1/threads/{ident}')
def thread_read(ident:str):
    row = own(ident)
    result = server.request('thread/read',{'threadId':ident,'includeTurns':True})
    with server.lock:active=server.active_turns.get(ident)
    if active:
        for turn in result['thread'].get('turns',[]):
            if turn['id']==active:turn['status']='inProgress'
    result['running_turn_id']=active
    images=generated_images(ident)
    for turn in result['thread'].get('turns',[]):
        turn['images']=[i for i in images if i['turn_id']==turn['id']]
        turn.update(server.load_public(ident,turn['id']))
        turn['items']=[i for i in turn.get('items',[]) if i.get('type')!='reasoning']
    result['metadata'] = row
    return redact(result)


@app.post('/api/v1/threads/{ident}/edit')
def thread_edit(ident:str,body:ThreadEdit):
    own(ident)
    if body.title is not None:
        server.request('thread/name/set',{'threadId':ident,'name':body.title})
        with database() as db: db.execute('update threads set title=? where id=?',(body.title,ident))
    if body.archived is not None:
        server.request('thread/archive' if body.archived else 'thread/unarchive',{'threadId':ident})
        with database() as db: db.execute('update threads set archived=? where id=?',(int(body.archived),ident))
    audit('thread.edit',ident)
    return {'updated':True}


@app.post('/api/v1/threads/{ident}/prompt')
def prompt(ident:str,body:Prompt):
    own(ident)
    with mutation:
        server.start()
        retry_after=provider_state()['retry_after']
        if retry_after:
            raise HTTPException(429,f'模型服务正在限流，请等待 {retry_after} 秒后再试。',headers={'Retry-After':str(retry_after)})
        with server.lock:
            if server.active_turns:
                raise HTTPException(409,'已有任务正在执行，请等待完成或先停止该任务。')
        with server.lock: loaded=ident in server.loaded_threads
        if not loaded:
            server.request('thread/resume',{'threadId':ident,'cwd':str(directory()),'excludeTurns':True,
                                          'developerInstructions':IMAGE_INSTRUCTIONS})
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
            'approvalPolicy':'never' if body.mode in {'full','plan'} else 'on-request','sandboxPolicy':policy})
        with server.lock:
            server.active_turns[ident]=result['turn']['id']
            server.turn_modes[(ident,result['turn']['id'])]=body.mode
        return result


@app.post('/api/v1/threads/{ident}/stop')
def stop(ident:str):
    own(ident)
    with mutation:
        data = server.request('thread/read',{'threadId':ident,'includeTurns':True})
        active = next((t for t in reversed(data['thread'].get('turns',[])) if t.get('status')=='inProgress'),None)
        with server.lock:live_id=server.active_turns.get(ident)
        if live_id and (not active or active['id']!=live_id):active={'id':live_id,'items':[]}
        # Native unified-exec sessions can outlive a turn interrupt. Scope cleanup
        # to the thread id Codex injects into its tool process environment.
        call_ids=[i['id'] for i in (active or {}).get('items',[]) if i.get('type')=='commandExecution']
        if active:
            with server.lock:
                call_ids.extend(server.active_calls.get((ident,active['id']),set()))
                call_ids.extend(e['params']['item']['id'] for e in server.events
                    if e.get('method') in {'item/started','item/completed'}
                    and e.get('params',{}).get('threadId')==ident
                    and e['params'].get('turnId')==active['id']
                    and e['params'].get('item',{}).get('type')=='commandExecution')
            call_ids=list(set(call_ids))
        if call_ids:
            # Separate watcher handles native late-spawn races. Native call ids
            # prevent it from affecting any subsequent turn in this thread.
            subprocess.Popen([sys.executable,str(Path(__file__).with_name('cancel_tools.py')),ident,json.dumps(call_ids)],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        if active:
            with server.lock: server.cancelled_turns.add((ident,active['id']))
            server.request('turn/interrupt',{'threadId':ident,'turnId':active['id']})
    audit('turn.stop',ident)
    return {'interrupted':bool(active),'cancelled_tool_calls':len(call_ids)}



@app.get('/api/v1/events')
def events(cursor:int=0):
    with database() as db: owned={r[0] for r in db.execute('select id from threads where workspace=?',(workspace.get(),))}
    def permitted(message):
        if message.get('method','').startswith('item/reasoning/') or message.get('params',{}).get('item',{}).get('type')=='reasoning':
            return False
        params=message.get('params',{})
        ident=params.get('threadId') or params.get('thread',{}).get('id')
        return ident in owned
    with server.lock:
        return redact({'cursor':server.cursor,'generation':server.generation,'provider':provider_state(),
            'refresh':bool(server.events and cursor<server.events[0]['cursor']-1),
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
