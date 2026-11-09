"""Reversible sidebar state and bounded copies of project-owned regular files."""
import json
import os
from pathlib import Path
import stat

from fastapi import HTTPException

MAX_FILES=10000
MAX_BYTES=256*1024*1024


def initialize(db):
    db.execute("create table if not exists lifecycle(kind text,id text,archived integer not null default 0,deleted integer not null default 0,metadata text not null default '{}',primary key(kind,id))")


def state(db,kind,ident):
    row=db.execute('select archived,deleted,metadata from lifecycle where kind=? and id=?',(kind,ident)).fetchone()
    if row is None:return {'archived':False,'deleted':False,'metadata':{}}
    try:metadata=json.loads(row['metadata'])
    except (ValueError,TypeError):metadata={}
    return {'archived':bool(row['archived']),'deleted':bool(row['deleted']),
            'metadata':metadata if isinstance(metadata,dict) else {}}


def update(db,kind,ident,**values):
    current=state(db,kind,ident);current.update(values)
    db.execute('insert into lifecycle(kind,id,archived,deleted,metadata) values(?,?,?,?,?) on conflict(kind,id) do update set archived=excluded.archived,deleted=excluded.deleted,metadata=excluded.metadata',
        (kind,ident,int(current['archived']),int(current['deleted']),json.dumps(current['metadata'],ensure_ascii=False)))
    return current


def require_idle(server,idents,*,check_goals=False):
    idents=set(idents)
    with server.lock:
        if idents.intersection(server.active_turns):
            raise HTTPException(409,'相关对话仍在执行，请先停止任务后重试')
    if check_goals:
        from conversation_ops import require_no_running_goal
        for ident in idents:require_no_running_goal(server,ident)
        with server.lock:
            if idents.intersection(server.active_turns):
                raise HTTPException(409,'相关对话已开始执行，请先停止任务后重试')


def copy_regular(source,target,budget):
    """Caller owns target's new staging directory; never overwrite an existing file."""
    if source.is_symlink():raise HTTPException(422,'项目含符号链接，无法安全复制')
    fd=os.open(source,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(fd,'rb') as incoming:
        info=os.fstat(incoming.fileno())
        if not stat.S_ISREG(info.st_mode):raise HTTPException(422,'项目含特殊文件，无法复制')
        if budget['files']>=MAX_FILES or budget['bytes']+info.st_size>MAX_BYTES:
            raise HTTPException(413,'项目复制限 10000 个文件、256 MiB')
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as outgoing:
            while chunk:=incoming.read(64*1024):
                budget['bytes']+=len(chunk)
                if budget['bytes']>MAX_BYTES:raise HTTPException(413,'项目复制超过 256 MiB')
                outgoing.write(chunk)
        target.chmod(stat.S_IMODE(info.st_mode)&0o777)
        budget['files']+=1


def copy_tree(source,target,budget):
    if source.is_symlink():raise HTTPException(422,'项目目录不能是符号链接')
    if not source.exists():return
    if not source.is_dir():raise HTTPException(422,'项目目录无效')
    for folder,dirs,files in os.walk(source,followlinks=False):
        folder=Path(folder)
        for name in dirs:
            entry=folder/name
            if entry.is_symlink():raise HTTPException(422,'项目含符号链接，无法安全复制')
            if budget['files']>=MAX_FILES:raise HTTPException(413,'项目复制限 10000 个文件和目录')
            budget['files']+=1
            (target/entry.relative_to(source)).mkdir(parents=True,exist_ok=True)
        for name in files:
            entry=folder/name
            if name=='.git' or '.git' in entry.relative_to(source).parts[:-1] and name in {'commondir','gitdir'}:
                raise HTTPException(422,'该项目含外部 Git 目录引用，无法复制')
            if not entry.resolve().is_relative_to(source.resolve()):raise HTTPException(422,'文件路径超出项目')
            copy_regular(entry,target/entry.relative_to(source),budget)


def remove_created(path,parent):
    """Remove only an owned copy directory directly below the verified project root."""
    import shutil
    path=Path(path);parent=Path(parent).resolve()
    if path.parent.resolve()!=parent or path.is_symlink() or not path.resolve().is_relative_to(parent):
        raise ValueError('Refusing cleanup outside the project copy root')
    if path.exists():shutil.rmtree(path)
