from pathlib import Path
from contextlib import contextmanager
import sqlite3

from fastapi import HTTPException
import pytest

import conversation_ops


@contextmanager
def database(path):
    db=sqlite3.connect(path)
    try:
        with db:yield db
    finally:db.close()


@pytest.fixture
def native_home(tmp_path,monkeypatch):
    home=tmp_path/'native-home';home.mkdir();monkeypatch.setenv('CODEX_HOME',str(home))
    with database(home/'state_5.sqlite') as db:
        db.execute('create table threads(id text primary key,archived integer not null)')
        db.execute("insert into threads values('source',1)")
    with database(home/'goals_1.sqlite') as db:
        db.execute('create table thread_goals(thread_id text primary key,goal_id text,objective text,status text,token_budget integer,tokens_used integer,time_used_seconds integer,created_at_ms integer,updated_at_ms integer)')
    return home


class ArchivedNative:
    def __init__(self,error=None):self.calls=[];self.error=error
    def request(self,method,params):
        self.calls.append((method,params))
        raise self.error or HTTPException(502,'thread not found: '+params['threadId'])


def set_goal(home,status):
    with database(home/'goals_1.sqlite') as db:
        db.execute('insert into thread_goals values(?,?,?,?,?,?,?,?,?)',('source','goal-1','synthetic objective',status,None,0,0,1,1))


@pytest.mark.parametrize('status',[None,'complete'])
def test_archived_no_goal_or_completed_goal_allows_readonly_check(native_home,status):
    if status is not None:set_goal(native_home,status)
    before={path.name:path.read_bytes() for path in native_home.iterdir()}
    server=ArchivedNative()
    assert conversation_ops.require_no_running_goal(server,'source') is True
    assert server.calls==[('thread/goal/get',{'threadId':'source'})]
    assert {path.name:path.read_bytes() for path in native_home.iterdir()}==before


@pytest.mark.parametrize('status',['active','paused','blocked','usage_limited','budget_limited','unknown',''])
def test_archived_unfinished_or_unknown_goal_stays_blocked(native_home,status):
    set_goal(native_home,status)
    with pytest.raises(HTTPException) as error:conversation_ops.require_no_running_goal(ArchivedNative(),'source')
    assert error.value.status_code==409
    with database(native_home/'goals_1.sqlite') as db:
        assert db.execute('select status from thread_goals where thread_id=?',('source',)).fetchone()==(status,)


@pytest.mark.parametrize('missing',['state_5.sqlite','goals_1.sqlite'])
def test_archived_missing_database_fails_closed_without_creating_it(native_home,missing):
    (native_home/missing).unlink()
    with pytest.raises(HTTPException) as error:conversation_ops.require_no_running_goal(ArchivedNative(),'source')
    assert error.value.status_code==409 and not (native_home/missing).exists()


@pytest.mark.parametrize('shape',['missing_table','missing_columns','no_primary_key','composite_key','not_archived','unknown_thread','composite_state_key'])
def test_archived_state_or_goal_schema_must_be_verified(native_home,shape):
    if shape in {'missing_table','missing_columns','no_primary_key','composite_key'}:
        with database(native_home/'goals_1.sqlite') as db:
            db.execute('drop table thread_goals')
            if shape=='missing_columns':db.execute('create table thread_goals(thread_id text primary key,status text)')
            elif shape=='no_primary_key':db.execute('create table thread_goals(thread_id text,goal_id text,objective text,status text,token_budget integer,tokens_used integer,time_used_seconds integer,created_at_ms integer,updated_at_ms integer)')
            elif shape=='composite_key':db.execute('create table thread_goals(thread_id text,goal_id text,objective text,status text,token_budget integer,tokens_used integer,time_used_seconds integer,created_at_ms integer,updated_at_ms integer,primary key(thread_id,goal_id))')
    else:
        with database(native_home/'state_5.sqlite') as db:
            if shape=='composite_state_key':
                db.execute('drop table threads');db.execute('create table threads(id text,extra text,archived integer,primary key(id,extra))');db.execute("insert into threads values('source','other',1)")
            else:db.execute('update threads set archived=0' if shape=='not_archived' else 'delete from threads')
    with pytest.raises(HTTPException) as error:conversation_ops.require_no_running_goal(ArchivedNative(),'source')
    assert error.value.status_code==409


@pytest.mark.parametrize('error',[HTTPException(502,'generic failure'),HTTPException(502,'goals feature is disabled'),
    HTTPException(502,'thread not found: another-thread'),HTTPException(503,'thread not found: source')])
def test_archived_fallback_does_not_bypass_other_native_errors(native_home,error,monkeypatch):
    def forbidden(ident):raise AssertionError('Unexpected archived fallback')
    monkeypatch.setattr(conversation_ops,'archived_goal_state',forbidden)
    with pytest.raises(HTTPException) as rejected:conversation_ops.require_no_running_goal(ArchivedNative(error),'source')
    assert rejected.value.status_code==409


class ArchivedForkNative:
    def __init__(self,home,fail=None):self.home=home;self.fail=fail;self.calls=[];self.archived=True;self.children=set()
    def request(self,method,params):
        self.calls.append((method,dict(params)))
        if method==self.fail:raise RuntimeError('synthetic failure')
        if method=='thread/read':return {'thread':{'id':'source','turns':[{'id':'first','status':'completed'},{'id':'last','status':'completed'}]}}
        if method=='thread/goal/get':
            if self.archived:raise HTTPException(502,'thread not found: source')
            with database(self.home/'goals_1.sqlite') as db:row=db.execute("select status from thread_goals where thread_id='source'").fetchone()
            return {'goal':None if row is None else {'status':row[0]}}
        if method in {'thread/archive','thread/unarchive'} and params['threadId']=='source':
            self.archived=method=='thread/archive'
            with database(self.home/'state_5.sqlite') as db:db.execute("update threads set archived=? where id='source'",(int(self.archived),))
        if method=='thread/fork':self.children.add('new-copy');return {'thread':{'id':'new-copy'}}
        if method=='thread/delete':self.children.discard(params['threadId'])
        return {}


@pytest.mark.parametrize('status',[None,'complete'])
def test_archived_fork_temporarily_restores_verified_safe_source_and_rearchives(native_home,status):
    if status is not None:set_goal(native_home,status)
    server=ArchivedForkNative(native_home)
    # Native actor bookkeeping is read-only here; this fake has no active actor.
    import threading
    server.lock=threading.RLock();server.active_turns={}
    assert conversation_ops.fork_conversation(server,'source','Copy',native_home,'first')=='new-copy'
    assert server.archived and server.children=={'new-copy'}
    methods=[method for method,_ in server.calls]
    assert methods.index('thread/unarchive')<methods.index('thread/fork')<methods.index('thread/archive')
    assert not any(method in {'thread/resume','turn/start','thread/goal/set','thread/goal/clear'} for method in methods)


@pytest.mark.parametrize('failed',['thread/unarchive','thread/fork','thread/name/set'])
def test_archived_fork_failure_always_attempts_source_archive_and_cleans_child(native_home,failed):
    import threading
    server=ArchivedForkNative(native_home,failed);server.lock=threading.RLock();server.active_turns={}
    with pytest.raises(HTTPException) as error:conversation_ops.fork_conversation(server,'source','Copy',native_home)
    assert error.value.status_code==502 and server.archived and not server.children
    assert server.calls[-1]==('thread/archive',{'threadId':'source'})


def test_archived_fork_reports_restore_failure_and_removes_new_copy(native_home):
    import threading
    server=ArchivedForkNative(native_home,'thread/archive');server.lock=threading.RLock();server.active_turns={}
    with pytest.raises(HTTPException) as error:conversation_ops.fork_conversation(server,'source','Copy',native_home)
    assert error.value.status_code==502 and '归档状态恢复' in error.value.detail
    assert not server.children
