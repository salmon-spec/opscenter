import importlib.util
import json
import sys
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE),str(HERE.parent)]
spec=importlib.util.spec_from_file_location('codex_gateway',HERE/'main.py')
main=importlib.util.module_from_spec(spec);spec.loader.exec_module(main)


@pytest.fixture
def env(tmp_path,monkeypatch):
    key=Ed25519PrivateKey.generate();public=tmp_path/'public.pem'
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv('OPS_JWT_PUBLIC_KEY_FILE',str(public))
    monkeypatch.setenv('OPS_ADMIN_USER','admin')
    monkeypatch.setattr(main,'STATE',tmp_path/'state');monkeypatch.setattr(main,'ROOT',tmp_path/'workspace')
    monkeypatch.setattr(main,'provider_retry_at',0)
    monkeypatch.setattr(main,'provider_last_error',None)
    calls=[]
    def rpc(method,params=None,**kw):
        calls.append((method,params))
        if method=='thread/start':return {'thread':{'id':'thread_test'}}
        if method in {'thread/read','thread/resume'}:return {'thread':{'id':'thread_test','turns':[]}}
        if method=='turn/start':return {'turn':{'id':'turn_test','status':'inProgress','items':[]}}
        return {}
    monkeypatch.setattr(main.server,'request',rpc)
    monkeypatch.setattr(main.server,'start',lambda:None)
    main.server.approvals.clear();main.server.events.clear()
    main.server.active_turns.clear()
    main.server.active_calls.clear()
    main.server.turn_modes.clear()
    main.server.cancelled_turns.clear()
    main.server.loaded_threads.clear()
    def token(role='admin'):
        return {'Authorization':'Bearer '+jwt.encode({'sub':'1','username':'admin','role':role,'exp':time.time()+300,'iss':'opscenter','aud':'opscenter-modules'},key,algorithm='EdDSA')}
    return TestClient(main.app),token,calls


def create(client,token):
    response=client.post('/api/v1/threads',headers=token(),json={'title':'Test','model':'agnes-2.5-flash'})
    assert response.status_code==200
    return response.json()['id']


def test_all_routes_fail_closed_and_keys_not_echoed(env):
    client,token,calls=env
    for route in ['/status','/models','/threads','/events','/files','/audit','/extensions','/changes']:
        assert client.get('/api/v1'+route).status_code==401
        assert client.get('/api/v1'+route,headers=token('viewer')).status_code==403
    assert not calls
    response=client.post('/api/v1/account/login',headers=token(),json={'type':'bad','api_key':'private-secret'})
    assert response.status_code==422 and 'private-secret' not in response.text
    assert "img-src data: https: http:" in client.get('/ui/').headers['content-security-policy']


def test_workspace_and_thread_isolation(env):
    client,token,calls=env;ident=create(client,token)
    assert client.post('/api/v1/workspaces',headers=token(),json={'name':'other','title':'Other'}).status_code==200
    assert client.get('/api/v1/threads/'+ident,headers=token(),params={'workspace':'other'}).status_code==404
    assert client.post('/api/v1/threads/'+ident+'/prompt',headers=token(),params={'workspace':'other'},json={'text':'test','model':'x'}).status_code==404
    assert client.get('/api/v1/events',headers=token(),params={'workspace':'missing'}).status_code==404


def test_file_validation_image_and_atomic_overwrite(env):
    client,token,_=env
    for path in ['../escape','/etc/passwd','..\\escape']:
        assert client.post('/api/v1/files/write',headers=token(),json={'path':path,'content':'x'}).status_code==422
    assert client.post('/api/v1/files/write',headers=token(),json={'path':'a.txt','content':'first'}).status_code==200
    assert client.post('/api/v1/files/write',headers=token(),json={'path':'a.txt','content':'lost'}).status_code==409
    assert client.get('/api/v1/files/content',headers=token(),params={'path':'a.txt'}).json()['content']=='first'
    assert client.post('/api/v1/files/write',headers=token(),json={'path':'a.txt','content':'saved','overwrite':True}).status_code==200
    assert client.post('/api/v1/files/write',headers=token(),json={'path':'p.png','content':'aW1hZ2U=','encoding':'base64'}).status_code==200
    assert client.get('/api/v1/files/content',headers=token(),params={'path':'p.png'}).json()['image']=='data:image/png;base64,aW1hZ2U='
    assert client.post('/api/v1/files/write',headers=token(),json={'path':'bad','content':'!','encoding':'base64'}).status_code==422


def test_prompt_native_modes_and_attachment_contract(env):
    client,token,calls=env;ident=create(client,token)
    client.post('/api/v1/files/write',headers=token(),json={'path':'p.png','content':'aW1hZ2U=','encoding':'base64'})
    for mode,policy,approval in [('ask','workspaceWrite','on-request'),('full','dangerFullAccess','never'),('plan','readOnly','never')]:
        main.server.active_turns.clear()
        r=client.post('/api/v1/threads/'+ident+'/prompt',headers=token(),json={'text':'hello','model':'agnes-2.5-flash','mode':mode,'attachments':['p.png']})
        assert r.status_code==200
        payload=next(p for m,p in reversed(calls) if m=='turn/start')
        assert payload['sandboxPolicy']['type']==policy and payload['approvalPolicy']==approval
        assert payload['input'][1]['type']=='localImage'
    assert client.post('/api/v1/threads/'+ident+'/prompt',headers=token(),json={'text':'hello','model':'x','cwd':'/etc'}).status_code==422


def test_approval_requires_ownership_and_events_do_not_cross_workspaces(env,monkeypatch):
    client,token,calls=env;ident=create(client,token)
    client.post('/api/v1/workspaces',headers=token(),json={'name':'other','title':'Other'})
    request={'id':10,'method':'item/commandExecution/requestApproval','params':{'threadId':ident,'command':'echo OK'}}
    main.server.approvals['10']=request
    main.server.events.append({'cursor':1,**request})
    assert client.get('/api/v1/events',headers=token(),params={'workspace':'other'}).json()['events']==[]
    assert client.post('/api/v1/approvals/10',headers=token(),params={'workspace':'other'},json={'decision':'accept'}).status_code==404
    replies=[];monkeypatch.setattr(main.server,'reply',lambda i,r:replies.append((i,r)))
    assert client.post('/api/v1/approvals/10',headers=token(),json={'decision':'accept'}).status_code==200
    assert replies==[('10',{'decision':'accept'})]


def test_cancel_watcher_never_targets_another_turn_or_thread():
    from cancel_tools import matches
    environment=b'CODEX_THREAD_ID=thread_a\0CODEX_TOOL_CALL_ID=call_1\0'
    assert matches(environment,'thread_a',{'call_1'})
    assert not matches(environment,'thread_b',{'call_1'})
    assert not matches(environment,'thread_a',{'call_2'})
    assert not matches(b'CODEX_THREAD_ID=thread_a_extra\0CODEX_TOOL_CALL_ID=call_1\0','thread_a',{'call_1'})


def test_live_turn_state_overrides_unfinished_persisted_placeholder(env,monkeypatch):
    client,token,calls=env;ident=create(client,token)
    main.server.active_turns[ident]='turn_live'
    monkeypatch.setattr(main.server,'request',lambda *a,**k:{'thread':{'id':ident,'turns':[{'id':'turn_live','status':'interrupted','items':[]}]}})
    data=client.get('/api/v1/threads/'+ident,headers=token()).json()
    assert data['running_turn_id']=='turn_live' and data['thread']['turns'][0]['status']=='inProgress'


def test_native_image_views_attach_on_thread_read_and_refresh(env,monkeypatch):
    client,token,_=env;ident=create(client,token)
    folder=main.ROOT/'output'/'imagegen';folder.mkdir(parents=True)
    # Native tools can save PNG bytes using a .jpg filename.
    image=folder/'puppy.jpg';image.write_bytes(b'\x89PNG\r\n\x1a\nimage-data')
    native={'thread':{'id':ident,'turns':[{'id':'native_turn','status':'completed','items':[
        {'id':'view1','type':'imageView','path':str(image)},
        {'id':'view2','type':'imageView','path':'output/imagegen/puppy.jpg'}]}]}}
    monkeypatch.setattr(main.server,'request',lambda *a,**k:json.loads(json.dumps(native)))
    first=client.get('/api/v1/threads/'+ident,headers=token()).json()['thread']['turns'][0]['images']
    second=client.get('/api/v1/threads/'+ident,headers=token()).json()['thread']['turns'][0]['images']
    assert len(first)==1 and first==second
    assert first[0]['path']==str(image.resolve()) and first[0]['mime']=='image/png'
    assert first[0]['source']=='imageView' and first[0]['id'].startswith('native-')
    assert client.get('/api/v1/files/content',headers=token(),params={'path':'output/imagegen/puppy.jpg'}).json()['image'].startswith('data:image/jpeg;base64,')


def test_native_image_views_reject_outside_invalid_and_oversize_files(env,monkeypatch,tmp_path):
    client,token,_=env;ident=create(client,token)
    outside=tmp_path/'outside.png';outside.write_bytes(b'\x89PNG\r\n\x1a\nvalid')
    invalid=main.ROOT/'invalid.png';invalid.write_bytes(b'not an image')
    unsupported=main.ROOT/'unsupported.txt';unsupported.write_bytes(b'\xff\xd8\xffimage-data')
    empty=main.ROOT/'empty.png';empty.write_bytes(b'')
    oversized=main.ROOT/'oversized.png'
    with oversized.open('wb') as stream:
        stream.write(b'\x89PNG\r\n\x1a\n');stream.truncate(main.MAX_IMAGE+1)
    paths=[str(outside),'../outside.png',str(invalid),str(unsupported),str(empty),str(oversized),str(main.ROOT/'missing.png'),'bad\0.png',None]
    native={'thread':{'id':ident,'turns':[{'id':'t','items':[
        {'id':str(index),'type':'imageView','path':path} for index,path in enumerate(paths)]}]}}
    monkeypatch.setattr(main.server,'request',lambda *a,**k:json.loads(json.dumps(native)))
    response=client.get('/api/v1/threads/'+ident,headers=token())
    assert response.status_code==200 and response.json()['thread']['turns'][0]['images']==[]


def test_native_image_views_dedupe_helper_images_and_respect_project_root(env,monkeypatch):
    client,token,_=env;ident=create(client,token)
    root_image=main.ROOT/'root.png';root_image.write_bytes(b'\x89PNG\r\n\x1a\nimage-data')
    folder=main.STATE/'images'/ident;folder.mkdir(parents=True)
    helper={'id':'helper-image','turn_id':'t','path':str(root_image),'created_at':'2026-11-08T00:00:00Z','mime':'image/png'}
    (folder/'helper-image.json').write_text(json.dumps(helper))
    native={'thread':{'id':ident,'turns':[{'id':'t','items':[{'id':'v','type':'imageView','path':str(root_image)}]}]}}
    monkeypatch.setattr(main.server,'request',lambda *a,**k:json.loads(json.dumps(native)))
    assert client.get('/api/v1/threads/'+ident,headers=token()).json()['thread']['turns'][0]['images']==[helper]
    # A project cannot import an image from the default workspace through history.
    client.post('/api/v1/workspaces',headers=token(),json={'name':'project','title':'Project'})
    with main.database() as db:db.execute('update threads set workspace=? where id=?',('project',ident))
    response=client.get('/api/v1/threads/'+ident,headers=token(),params={'workspace':'project'})
    assert response.status_code==200 and response.json()['thread']['turns'][0]['images']==[]


def test_reads_do_not_wait_for_database_writer(env):
    client,token,calls=env
    client.get('/api/v1/threads',headers=token())
    import sqlite3
    db=sqlite3.connect(main.STATE/'gateway.sqlite')
    try:
        db.execute('begin immediate')
        db.execute("insert into audit(action) values('held-write')")
        for path in ['/threads','/events','/workspaces']:
            before=time.monotonic()
            assert client.get('/api/v1'+path,headers=token()).status_code==200
            assert time.monotonic()-before<1
    finally: db.rollback();db.close()


def test_loaded_thread_sends_without_resuming_each_turn(env):
    client,token,calls=env;ident=create(client,token)
    main.server.loaded_threads.add(ident)
    calls.clear()
    assert client.post('/api/v1/threads/'+ident+'/prompt',headers=token(),json={'text':'hello','model':'x'}).status_code==200
    assert [m for m,p in calls]==['turn/start']


def test_cooldown_and_active_turn_prevent_duplicate_inference(env,monkeypatch):
    client,token,calls=env;ident=create(client,token);calls.clear()
    monkeypatch.setattr(main,'provider_retry_at',time.time()+60)
    r=client.post('/api/v1/threads/'+ident+'/prompt',headers=token(),json={'text':'hello','model':'x'})
    assert r.status_code==429 and int(r.headers['retry-after'])>0 and not calls
    monkeypatch.setattr(main,'provider_retry_at',0)
    main.server.active_turns[ident]='active'
    assert client.post('/api/v1/threads/'+ident+'/prompt',headers=token(),json={'text':'hello','model':'x'}).status_code==409
    assert not calls


def test_provider_429_is_not_retried_and_cooldown_does_not_contact_upstream(env,monkeypatch,tmp_path):
    import httpx
    client,token,calls=env
    key=tmp_path/'key';key.write_text('test-provider-key')
    monkeypatch.setenv('CODEX_API_KEY_FILE',str(key));monkeypatch.setenv('CODEX_API_BASE','https://provider.invalid/v1')
    requests=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(429,headers={'Retry-After':'60'},json={'error':{'message':'Too Many Request&#x73;'}})
    http=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(main,'provider_client',http)
    local=TestClient(main.app,client=('127.0.0.1',12345))
    for _ in range(2):
        r=local.post('/provider/v1/responses',headers={'Authorization':'Bearer test-provider-key'},json={'model':'x','input':[]})
        assert r.status_code==429 and int(r.headers['retry-after'])>0
    assert len(requests)==1
    assert main.provider_last_error['message']=='Too Many Requests'
    assert main.retry_seconds('invalid')==60 and main.retry_seconds('nan')==60


def test_generated_image_is_owned_by_thread_and_survives_history_read(env,monkeypatch,tmp_path):
    client,token,calls=env;ident=create(client,token)
    key=tmp_path/'key';key.write_text('image-test-secret')
    monkeypatch.setenv('CODEX_API_KEY_FILE',str(key));monkeypatch.setenv('CODEX_API_BASE','https://api.agnes-ai.cn/v1')
    from base64 import b64decode
    png=b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF1sAAAAASUVORK5CYII=')
    async def generate(*args):return {'data':png,'mime':'image/png','suffix':'.png'}
    monkeypatch.setattr(main,'generate_image',generate)
    local=TestClient(main.app,client=('127.0.0.1',12345))
    body={'thread_id':ident,'call_id':'image-call','prompt':'A small dog'}
    headers={'Authorization':'Bearer image-test-secret'}
    assert local.post('/provider/v1/images',headers=headers,json=body).status_code==409
    main.server.active_turns[ident]='image-turn';main.server.active_calls[(ident,'image-turn')]={'image-call'}
    main.server.turn_modes[(ident,'image-turn')]='plan'
    assert local.post('/provider/v1/images',headers=headers,json=body).status_code==403
    main.server.turn_modes[(ident,'image-turn')]='ask'
    response=local.post('/provider/v1/images',headers=headers,json=body);assert response.status_code==200
    image=response.json();assert main.Path(image['path']).read_bytes()==png
    monkeypatch.setattr(main.server,'request',lambda *a,**k:{'thread':{'id':ident,'turns':[{'id':'image-turn','status':'completed','items':[]}]}})
    data=client.get('/api/v1/threads/'+ident,headers=token()).json()
    assert data['thread']['turns'][0]['images'][0]['id']==image['id']
    events=client.get('/api/v1/events',headers=token()).json()['events']
    assert events[-1]['method']=='item/image/generated' and events[-1]['params']['image']['path']==image['path']
    relative=main.Path(image['path']).relative_to(main.ROOT).as_posix()
    assert client.get('/api/v1/files/content',headers=token(),params={'path':relative}).json()['image'].startswith('data:image/png;base64,')
    assert client.post('/provider/v1/images',headers=headers,json=body).status_code==404


def test_provider_key_is_redacted_from_saved_history_and_events(env,monkeypatch,tmp_path):
    client,token,calls=env;ident=create(client,token)
    key=tmp_path/'key';key.write_text('private-provider-value')
    monkeypatch.setenv('CODEX_API_KEY_FILE',str(key))
    data={'thread':{'id':ident,'turns':[{'id':'t','items':[{'type':'commandExecution','aggregatedOutput':'private-provider-value'}]}]}}
    monkeypatch.setattr(main.server,'request',lambda *a,**k:data)
    assert 'private-provider-value' not in client.get('/api/v1/threads/'+ident,headers=token()).text
    main.server.emit('item/completed',{'threadId':ident,'item':{'aggregatedOutput':'private-provider-value'}})
    response=client.get('/api/v1/events',headers=token())
    assert 'private-provider-value' not in response.text and '[REDACTED]' in response.text


def image_request_setup(env,monkeypatch,tmp_path):
    client,token,calls=env;ident=create(client,token)
    key=tmp_path/'image-key';key.write_text('local-image-test')
    monkeypatch.setenv('CODEX_API_KEY_FILE',str(key));monkeypatch.setenv('CODEX_API_BASE','https://api.agnes-ai.cn/v1')
    main.server.active_turns[ident]='image-turn'
    main.server.active_calls[(ident,'image-turn')]={'current-call'}
    main.server.turn_modes[(ident,'image-turn')]='ask'
    local=TestClient(main.app,client=('127.0.0.1',12345))
    return local,{'Authorization':'Bearer local-image-test'},{'thread_id':ident,'call_id':'current-call','prompt':'A dog'}


def test_old_tool_call_and_unknown_permissions_never_generate(env,monkeypatch,tmp_path):
    local,headers,body=image_request_setup(env,monkeypatch,tmp_path)
    def unexpected(*args):raise AssertionError('Upstream must not be called')
    monkeypatch.setattr(main,'generate_image',unexpected)
    assert local.post('/provider/v1/images',headers=headers,json={**body,'call_id':'old-call'}).status_code==409
    main.server.turn_modes.clear()
    assert local.post('/provider/v1/images',headers=headers,json=body).status_code==403


def test_cancel_while_waiting_for_inference_does_not_call_upstream(env,monkeypatch,tmp_path):
    local,headers,body=image_request_setup(env,monkeypatch,tmp_path)
    class Gate:
        async def __aenter__(self):main.server.cancelled_turns.add((body['thread_id'],'image-turn'))
        async def __aexit__(self,*args):pass
    monkeypatch.setattr(main,'inference_gate',Gate())
    def unexpected(*args):raise AssertionError('Cancelled image request contacted upstream')
    monkeypatch.setattr(main,'generate_image',unexpected)
    assert local.post('/provider/v1/images',headers=headers,json=body).status_code==409


def test_cancel_during_generation_does_not_save_late_results(env,monkeypatch,tmp_path):
    local,headers,body=image_request_setup(env,monkeypatch,tmp_path)
    async def generate(*args):
        main.server.cancelled_turns.add((body['thread_id'],'image-turn'))
        return {'data':b'image','mime':'image/png','suffix':'.png'}
    monkeypatch.setattr(main,'generate_image',generate)
    assert local.post('/provider/v1/images',headers=headers,json=body).status_code==409
    assert not list(main.ROOT.rglob('*.png'))
    assert not any(e['method']=='item/image/generated' for e in main.server.events)


def test_image_collision_preserves_existing_file(env,monkeypatch,tmp_path):
    from types import SimpleNamespace
    local,headers,body=image_request_setup(env,monkeypatch,tmp_path)
    target=main.ROOT/'output/imagegen/fixed.png';target.parent.mkdir(parents=True);target.write_bytes(b'user-file')
    monkeypatch.setattr(main.uuid,'uuid4',lambda:SimpleNamespace(hex='fixed'))
    async def generate(*args):return {'data':b'new','mime':'image/png','suffix':'.png'}
    monkeypatch.setattr(main,'generate_image',generate)
    assert local.post('/provider/v1/images',headers=headers,json=body).status_code==502
    assert target.read_bytes()==b'user-file'


def test_image_commit_failure_cleans_only_new_files(env,monkeypatch,tmp_path):
    import sqlite3
    local,headers,body=image_request_setup(env,monkeypatch,tmp_path)
    async def generate(*args):return {'data':b'new','mime':'image/png','suffix':'.png'}
    monkeypatch.setattr(main,'generate_image',generate)
    def fail(*args):raise sqlite3.OperationalError('disk failure')
    monkeypatch.setattr(main,'audit',fail)
    assert local.post('/provider/v1/images',headers=headers,json=body).status_code==502
    assert not list(main.ROOT.rglob('*.png')) and not list((main.STATE/'images').rglob('*.json'))


def test_stopped_image_releases_local_slot_before_provider_returns(env,monkeypatch,tmp_path):
    import asyncio
    from starlette.requests import Request
    local,headers,body=image_request_setup(env,monkeypatch,tmp_path)
    request=Request({'type':'http','method':'POST','path':'/provider/v1/images','client':('127.0.0.1',12345),
                     'headers':[(b'authorization',b'Bearer local-image-test')]})
    async def exercise():
        started=asyncio.Event();finish=asyncio.Event();closed=asyncio.Event()
        async def generate(*args):
            started.set()
            try:await finish.wait()
            finally:closed.set()
            return {'data':b'new','mime':'image/png','suffix':'.png'}
        monkeypatch.setattr(main,'generate_image',generate)
        monkeypatch.setattr(main,'inference_gate',asyncio.Semaphore(1))
        task=asyncio.create_task(main.provider_images(request,main.ImageRequest(**body)))
        try:
            await asyncio.wait_for(started.wait(),1)
            main.server.cancelled_turns.add((body['thread_id'],'image-turn'))
            with pytest.raises(main.HTTPException) as error:await asyncio.wait_for(task,1)
            assert error.value.status_code==409 and not finish.is_set() and closed.is_set()
            await asyncio.wait_for(main.inference_gate.acquire(),0.1)
            main.inference_gate.release()
        finally:finish.set()
    asyncio.run(exercise())
    assert not list(main.ROOT.rglob('*.png'))
