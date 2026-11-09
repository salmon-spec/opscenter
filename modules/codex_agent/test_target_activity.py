from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from test_lifecycle import native,project,chat
from test_main import env,main


@pytest.mark.parametrize('running',['turn','background','goal'])
def test_running_target_does_not_block_deleting_other_chats_or_projects(native,monkeypatch,running):
    client,token,_,_=native;first=project(native,'first');second=project(native,'second')
    a=chat(native,first);b=chat(native,first);c=chat(native,second)
    if running=='goal':
        original=main.server.request
        def rpc(method,params=None,**kwargs):
            if method=='thread/goal/get' and params['threadId']==a:return {'goal':{'status':'active'}}
            return original(method,params,**kwargs)
        monkeypatch.setattr(main.server,'request',rpc)
    else:
        main.server.active_turns[a]='running-turn'
        if running=='background':
            main.server.completed_turns[(a,'running-turn')]='completed'
            main.server.active_commands[(a,'running-turn')]={'live-call':{'id':'live-call','type':'commandExecution','status':'inProgress'}}
    rows=client.get('/api/v1/threads',headers=token(),params={'workspace':first}).json()['items']
    active={row['id']:row for row in rows}
    assert active[a]['active']==(running!='goal') and not active[b]['active']
    assert active[a]['background_running']==(running=='background')
    projects={row['id']:row for row in client.get('/api/v1/workspaces',headers=token()).json()['items']}
    assert projects[first]['active']==(running!='goal') and not projects[second]['active']
    events=client.get('/api/v1/events',headers=token(),params={'workspace':first}).json()['active_threads']
    assert [row['threadId'] for row in events]==([] if running=='goal' else [a])
    assert all(row['workspace']==first for row in events)
    assert client.get('/api/v1/events',headers=token(),params={'workspace':second}).json()['active_threads']==[]
    assert client.post('/api/v1/threads/'+b+'/delete',headers=token(),params={'workspace':first},json={}).status_code==200
    assert client.post('/api/v1/threads/'+c+'/delete',headers=token(),params={'workspace':second},json={}).status_code==200
    assert client.post('/api/v1/threads/'+a+'/delete',headers=token(),params={'workspace':first},json={}).status_code==409
    assert client.post('/api/v1/workspaces/'+first+'/delete',headers=token(),json={}).status_code==409
    assert client.post('/api/v1/workspaces/'+second+'/delete',headers=token(),json={}).status_code==200
    assert not client.get('/api/v1/threads/'+a,headers=token(),params={'workspace':first}).json()['metadata']['deleted']


@pytest.mark.parametrize('blocked_rpc',['thread/resume','turn/start'])
def test_slow_prompt_rpc_reserves_its_targets_without_blocking_unrelated_delete(native,monkeypatch,blocked_rpc):
    client,token,_,calls=native;first=project(native,'first');second=project(native,'second')
    a=chat(native,first);b=chat(native,first);c=chat(native,second)
    started=threading.Event();release=threading.Event();original=main.server.request
    def rpc(method,params=None,**kwargs):
        if method==blocked_rpc and params['threadId']==a:
            started.set();assert release.wait(5)
        return original(method,params,**kwargs)
    monkeypatch.setattr(main.server,'request',rpc)
    with ThreadPoolExecutor(max_workers=4) as workers:
        pending=workers.submit(lambda:client.post('/api/v1/threads/'+a+'/prompt',headers=token(),params={'workspace':first},json={'text':'test','model':'test'}))
        try:
            assert started.wait(3)
            for ident,key in [(b,first),(c,second)]:
                result=workers.submit(lambda ident=ident,key=key:client.post('/api/v1/threads/'+ident+'/delete',headers=token(),params={'workspace':key},json={}))
                assert result.result(timeout=2).status_code==200
            assert client.post('/api/v1/threads/'+a+'/delete',headers=token(),params={'workspace':first},json={}).status_code==409
            assert client.post('/api/v1/workspaces/'+first+'/delete',headers=token(),json={}).status_code==409
            assert client.post('/api/v1/workspaces/'+second+'/delete',headers=token(),json={}).status_code==200
            assert client.post('/api/v1/threads/'+a+'/prompt',headers=token(),params={'workspace':first},json={'text':'no second inference','model':'test'}).status_code==409
            assert not pending.done()
        finally:release.set()
        assert pending.result(timeout=3).status_code==200
    assert sum(method=='turn/start' for method,_ in calls)==1


@pytest.mark.parametrize('operation',['terminal','write'])
def test_slow_project_file_operation_does_not_block_other_chat_or_project_delete(native,monkeypatch,operation):
    client,token,_,_=native;first=project(native,'first');second=project(native,'second')
    a=chat(native,first);b=chat(native,first);c=chat(native,second)
    root=main.ROOT/'projects'/first;(root/'file.txt').write_text('original')
    started=threading.Event();release=threading.Event()
    def wait():started.set();assert release.wait(5)
    if operation=='terminal':
        monkeypatch.setattr(main.subprocess,'Popen',lambda *args,**kwargs:SimpleNamespace(wait=lambda timeout=None:wait(),returncode=0))
        path='/terminal';body={'command':'test-command'}
    else:
        original=main.os.replace
        def replace(source,target):
            if Path(target)==root/'file.txt':wait()
            return original(source,target)
        monkeypatch.setattr(main.os,'replace',replace)
        path='/files/write';body={'path':'file.txt','content':'updated','overwrite':True}
    with ThreadPoolExecutor(max_workers=4) as workers:
        pending=workers.submit(lambda:client.post('/api/v1'+path,headers=token(),params={'workspace':first},json=body))
        try:
            assert started.wait(3)
            rows={row['id']:row for row in client.get('/api/v1/workspaces',headers=token()).json()['items']}
            assert rows[first]['active'] and not rows[second]['active']
            for ident,key in [(b,first),(c,second)]:
                result=workers.submit(lambda ident=ident,key=key:client.post('/api/v1/threads/'+ident+'/delete',headers=token(),params={'workspace':key},json={}))
                assert result.result(timeout=2).status_code==200
            assert client.post('/api/v1/workspaces/'+first+'/delete',headers=token(),json={}).status_code==409
            assert client.post('/api/v1/workspaces/'+second+'/delete',headers=token(),json={}).status_code==200
            assert not pending.done()
        finally:release.set()
        assert pending.result(timeout=3).status_code==200
    assert not main.operation_projects and not main.operation_threads
