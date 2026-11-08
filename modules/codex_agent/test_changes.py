import hashlib
import os
from pathlib import Path
import subprocess

from fastapi import HTTPException
import pytest

import changes


def git(root,*args):
    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True,env=env).stdout


@pytest.fixture
def repo(tmp_path):
    root=tmp_path/'工作区 with spaces';root.mkdir()
    git(root,'init','--quiet')
    git(root,'config','user.name','Test');git(root,'config','user.email','test@example.invalid')
    git(root,'config','core.autocrlf','false')
    (root/'tracked.txt').write_text('original\n',encoding='utf8')
    git(root,'add','.');git(root,'commit','--quiet','-m','initial')
    return root


def test_not_a_repository_and_exact_changed_file_membership(tmp_path,repo):
    assert changes.read_changes(tmp_path)['repository'] is False
    with pytest.raises(HTTPException) as error: changes.read_changes(tmp_path,'file.txt')
    assert error.value.status_code==409
    with pytest.raises(HTTPException) as error: changes.read_changes(repo,'tracked.txt')
    assert error.value.status_code==404


def test_staged_and_worktree_diffs_are_separate_without_index_writes(repo):
    target=repo/'tracked.txt';target.write_text('staged\n',encoding='utf8');git(repo,'add','tracked.txt')
    target.write_text('unstaged\n',encoding='utf8')
    index=repo/'.git'/'index';before=(hashlib.sha256(index.read_bytes()).hexdigest(),index.stat().st_mtime_ns)
    git(repo,'config','diff.external','this-external-command-must-never-run')
    listing=changes.read_changes(repo)
    assert listing['items']==[{'path':'tracked.txt','status':'MM'}]
    result=changes.read_changes(repo,'tracked.txt')
    assert '+unstaged' in result['diff'] and '-staged' in result['diff']
    assert '+staged' in result['staged_diff'] and '-original' in result['staged_diff']
    assert not result['untracked'] and not result['truncated']
    assert before==(hashlib.sha256(index.read_bytes()).hexdigest(),index.stat().st_mtime_ns)


def test_nul_status_handles_chinese_spaces_and_rename(repo):
    original='原始 name.txt';renamed='重命名 文件.txt'
    (repo/original).write_text('keep this text\n',encoding='utf8')
    git(repo,'add',original);git(repo,'commit','--quiet','-m','rename base');git(repo,'mv',original,renamed)
    listing=changes.read_changes(repo)
    assert listing['items']==[{'path':renamed,'old_path':original,'status':'R '}]
    result=changes.read_changes(repo,renamed)
    assert original in result['staged_diff'] and renamed in result['staged_diff']
    with pytest.raises(HTTPException) as error: changes.read_changes(repo,original)
    assert error.value.status_code==404


def test_untracked_preview_uses_literal_path_and_binary_detection(repo):
    # A leading colon is a valid Linux filename and a Git pathspec metacharacter.
    name='[literal].txt' if os.name=='nt' else ':(glob)*.txt'
    (repo/name).write_text('only requested file\n',encoding='utf8')
    (repo/'other.txt').write_text('do not include this\n',encoding='utf8')
    result=changes.read_changes(repo,name)
    assert result['untracked'] and '+only requested file' in result['diff']
    assert 'do not include this' not in result['diff'] and not result['binary']
    (repo/'binary.dat').write_bytes(b'\0\x01\xff')
    result=changes.read_changes(repo,'binary.dat')
    assert result['binary'] and result['diff']==''


@pytest.mark.parametrize('path',['../outside','/etc/passwd','C:/outside','..\\outside','tracked.txt\0'])
def test_paths_outside_workspace_are_rejected(repo,path):
    with pytest.raises(HTTPException) as error: changes.read_changes(repo,path)
    assert error.value.status_code==422


def test_symlink_cannot_expose_outside_file(repo,tmp_path):
    outside=tmp_path/'outside.txt';outside.write_text('private outside content',encoding='utf8')
    try: (repo/'escape.txt').symlink_to(outside)
    except (OSError,NotImplementedError): pytest.skip('Symlink creation unavailable on this platform')
    with pytest.raises(HTTPException) as error: changes.read_changes(repo,'escape.txt')
    assert error.value.status_code==422


def test_nested_workspace_omits_other_project_changes(repo):
    nested=repo/'project';nested.mkdir();(nested/'inside.txt').write_text('base\n',encoding='utf8')
    git(repo,'add','.');git(repo,'commit','--quiet','-m','nested')
    (nested/'inside.txt').write_text('inside update\n',encoding='utf8')
    (repo/'tracked.txt').write_text('outside update\n',encoding='utf8')
    assert changes.read_changes(nested)['items']==[{'path':'inside.txt','status':' M'}]
    result=changes.read_changes(nested,'inside.txt')
    assert '+inside update' in result['diff'] and 'outside update' not in result['diff']
    with pytest.raises(HTTPException) as error: changes.read_changes(nested,'tracked.txt')
    assert error.value.status_code==404


def test_large_output_and_file_count_are_bounded(repo,monkeypatch):
    (repo/'tracked.txt').write_text('long changed line\n'*50000,encoding='utf8')
    result=changes.read_changes(repo,'tracked.txt')
    assert result['truncated'] and len(result['diff'].encode('utf8'))<=changes.MAX_DIFF
    (repo/'large.txt').write_text('new line\n'*50000,encoding='utf8')
    result=changes.read_changes(repo,'large.txt')
    assert result['truncated'] and len(result['diff'].encode('utf8'))<=changes.MAX_DIFF
    (repo/'last.txt').write_text('last\n',encoding='utf8')
    monkeypatch.setattr(changes,'MAX_ITEMS',1)
    listing=changes.read_changes(repo)
    assert len(listing['items'])==1 and listing['truncated']
    with pytest.raises(HTTPException) as error: changes.read_changes(repo,'last.txt')
    assert error.value.status_code==413


def test_tracked_binary_change_has_no_text_preview(repo):
    (repo/'binary.dat').write_bytes(b'\0original');git(repo,'add','binary.dat');git(repo,'commit','--quiet','-m','binary')
    (repo/'binary.dat').write_bytes(b'\0updated')
    result=changes.read_changes(repo,'binary.dat')
    assert result['binary'] and 'Binary files' in result['diff']
