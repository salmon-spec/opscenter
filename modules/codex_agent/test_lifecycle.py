import copy
import json
from pathlib import Path

import pytest

from test_main import env,main


@pytest.fixture
def native(env,monkeypatch):
    client,token,_=env;histories={};calls=[];counter=0
    def rpc(method,params=None,**kwargs):
        nonlocal counter
        params=params or {};calls.append((method,copy.deepcopy(params)))
        if method=='thread/start':
            counter+=1;ident='chat-'+str(counter)
            histories[ident]={'id':ident,'turns':[]}
            return {'thread':copy.deepcopy(histories[ident])}
        if method=='thread/read':return {'thread':copy.deepcopy(histories[params['threadId']])}
        if method=='thread/goal/get':return {'goal':None}
        if method=='thread/fork':
            counter+=1;ident='chat-'+str(counter);value=copy.deepcopy(histories[params['threadId']]);value['id']=ident
            if 'lastTurnId' in params:
                end=next(i for i,t in enumerate(value['turns']) if t['id']==params['lastTurnId'])
                value['turns']=value['turns'][:end+1]
            histories[ident]=value;return {'thread':copy.deepcopy(value)}
        if method=='thread/delete':histories.pop(params['threadId'],None)
        if method=='turn/start':return {'turn':{'id':'new-turn','status':'inProgress','items':[]}}
        return {}
    monkeypatch.setattr(main.server,'request',rpc)
    return client,token,histories,calls


def project(native,name='source'):
    client,token,_,_=native
    result=client.post('/api/v1/workspaces',headers=token(),json={'name':name,'title':'项目 '+name})
    assert result.status_code==200
    return result.json()['id']


def chat(native,workspace='default',title='对话'):
    client,token,histories,_=native
    result=client.post('/api/v1/threads',headers=token(),params={'workspace':workspace},json={'title':title})
    assert result.status_code==200
    ident=result.json()['id']
    histories[ident]['turns']=[{'id':'turn-1','status':'completed','items':[
        {'id':'user-1','type':'userMessage','content':[{'type':'text','text':'公开问题'}]},
        {'id':'reason-1','type':'reasoning','summary':['private reasoning']},
        {'id':'plan-1','type':'plan','text':'公开执行建议'},
        {'id':'answer-1','type':'agentMessage','phase':'final_answer','text':'公开答复'}]},
        {'id':'turn-2','status':'completed','items':[]}]
    return ident


def test_project_archive_trash_restore_and_default_protection(native):
    client,token,_,_=native;ident=project(native)
    auto=client.post('/api/v1/workspaces',headers=token(),json={'title':'自动项目'})
    assert auto.status_code==200 and auto.json()['id'].startswith('project-')
    assert client.post('/api/v1/workspaces/default/edit',headers=token(),json={'title':'默认项目改名'}).status_code==200
    for suffix,body in [('edit',{'archived':True}),('delete',{}),('copy',{})]:
        assert client.post('/api/v1/workspaces/default/'+suffix,headers=token(),json=body).status_code==409
    assert client.post('/api/v1/workspaces/'+ident+'/edit',headers=token(),json={'archived':True}).status_code==200
    assert client.post('/api/v1/threads',headers=token(),params={'workspace':ident},json={'title':'blocked'}).status_code==409
    assert ident not in {r['id'] for r in client.get('/api/v1/workspaces',headers=token()).json()['items']}
    assert [r['id'] for r in client.get('/api/v1/workspaces',headers=token(),params={'view':'archived'}).json()['items']]==[ident]
    assert client.post('/api/v1/workspaces/'+ident+'/delete',headers=token(),json={}).json()['recoverable']
    assert client.get('/api/v1/workspaces',headers=token(),params={'view':'deleted'}).json()['items'][0]['id']==ident
    restored=client.post('/api/v1/workspaces/'+ident+'/restore',headers=token(),json={}).json()
    assert restored['archived'] and not restored['deleted']
    assert client.post('/api/v1/workspaces/'+ident+'/edit',headers=token(),json={'archived':False}).status_code==200
    assert client.get('/api/v1/workspaces',headers=token(),params={'view':'all'}).status_code==200


def test_chat_archive_delete_restore_read_export_and_full_copy(native):
    client,token,histories,_=native;ident=chat(native);before=copy.deepcopy(histories[ident])
    assert client.post('/api/v1/threads/'+ident+'/edit',headers=token(),json={'archived':True,'title':'保留名称'}).status_code==200
    assert client.get('/api/v1/threads',headers=token(),params={'archived':True}).json()['items'][0]['id']==ident
    assert client.post('/api/v1/threads/'+ident+'/prompt',headers=token(),json={'text':'blocked','model':'test'}).status_code==409
    exported=client.get('/api/v1/threads/'+ident+'/export',headers=token())
    assert exported.status_code==200 and 'attachment;' in exported.headers['content-disposition']
    assert '公开问题' in exported.text and '公开答复' in exported.text and 'private reasoning' not in exported.text
    assert '公开执行建议' in exported.text
    copied=client.post('/api/v1/threads/'+ident+'/copy',headers=token(),json={})
    assert copied.status_code==200 and copied.json()['id']!=ident
    assert histories[ident]==before and len(histories[copied.json()['id']]['turns'])==2
    assert client.post('/api/v1/threads/'+ident+'/delete',headers=token(),json={}).json()['recoverable']
    assert client.get('/api/v1/threads/'+ident,headers=token()).status_code==200
    assert client.get('/api/v1/threads',headers=token(),params={'deleted':True}).json()['items'][0]['id']==ident
    assert client.post('/api/v1/threads/'+ident+'/copy',headers=token(),json={}).status_code==409
    restored=client.post('/api/v1/threads/'+ident+'/restore',headers=token(),json={}).json()
    assert restored['archived'] and not restored['deleted']


def test_fork_copies_only_retained_public_metadata_and_keeps_source(native):
    client,token,histories,_=native;ident=chat(native);before=copy.deepcopy(histories[ident])
    image=main.ROOT/'image.png';image.write_bytes(b'\x89PNG\r\n\x1a\ndata')
    image_dir=main.STATE/'images'/ident;image_dir.mkdir(parents=True)
    progress=main.STATE/'progress'/ident;progress.mkdir(parents=True)
    for number in [1,2]:
        (image_dir/(str(number)+'.json')).write_text(json.dumps({'id':str(number),'turn_id':'turn-'+str(number),'path':str(image),'created_at':str(number)}))
        (progress/('turn-'+str(number)+'.json')).write_text(json.dumps({'plan':[{'step':'step '+str(number),'status':'completed'}],'private':'do not copy'}))
    fork=client.post('/api/v1/threads/'+ident+'/fork',headers=token(),json={'title':'第一轮分支','turn_id':'turn-1'})
    assert fork.status_code==200
    created=fork.json()['id']
    assert len(histories[created]['turns'])==1 and histories[ident]==before
    assert [p.name for p in (main.STATE/'images'/created).iterdir()]==['1.json']
    plan=json.loads((main.STATE/'progress'/created/'turn-1.json').read_text())
    assert plan['plan'][0]['step']=='step 1' and 'private' not in plan
    assert client.post('/api/v1/threads/'+ident+'/fork',headers=token(),json={'turn_id':'foreign-turn'}).status_code in {404,422}


def test_active_chat_blocks_all_related_lifecycle_mutations(native):
    client,token,_,_=native;workspace=project(native);ident=chat(native,workspace)
    main.server.active_turns[ident]='running'
    for path,body in [('/threads/'+ident+'/edit',{'title':'rename'}),('/threads/'+ident+'/delete',{}),
                      ('/threads/'+ident+'/restore',{}),('/threads/'+ident+'/copy',{}),('/threads/'+ident+'/fork',{}),
                      ('/workspaces/'+workspace+'/edit',{'archived':True}),('/workspaces/'+workspace+'/delete',{}),
                      ('/workspaces/'+workspace+'/restore',{}),('/workspaces/'+workspace+'/copy',{})]:
        assert client.post('/api/v1'+path,headers=token(),params={'workspace':workspace},json=body).status_code==409,path
    other=chat(native,workspace)
    assert client.post('/api/v1/threads/'+other+'/copy',headers=token(),params={'workspace':workspace},json={}).status_code==409


@pytest.mark.parametrize('include_files',[True,False])
def test_project_copy_maps_images_and_keeps_independent_files(native,include_files):
    client,token,histories,calls=native;workspace=project(native);ident=chat(native,workspace)
    source=main.ROOT/'projects'/workspace
    image=source/'output'/'image.png';image.parent.mkdir(parents=True);image.write_bytes(b'\x89PNG\r\n\x1a\nimage-data')
    other=source/'source.txt';other.write_text('original')
    histories[ident]['turns'][0]['items'].append({'id':'view','type':'imageView','path':str(image)})
    histories[ident]['turns'][0]['items'].append({'id':'linked','type':'agentMessage','text':
        '![图]('+str(image)+')  `'+str(image)+'` 历史脚本路径 `'+str(source/'source.txt')+'`'})
    records=main.STATE/'images'/ident;records.mkdir(parents=True)
    original={'id':'image','turn_id':'turn-1','path':str(image),'created_at':'2026-11-09'}
    (records/'image.json').write_text(json.dumps(original))
    before=copy.deepcopy(histories[ident])
    result=client.post('/api/v1/workspaces/'+workspace+'/copy',headers=token(),json={'include_files':include_files,'include_threads':True})
    assert result.status_code==200,result.text
    copied=result.json();target=main.ROOT/'projects'/copied['id']
    assert copied['copied_threads']==1 and copied['image_assets_copied']==(0 if include_files else 1)
    assert (target/'output'/'image.png').read_bytes()==image.read_bytes()
    assert (target/'source.txt').exists()==include_files
    chats=client.get('/api/v1/threads',headers=token(),params={'workspace':copied['id']}).json()['items']
    new_id=chats[0]['id'];history=client.get('/api/v1/threads/'+new_id,headers=token(),params={'workspace':copied['id']}).json()
    viewed=next(i for i in history['thread']['turns'][0]['items'] if i['type']=='imageView')
    assert Path(viewed['path']).is_relative_to(target)
    assert Path(history['thread']['turns'][0]['images'][0]['path']).is_relative_to(target)
    rendered=next(i['text'] for i in history['thread']['turns'][0]['items'] if i['id']=='linked')
    assert ']('+str(target/'output'/'image.png')+')' in rendered
    assert '`'+str(target/'output'/'image.png')+'`' in rendered
    assert '`'+str(source/'source.txt')+'`' in rendered
    assert histories[ident]==before and json.loads((records/'image.json').read_text())==original
    (target/'output'/'image.png').write_bytes(b'changed')
    assert image.read_bytes().startswith(b'\x89PNG') and other.read_text()=='original'
    main.server.loaded_threads.discard(new_id)
    assert client.post('/api/v1/threads/'+new_id+'/prompt',headers=token(),params={'workspace':copied['id']},json={'text':'test','model':'test'}).status_code==200
    resume=next(params for method,params in reversed(calls) if method=='thread/resume')
    assert str(target) in resume['developerInstructions'] and 'do not modify the source project' in resume['developerInstructions']


def test_project_copy_failure_rolls_back_new_native_files_and_registration(native,monkeypatch):
    client,token,histories,calls=native;workspace=project(native);ident=chat(native,workspace)
    source=main.ROOT/'projects'/workspace;(source/'keep.txt').write_text('keep')
    before=copy.deepcopy(histories);before_projects={p.name for p in source.parent.iterdir()}
    def fail(*args,**kwargs):raise RuntimeError('synthetic metadata failure')
    monkeypatch.setattr(main,'copy_public_history',fail)
    with pytest.raises(RuntimeError,match='synthetic metadata failure'):
        client.post('/api/v1/workspaces/'+workspace+'/copy',headers=token(),json={})
    assert histories==before and (source/'keep.txt').read_text()=='keep'
    assert {p.name for p in source.parent.iterdir()}==before_projects
    assert any(method=='thread/delete' and params['threadId']!=ident for method,params in calls)
    assert {r['id'] for r in client.get('/api/v1/workspaces',headers=token(),params={'view':'all'}).json()['items']}=={'default',workspace}


def test_project_copy_size_limit_and_special_file_boundary(native,monkeypatch):
    import lifecycle
    client,token,_,_=native;workspace=project(native);source=main.ROOT/'projects'/workspace;source.mkdir(parents=True)
    (source/'large.txt').write_text('too large');monkeypatch.setattr(lifecycle,'MAX_BYTES',1)
    result=client.post('/api/v1/workspaces/'+workspace+'/copy',headers=token(),json={'include_files':True,'include_threads':False})
    assert result.status_code==413 and [p.name for p in source.parent.iterdir()]==[workspace]
    assert client.post('/api/v1/workspaces/'+workspace+'/copy',headers=token(),json={'include_files':False,'include_threads':False}).status_code==422
    assert client.post('/api/v1/workspaces',headers=token(),json={'name':'../escape','title':'bad'}).status_code==422


def test_project_copy_rejects_symlinks_without_touching_source(native,tmp_path):
    client,token,_,_=native;workspace=project(native);source=main.ROOT/'projects'/workspace;source.mkdir(parents=True)
    outside=tmp_path/'outside.txt';outside.write_text('private outside')
    try:(source/'escape.txt').symlink_to(outside)
    except OSError:pytest.skip('Symlink creation unavailable')
    result=client.post('/api/v1/workspaces/'+workspace+'/copy',headers=token(),json={'include_threads':False})
    assert result.status_code==422 and outside.read_text()=='private outside'
    assert [p.name for p in source.parent.iterdir()]==[workspace]


def test_display_names_reject_whitespace_without_trimming_prompt_or_file_content(native):
    client,token,_,calls=native;workspace=project(native);ident=chat(native,workspace)
    for path,body in [('/workspaces',{'title':'  '}),('/workspaces/'+workspace+'/edit',{'title':'\t '}),
                      ('/workspaces/'+workspace+'/copy',{'title':'  '}),('/threads',{'title':'  '}),
                      ('/threads/'+ident+'/edit',{'title':'\n '}),('/threads/'+ident+'/copy',{'title':'  '}),
                      ('/threads/'+ident+'/fork',{'title':'  '})]:
        assert client.post('/api/v1'+path,headers=token(),params={'workspace':workspace},json=body).status_code==422
    code='\n  print("keep indentation")\n\n'
    assert client.post('/api/v1/threads/'+ident+'/prompt',headers=token(),params={'workspace':workspace},json={'text':code,'model':'test'}).status_code==200
    params=next(p for method,p in reversed(calls) if method=='turn/start')
    assert params['input'][0]['text']==code
    assert client.post('/api/v1/files/write',headers=token(),params={'workspace':workspace},json={'path':'code.py','content':code}).status_code==200
    assert (main.ROOT/'projects'/workspace/'code.py').read_text()==code


@pytest.mark.parametrize('goal_response',[{'goal':{'status':'active'}},{'goal':{'status':'paused'}},
    {'goal':{'status':'budget_limited'}},{},None])
def test_unfinished_or_unknown_goals_cannot_be_hidden_or_copied(native,monkeypatch,goal_response):
    from fastapi import HTTPException
    client,token,_,calls=native;workspace=project(native);ident=chat(native,workspace)
    original=main.server.request
    def rpc(method,params=None,**kwargs):
        if method=='thread/goal/get':
            if goal_response is None:raise HTTPException(503,'synthetic unavailable')
            return goal_response
        return original(method,params,**kwargs)
    monkeypatch.setattr(main.server,'request',rpc);calls.clear()
    for path,body in [('/threads/'+ident+'/edit',{'archived':True}),('/threads/'+ident+'/delete',{}),
                      ('/threads/'+ident+'/copy',{}),('/workspaces/'+workspace+'/edit',{'archived':True}),
                      ('/workspaces/'+workspace+'/delete',{}),('/workspaces/'+workspace+'/copy',{})]:
        result=client.post('/api/v1'+path,headers=token(),params={'workspace':workspace},json=body)
        assert result.status_code==409,path
    assert not any(method in {'thread/archive','thread/delete','thread/fork','thread/goal/clear','thread/goal/set'} for method,_ in calls)
    assert client.get('/api/v1/threads',headers=token(),params={'workspace':workspace}).json()['items'][0]['id']==ident
    assert client.post('/api/v1/threads/'+ident+'/edit',headers=token(),params={'workspace':workspace},json={'title':'仅重命名'}).status_code==200


@pytest.mark.parametrize('relative',['.git','.git/commondir','.git/worktrees/linked/gitdir'])
def test_project_copy_rejects_external_git_references(native,relative):
    client,token,_,_=native;workspace=project(native);source=main.ROOT/'projects'/workspace
    pointer=source/relative;pointer.parent.mkdir(parents=True,exist_ok=True);pointer.write_text('external-reference')
    result=client.post('/api/v1/workspaces/'+workspace+'/copy',headers=token(),json={'include_threads':False})
    assert result.status_code==422 and 'Git' in result.json()['detail']
    assert pointer.read_text()=='external-reference' and [p.name for p in source.parent.iterdir()]==[workspace]


@pytest.mark.parametrize('state',['archived','deleted'])
def test_inactive_project_rejects_files_and_terminal_before_side_effects(native,monkeypatch,state):
    client,token,_,_=native;workspace=project(native)
    params={'workspace':workspace}
    assert client.post('/api/v1/files/write',headers=token(),params=params,json={'path':'keep.txt','content':'original'}).status_code==200
    if state=='archived':
        assert client.post('/api/v1/workspaces/'+workspace+'/edit',headers=token(),json={'archived':True}).status_code==200
    else:assert client.post('/api/v1/workspaces/'+workspace+'/delete',headers=token(),json={}).status_code==200
    def forbidden(*args,**kwargs):raise AssertionError('Inactive project must never spawn a process')
    monkeypatch.setattr(main.subprocess,'Popen',forbidden)
    for path,body in [('/files/write',{'path':'keep.txt','content':'changed','overwrite':True}),
                      ('/files/write',{'path':'new/fresh.txt','content':'new'}),
                      ('/terminal',{'command':'must-not-run'})]:
        assert client.post('/api/v1'+path,headers=token(),params=params,json=body).status_code==409
    root=main.ROOT/'projects'/workspace
    assert (root/'keep.txt').read_text()=='original' and not (root/'new').exists()
    assert client.get('/api/v1/files/content',headers=token(),params={**params,'path':'keep.txt'}).json()['content']=='original'


def test_archived_chat_does_not_disable_active_project_file_tools(native,monkeypatch):
    from types import SimpleNamespace
    client,token,_,_=native;workspace=project(native);ident=chat(native,workspace)
    assert client.post('/api/v1/threads/'+ident+'/edit',headers=token(),params={'workspace':workspace},json={'archived':True}).status_code==200
    spawned=[]
    def spawn(*args,**kwargs):
        spawned.append(args[0]);return SimpleNamespace(wait=lambda timeout=None:0,returncode=0)
    monkeypatch.setattr(main.subprocess,'Popen',spawn)
    assert client.post('/api/v1/files/write',headers=token(),params={'workspace':workspace},json={'path':'allowed.txt','content':'ok'}).status_code==200
    assert client.post('/api/v1/terminal',headers=token(),params={'workspace':workspace},json={'command':'administrator-command'}).status_code==200
    assert spawned==[['bash','-lc','administrator-command']]


@pytest.mark.parametrize('operation',['write','terminal'])
def test_project_archive_waits_for_write_or_terminal_operation(native,monkeypatch,operation):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import threading
    client,token,_,_=native;workspace=project(native)
    params={'workspace':workspace};root=main.ROOT/'projects'/workspace
    assert client.post('/api/v1/files/write',headers=token(),params=params,json={'path':'keep.txt','content':'original'}).status_code==200
    started=threading.Event();release=threading.Event();archive_requested=threading.Event()
    def wait_for_release():
        started.set();assert release.wait(5),'Test operation was not released'
    if operation=='write':
        original=main.os.replace
        def replace(source,target):
            if Path(target)==root/'keep.txt':wait_for_release()
            return original(source,target)
        monkeypatch.setattr(main.os,'replace',replace)
        path='/files/write';body={'path':'keep.txt','content':'updated','overwrite':True}
    else:
        def spawn(*args,**kwargs):
            return SimpleNamespace(wait=lambda timeout=None:wait_for_release(),returncode=0)
        monkeypatch.setattr(main.subprocess,'Popen',spawn)
        path='/terminal';body={'command':'administrator-command'}
    def archive():
        archive_requested.set()
        return client.post('/api/v1/workspaces/'+workspace+'/edit',headers=token(),json={'archived':True})
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending=workers.submit(lambda:client.post('/api/v1'+path,headers=token(),params=params,json=body))
        try:
            assert started.wait(5)
            acquired=main.mutation.acquire(blocking=False)
            if acquired:main.mutation.release()
            assert not acquired,'The write/terminal must retain the lifecycle lock'
            archived=workers.submit(archive);assert archive_requested.wait(5)
            assert not archived.done() and not main.project_record(workspace)['archived']
        finally:release.set()
        assert pending.result(timeout=5).status_code==200
        assert archived.result(timeout=5).status_code==200
    assert main.project_record(workspace)['archived']
    if operation=='write':assert (root/'keep.txt').read_text()=='updated'


def test_native_mixed_content_preserves_fragments_during_read_export_and_copy(native):
    import lifecycle
    client,token,histories,_=native;workspace=project(native);ident=chat(native,workspace)
    root=main.ROOT/'projects'/workspace;image=root/'mixed.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\nimage-data')
    content=['native string fragment',{'type':'text','text':'object text'},
             {'type':'localImage','path':str(image)},{'type':'localImage','path':['not-a-path']},None,7]
    histories[ident]['turns'][0]['items'][0]['content']=copy.deepcopy(content)
    histories[ident]['turns'][0]['items'].append({'id':'bad-path-view','type':'imageView','path':{'invalid':'path'}})
    before=copy.deepcopy(histories[ident])
    with main.database() as db:lifecycle.update(db,'thread',ident,metadata={'path_map':{str(image):str(image)}})
    query={'workspace':workspace}
    read=client.get('/api/v1/threads/'+ident,headers=token(),params=query)
    assert read.status_code==200
    assert read.json()['thread']['turns'][0]['items'][0]['content']==content
    exported=client.get('/api/v1/threads/'+ident+'/export',headers=token(),params=query)
    assert exported.status_code==200 and 'native string fragment' in exported.text and 'object text' in exported.text
    copied=client.post('/api/v1/workspaces/'+workspace+'/copy',headers=token(),json={'include_files':False,'include_threads':True})
    assert copied.status_code==200,copied.text
    assert copied.json()['image_assets_copied']==1 and histories[ident]==before
    target=copied.json()['id'];new_id=client.get('/api/v1/threads',headers=token(),params={'workspace':target}).json()['items'][0]['id']
    reread=client.get('/api/v1/threads/'+new_id,headers=token(),params={'workspace':target})
    assert reread.status_code==200
    mapped=reread.json()['thread']['turns'][0]['items'][0]['content']
    assert mapped[0:2]==content[0:2] and mapped[3:]==content[3:]
    assert Path(mapped[2]['path']).is_relative_to(main.ROOT/'projects'/target)


def test_native_archived_chat_can_recycle_restore_and_copy_project_without_changing_source(native,monkeypatch,tmp_path):
    from fastapi import HTTPException
    import sqlite3
    client,token,histories,calls=native;workspace=project(native);ident=chat(native,workspace)
    home=tmp_path/'native-home';home.mkdir();monkeypatch.setenv('CODEX_HOME',str(home))
    with sqlite3.connect(home/'state_5.sqlite') as db:
        db.execute('create table threads(id text primary key,archived integer not null)')
        db.execute('insert into threads values(?,0)',(ident,))
    with sqlite3.connect(home/'goals_1.sqlite') as db:
        db.execute('create table thread_goals(thread_id text primary key,goal_id text,objective text,status text,token_budget integer,tokens_used integer,time_used_seconds integer,created_at_ms integer,updated_at_ms integer)')
    original=main.server.request
    def rpc(method,params=None,**kwargs):
        params=params or {};target=params.get('threadId')
        with sqlite3.connect(home/'state_5.sqlite') as db:row=db.execute('select archived from threads where id=?',(target,)).fetchone()
        if method=='thread/goal/get' and row and row[0]:raise HTTPException(502,'thread not found: '+target)
        result=original(method,params,**kwargs)
        if method=='thread/fork':
            with sqlite3.connect(home/'state_5.sqlite') as db:db.execute('insert into threads values(?,0)',(result['thread']['id'],))
        if method in {'thread/archive','thread/unarchive'}:
            with sqlite3.connect(home/'state_5.sqlite') as db:db.execute('update threads set archived=? where id=?',(int(method=='thread/archive'),target))
        return result
    monkeypatch.setattr(main.server,'request',rpc)
    query={'workspace':workspace};root=main.ROOT/'projects'/workspace
    (root/'keep.txt').write_text('source file preserved');before=copy.deepcopy(histories[ident])
    assert client.post('/api/v1/threads/'+ident+'/edit',headers=token(),params=query,json={'archived':True}).status_code==200
    assert client.post('/api/v1/threads/'+ident+'/delete',headers=token(),params=query,json={}).status_code==200
    restored=client.post('/api/v1/threads/'+ident+'/restore',headers=token(),params=query,json={})
    assert restored.status_code==200 and restored.json()['archived']
    copied=client.post('/api/v1/workspaces/'+workspace+'/copy',headers=token(),json={})
    assert copied.status_code==200,copied.text
    assert copied.json()['copied_threads']==1
    assert histories[ident]==before and (root/'keep.txt').read_text()=='source file preserved'
    with sqlite3.connect(home/'state_5.sqlite') as db:assert db.execute('select archived from threads where id=?',(ident,)).fetchone()==(1,)
    assert client.get('/api/v1/threads/'+ident,headers=token(),params=query).json()['metadata']['archived']
