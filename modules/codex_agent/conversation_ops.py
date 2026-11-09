"""Native conversation copies and branches; never copy or roll back project files."""
from pathlib import Path
import re

from fastapi import HTTPException


def _valid_id(value):
    return isinstance(value,str) and re.fullmatch('[A-Za-z0-9_-]{1,100}',value) is not None


def cleanup_conversation(server,ident):
    """Best-effort delete of a new unregistered copy; archive if deletion fails."""
    if not _valid_id(ident):return False
    for method in ['thread/delete','thread/archive']:
        try:server.request(method,{'threadId':ident});return True
        except Exception:pass
    return False


def require_no_running_goal(server,ident):
    try:goal_state=server.request('thread/goal/get',{'threadId':ident})
    except Exception:
        raise HTTPException(409,'无法确认持续目标状态，操作已取消，请刷新后重试') from None
    if not isinstance(goal_state,dict) or 'goal' not in goal_state:
        raise HTTPException(409,'持续目标状态缺失，操作已取消，请刷新后重试')
    goal=goal_state['goal']
    if goal is not None and (not isinstance(goal,dict) or goal.get('status')!='complete'):
        raise HTTPException(409,'请先完成或清除持续目标，再执行此操作')


def fork_conversation(server,source_id,title,cwd,turn_id=None,developer_instructions=''):
    if not _valid_id(source_id) or turn_id is not None and not _valid_id(turn_id):
        raise HTTPException(422,'对话或轮次标识无效')
    if not isinstance(title,str) or not 0<len(title.strip())<=120:
        raise HTTPException(422,'请输入 1 至 120 字的对话名称')
    try:
        target=Path(cwd).resolve(strict=True)
        if not target.is_dir():raise ValueError()
    except (OSError,ValueError):raise HTTPException(422,'目标项目目录不存在') from None
    with server.lock:
        if source_id in server.active_turns:
            raise HTTPException(409,'请先等待源对话完成或停止，再复制或创建分支')
    result=server.request('thread/read',{'threadId':source_id,'includeTurns':True})
    source=result.get('thread',{})
    turns=source.get('turns',[])
    if source.get('id')!=source_id or not isinstance(turns,list):
        raise HTTPException(502,'无法读取完整对话历史，请刷新后重试')
    if any(turn.get('status')=='inProgress' for turn in turns):
        raise HTTPException(409,'请先等待源对话完成或停止，再复制或创建分支')
    if turn_id is not None and not any(turn.get('id')==turn_id for turn in turns):
        raise HTTPException(404,'所选轮次不属于源对话，请刷新后重试')
    require_no_running_goal(server,source_id)
    params={'threadId':source_id,'cwd':str(target),'excludeTurns':True,
            'approvalPolicy':'on-request','sandbox':'workspace-write'}
    if turn_id is not None:params['lastTurnId']=turn_id
    if developer_instructions:params['developerInstructions']=developer_instructions
    ident=None
    try:
        result=server.request('thread/fork',params)
        candidate=result.get('thread',{}).get('id')
        if not _valid_id(candidate) or candidate==source_id:
            raise HTTPException(502,'创建对话分支未返回有效的新标识')
        ident=candidate
        server.request('thread/name/set',{'threadId':ident,'name':title.strip()})
        return ident
    except Exception:
        if ident is not None:
            cleaned=cleanup_conversation(server,ident)
            message='复制对话未完成，新建副本已清理，请重试' if cleaned else '复制对话未完成，新建副本未能清理，请刷新检查'
        else:message='无法复制对话，请刷新后重试'
        raise HTTPException(502,message) from None
