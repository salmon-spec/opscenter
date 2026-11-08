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
    for route in ['/status','/models','/threads','/events','/files','/audit','/extensions']:
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
