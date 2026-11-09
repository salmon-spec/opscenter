import copy
import threading

from fastapi import HTTPException
import pytest

from conversation_ops import cleanup_conversation,fork_conversation,require_no_running_goal


class Native:
    def __init__(self):
        self.lock=threading.RLock();self.active_turns={};self.calls=[];self.fail=None
        self.goal_response={'goal':None}
        self.history={'id':'source','turns':[{'id':'turn-a','status':'completed'},
            {'id':'turn-b','status':'completed'},{'id':'turn-c','status':'completed'}]}
    def request(self,method,params):
        self.calls.append((method,copy.deepcopy(params)))
        if method==self.fail or isinstance(self.fail,set) and method in self.fail:raise RuntimeError('synthetic private native error')
        if method=='thread/read':return {'thread':copy.deepcopy(self.history)}
        if method=='thread/goal/get':return copy.deepcopy(self.goal_response)
        if method=='thread/fork':return {'thread':{'id':'new-copy'}}
        return {}


def test_full_copy_uses_native_history_without_touching_files_or_source(tmp_path):
    native=Native();before=copy.deepcopy(native.history)
    (tmp_path/'user.txt').write_text('user file',encoding='utf8')
    ident=fork_conversation(native,'source','副本',tmp_path,developer_instructions='installed image tool')
    assert ident=='new-copy'
    params=next(params for method,params in native.calls if method=='thread/fork')
    assert params=={'threadId':'source','cwd':str(tmp_path.resolve()),'excludeTurns':True,
        'approvalPolicy':'on-request','sandbox':'workspace-write','developerInstructions':'installed image tool'}
    assert native.calls[-1]==('thread/name/set',{'threadId':'new-copy','name':'副本'})
    assert native.history==before and (tmp_path/'user.txt').read_text(encoding='utf8')=='user file'
    assert {method for method,_ in native.calls}=={'thread/read','thread/goal/get','thread/fork','thread/name/set'}


def test_branch_uses_inclusive_last_turn_without_rollback(tmp_path):
    native=Native()
    assert fork_conversation(native,'source','分支',tmp_path,turn_id='turn-b')=='new-copy'
    params=next(params for method,params in native.calls if method=='thread/fork')
    assert params['lastTurnId']=='turn-b'
    assert not any(method in {'thread/rollback','thread/revert','turn/start'} for method,_ in native.calls)


def test_missing_turn_is_rejected_before_creation(tmp_path):
    native=Native()
    with pytest.raises(HTTPException) as error:fork_conversation(native,'source','copy',tmp_path,'another-turn')
    assert error.value.status_code==404 and [method for method,_ in native.calls]==['thread/read']


@pytest.mark.parametrize('cached',[True,False])
def test_active_source_is_protected_from_copy(tmp_path,cached):
    native=Native()
    if cached:native.active_turns['source']='turn-c'
    else:native.history['turns'][-1]['status']='inProgress'
    with pytest.raises(HTTPException) as error:fork_conversation(native,'source','copy',tmp_path)
    assert error.value.status_code==409 and not any(method=='thread/fork' for method,_ in native.calls)


def test_name_failure_deletes_only_the_new_conversation(tmp_path):
    native=Native();native.fail='thread/name/set'
    with pytest.raises(HTTPException) as error:fork_conversation(native,'source','copy',tmp_path)
    assert error.value.status_code==502 and 'private' not in error.value.detail
    assert native.calls[-1]==('thread/delete',{'threadId':'new-copy'})
    assert not any(method in {'thread/archive','thread/delete'} and params['threadId']=='source' for method,params in native.calls)


def test_invalid_fork_result_cannot_archive_the_source(tmp_path):
    native=Native();original=native.request
    def request(method,params):
        if method=='thread/fork':return {'thread':{'id':'source'}}
        return original(method,params)
    native.request=request
    with pytest.raises(HTTPException):fork_conversation(native,'source','copy',tmp_path)
    assert not any(method in {'thread/archive','thread/delete'} for method,_ in native.calls)


def test_cleanup_is_best_effort_and_bad_ids_cannot_reach_native():
    native=Native()
    assert cleanup_conversation(native,'../source') is False and native.calls==[]
    assert cleanup_conversation(native,'new-copy') is True
    native.fail='thread/delete'
    assert cleanup_conversation(native,'new-copy') is True
    assert native.calls[-1]==('thread/archive',{'threadId':'new-copy'})
    native.fail={'thread/delete','thread/archive'}
    assert cleanup_conversation(native,'new-copy') is False


def test_target_directory_and_title_fail_before_native_calls(tmp_path):
    native=Native()
    for title,target in [('',tmp_path),('copy',tmp_path/'missing')]:
        with pytest.raises(HTTPException) as error:fork_conversation(native,'source',title,target)
        assert error.value.status_code==422
    assert native.calls==[]


@pytest.mark.parametrize('goal',[None,{'status':'complete'}])
def test_absent_or_completed_goal_allows_copy_without_goal_mutation(tmp_path,goal):
    native=Native();native.goal_response={'goal':goal}
    assert fork_conversation(native,'source','copy',tmp_path)=='new-copy'
    assert native.goal_response=={'goal':goal}
    assert native.calls.index(('thread/goal/get',{'threadId':'source'}))<next(
        index for index,(method,_) in enumerate(native.calls) if method=='thread/fork')
    assert not any(method in {'thread/goal/set','thread/goal/clear','turn/start'} for method,_ in native.calls)


@pytest.mark.parametrize('status',['active','paused','blocked','usageLimited','budgetLimited','unknown'])
def test_unfinished_goal_prevents_native_copy(tmp_path,status):
    native=Native();native.goal_response={'goal':{'status':status}}
    with pytest.raises(HTTPException) as error:fork_conversation(native,'source','copy',tmp_path)
    assert error.value.status_code==409 and '持续目标' in error.value.detail
    assert [method for method,_ in native.calls]==['thread/read','thread/goal/get']


@pytest.mark.parametrize('response',[{},None,[],{'goal':{}},{'goal':'complete'}])
def test_missing_or_invalid_goal_state_fails_closed(tmp_path,response):
    native=Native();native.goal_response=response
    with pytest.raises(HTTPException) as error:fork_conversation(native,'source','copy',tmp_path)
    assert error.value.status_code==409
    assert not any(method=='thread/fork' for method,_ in native.calls)


def test_goal_read_failure_fails_closed_without_exposing_native_error(tmp_path):
    native=Native();native.fail='thread/goal/get'
    with pytest.raises(HTTPException) as error:fork_conversation(native,'source','copy',tmp_path)
    assert error.value.status_code==409 and 'private' not in error.value.detail
    assert not any(method in {'thread/fork','thread/goal/set','thread/goal/clear','turn/start'} for method,_ in native.calls)


def test_public_goal_guard_only_reads_goal_state():
    native=Native()
    assert require_no_running_goal(native,'source') is False
    assert native.calls==[('thread/goal/get',{'threadId':'source'})]
