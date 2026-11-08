"""Bounded, read-only Git status and per-file changes for one workspace."""
import difflib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import threading
import time

from fastapi import HTTPException

MAX_STATUS = 1024 * 1024
MAX_ITEMS = 1000
MAX_DIFF = 256 * 1024


def _git(root, args, deadline, limit=MAX_DIFF, allowed=(0,)):
    # Optional locks and filesystem-monitor commands are unnecessary for previews.
    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0',GIT_TERMINAL_PROMPT='0',GIT_PAGER='cat',LC_ALL='C')
    command=['git','--no-pager','--literal-pathspecs','-C',str(root),
             '-c','core.fsmonitor=false','-c','core.untrackedCache=false',
             '-c','core.quotePath=false','-c','status.renames=true',*args]
    try: proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env)
    except FileNotFoundError: raise HTTPException(503,'Git 不可用') from None
    output=bytearray();truncated=False
    def collect():
        nonlocal truncated
        try:
            while chunk:=proc.stdout.read(64*1024):
                available=limit-len(output)
                output.extend(chunk[:available])
                if len(chunk)>available:
                    truncated=True
                    try: proc.kill()
                    except ProcessLookupError: pass
                    break
        finally: proc.stdout.close()
    reader=threading.Thread(target=collect,daemon=True);reader.start()
    try:
        proc.wait(timeout=max(.001,deadline-time.monotonic()))
        reader.join(timeout=max(.001,deadline-time.monotonic()))
        if reader.is_alive(): raise subprocess.TimeoutExpired(command,10)
    except subprocess.TimeoutExpired:
        proc.kill();proc.wait();reader.join(timeout=1)
        raise HTTPException(504,'读取 Git 变更超过 10 秒，请缩小工作区后重试') from None
    if proc.returncode not in allowed and not truncated:
        raise HTTPException(502,'无法读取 Git 变更，请检查仓库状态')
    return bytes(output),truncated,proc.returncode


def _inside(path, prefix):
    value=PurePosixPath(path)
    if value.is_absolute() or '..' in value.parts: return None
    try: relative=value.relative_to(prefix)
    except ValueError: return None
    return relative.as_posix()


def _status(root, repository, deadline):
    raw,truncated,_=_git(root,['status','--porcelain=v1','-z','--untracked-files=all',
                              '--ignore-submodules=all','--','.'],deadline,MAX_STATUS)
    fields=raw.split(b'\0')
    # Only complete NUL-terminated records are authoritative after truncation.
    fields.pop()
    items=[];index=0;prefix=PurePosixPath(root.relative_to(repository).as_posix())
    while index<len(fields):
        field=fields[index];index+=1
        if len(field)<4 or field[2:3]!=b' ': continue
        status=field[:2].decode('ascii',errors='replace')
        name=field[3:].decode('utf8',errors='replace')
        old=None
        if 'R' in status or 'C' in status:
            if index>=len(fields): break
            old=fields[index].decode('utf8',errors='replace');index+=1
        path=_inside(name,prefix)
        old_path=_inside(old,prefix) if old is not None else None
        if path is None:
            # A rename out of this workspace is a deletion from this view.
            if old_path is None: continue
            path=old_path;old_path=None;status='D '
        item={'path':path,'status':status}
        if old_path is not None: item['old_path']=old_path
        items.append(item)
        if len(items)>=MAX_ITEMS:
            truncated=truncated or index<len(fields)
            break
    return items,truncated


def _validate_path(root, path):
    if not path or '\0' in path or '\\' in path or PurePosixPath(path).is_absolute() or PureWindowsPath(path).drive:
        raise HTTPException(422,'仅允许当前工作区中的相对文件路径')
    if '..' in PurePosixPath(path).parts or not (root/path).resolve().is_relative_to(root):
        raise HTTPException(422,'文件路径超出当前工作区')


def read_changes(root, path=None):
    root=Path(root).resolve();deadline=time.monotonic()+10
    if path is not None: _validate_path(root,path)
    raw,_,code=_git(root,['rev-parse','--show-toplevel'],deadline,16*1024,allowed=(0,128))
    if code==128:
        if path is not None: raise HTTPException(409,'当前工作区不是 Git 仓库')
        return {'repository':False,'items':[],'message':'当前工作区不是 Git 仓库'}
    repository=Path(os.fsdecode(raw).rstrip('\r\n')).resolve()
    if not root.is_relative_to(repository): raise HTTPException(422,'仓库不属于当前工作区')
    items,list_truncated=_status(root,repository,deadline)
    if path is None: return {'repository':True,'items':items,'truncated':list_truncated}
    row=next((item for item in items if item['path']==path),None)
    if row is None:
        raise HTTPException(413 if list_truncated else 404,
                            '变更清单已截断，请缩小工作区后重试' if list_truncated else '文件已不在变更清单中，请刷新')
    _validate_path(root,path)
    paths=[path]
    if row.get('old_path'):
        _validate_path(root,row['old_path']);paths.insert(0,row['old_path'])
    if row['status']=='??':
        try:
            with (root/path).open('rb') as f: data=f.read(MAX_DIFF+1)
        except (OSError,IsADirectoryError): raise HTTPException(409,'文件已变化，请刷新变更清单') from None
        truncated=len(data)>MAX_DIFF;data=data[:MAX_DIFF]
        try: text=data.decode('utf8')
        except UnicodeDecodeError:
            if truncated: text=data.decode('utf8',errors='replace')
            else: return {'path':path,'diff':'','staged_diff':'','untracked':True,'binary':True,'truncated':False}
        if b'\0' in data:
            return {'path':path,'diff':'','staged_diff':'','untracked':True,'binary':True,'truncated':truncated}
        diff=''.join(difflib.unified_diff([],text.splitlines(keepends=True),fromfile='/dev/null',tofile='b/'+path))
        encoded=diff.encode('utf8');truncated=truncated or len(encoded)>MAX_DIFF
        return {'path':path,'diff':encoded[:MAX_DIFF].decode('utf8',errors='replace'),'staged_diff':'',
                'untracked':True,'binary':False,'truncated':truncated}
    options=['--no-ext-diff','--no-textconv','--no-color','--ignore-submodules=all']
    diff,cut,_=_git(root,['diff',*options,'--',*paths],deadline)
    staged,staged_cut,_=_git(root,['diff','--cached',*options,'--',*paths],deadline)
    binary=any(b'Binary files ' in data or b'GIT binary patch' in data for data in (diff,staged))
    return {'path':path,'diff':diff.decode('utf8',errors='replace'),
            'staged_diff':staged.decode('utf8',errors='replace'),'untracked':False,
            'binary':binary,'truncated':cut or staged_cut}
