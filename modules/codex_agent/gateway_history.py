"""Copy bounded gateway image/plan metadata for native conversation forks."""
import json
import os
from pathlib import Path
import re
import stat

from fastapi import HTTPException

MAX_RECORDS=10000


def _id(value):
    return isinstance(value,str) and re.fullmatch('[A-Za-z0-9_-]{1,100}',value) is not None


def _directory(state,kind,ident):
    parent=state/kind;target=parent/ident
    if parent.is_symlink() or target.is_symlink() or not target.resolve().is_relative_to(state):
        raise ValueError('unsafe metadata directory')
    return target


def _read(path,limit):
    if path.is_symlink():raise ValueError('symbolic metadata file')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise ValueError('invalid metadata file')
        raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError('metadata too large')
    result=json.loads(raw)
    if not isinstance(result,dict):raise ValueError('invalid metadata object')
    return result


def _text(value,limit):
    return value.encode('utf8')[:limit].decode('utf8',errors='ignore') if isinstance(value,str) else ''


def _progress(data):
    result={}
    if 'plan' in data:
        if not isinstance(data['plan'],list):raise ValueError('invalid plan')
        result['plan']=[{'step':_text(item.get('step',''),1000),
            'status':item.get('status') if item.get('status') in {'pending','inProgress','completed'} else 'pending'}
            for item in data['plan'][:50] if isinstance(item,dict)]
    for key,limit in [('planExplanation',1000),('diff',256*1024)]:
        if key in data:result[key]=_text(data[key],limit)
    return result


def _remove(path):
    """Remove only flat regular JSON records, never arbitrary nested directories."""
    if path.is_symlink():return False
    if not path.exists():return True
    if not path.is_dir():return False
    files=list(path.iterdir())
    if any(file.is_symlink() or file.suffix!='.json' or not file.is_file() for file in files):return False
    try:
        for file in files:file.unlink()
        path.rmdir()
    except OSError:return False
    return True


def cleanup_public_history(state,ident):
    if not _id(ident):return False
    state=Path(state).resolve()
    cleaned=True
    for kind in ['images','progress']:
        try:cleaned=_remove(_directory(state,kind,ident)) and cleaned
        except (OSError,ValueError):cleaned=False
    return cleaned


def copy_public_history(state,source_id,target_id,turn_map,path_map=None):
    if not all(_id(value) for value in [source_id,target_id]) or source_id==target_id:
        raise HTTPException(422,'源对话和目标对话标识无效')
    if (not isinstance(turn_map,dict) or len(turn_map)>MAX_RECORDS
        or not all(_id(key) and _id(value) for key,value in turn_map.items())
        or len(set(turn_map.values()))!=len(turn_map)):
        raise HTTPException(422,'对话轮次映射无效')
    path_map={} if path_map is None else path_map
    if not isinstance(path_map,dict) or not all(isinstance(key,str) and isinstance(value,str)
        and Path(key).is_absolute() and Path(value).is_absolute() for key,value in path_map.items()):
        raise HTTPException(422,'图片路径映射必须使用绝对路径')
    state=Path(state).resolve();created=[]
    try:
        sources={kind:_directory(state,kind,source_id) for kind in ['images','progress']}
        targets={kind:_directory(state,kind,target_id) for kind in ['images','progress']}
        if any(path.exists() for path in targets.values()):
            raise HTTPException(409,'目标对话已有公开历史，未覆盖现有内容')
        for path in targets.values():
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            path.mkdir(mode=0o700);created.append(path)
        count=0
        for kind,source in sources.items():
            if not source.exists():continue
            for path in source.iterdir():
                if path.suffix!='.json':continue
                count+=1
                if count>MAX_RECORDS:raise HTTPException(413,'公开历史记录过多，复制已取消')
                if kind=='progress':
                    if path.stem not in turn_map:continue
                    data=_progress(_read(path,2*1024*1024));name=turn_map[path.stem]+'.json'
                else:
                    data=_read(path,16*1024)
                    if data.get('turn_id') not in turn_map:continue
                    data['turn_id']=turn_map[data['turn_id']]
                    if isinstance(data.get('path'),str) and data['path'] in path_map:data['path']=path_map[data['path']]
                    name=path.name
                fd=os.open(targets[kind]/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w',encoding='utf8') as stream:json.dump(data,stream,ensure_ascii=False)
    except Exception as error:
        cleaned=all([_remove(path) for path in reversed(created)])
        if not cleaned:raise HTTPException(502,'公开历史复制失败，新副本资源未能清理，请刷新检查') from None
        if isinstance(error,HTTPException):raise
        raise HTTPException(502,'无法复制公开历史，新建副本已清理') from None
