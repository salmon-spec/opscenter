from pathlib import Path

import pytest
from fastapi import HTTPException

from .parameters import definitions, read_parameters, with_parameters, resolve_parameters
from .test_scripts import repository
from .test_main import client


SCHEMA = [{'name': 'server_id', 'label':'目标主机', 'type':'host', 'required':True},
          {'name':'retries', 'label':'重试次数', 'type':'number', 'default':2},
          {'name':'enabled', 'label':'启用', 'type':'boolean', 'default':False},
          {'name':'mode', 'label':'模式', 'type':'choice', 'default':'read', 'options':['read','write']}]


def test_parameter_header_preserves_script_body_and_defaults():
    content='#!/bin/sh\necho "keep whitespace"\n\n'
    updated=with_parameters(content, SCHEMA)
    assert updated.startswith('#!/bin/sh\n# opscenter-parameters: ')
    schema=read_parameters(updated)
    assert with_parameters(updated,schema)==updated
    assert updated.endswith('echo "keep whitespace"\n\n')
    values=resolve_parameters(schema, {'server_id':'00000000-0000-0000-0000-000000000001'})
    assert values['retries']==2 and values['enabled'] is False and values['mode']=='read'


@pytest.mark.parametrize('value', [SCHEMA+[SCHEMA[0]], [{'name':'bad-key','label':'bad'}],
                                  [{'name':'mode','label':'模式','type':'choice'}],
                                  [{'name':'count','label':'数量','type':'number','default':'text'}]])
def test_invalid_definitions_are_rejected(value):
    with pytest.raises(HTTPException): definitions(value)


def test_missing_unknown_and_invalid_run_values_fail():
    schema=definitions(SCHEMA)
    for supplied in ({}, {'server_id':'bad'}, {'server_id':'00000000-0000-0000-0000-000000000001','retries':True}, {'other':'x'}):
        with pytest.raises(HTTPException): resolve_parameters(schema,supplied)


def test_script_and_parameters_commit_together_with_conflict_detection(tmp_path):
    scripts=repository(tmp_path)
    (scripts.root/'worker.py').write_text('print("before")\n')
    scripts.git('add','worker.py');scripts.git('-c','user.name=Test','-c','user.email=test@localhost','commit','-m','worker')
    original=scripts.read('check.yml');worker=scripts.read('worker.py')
    result=scripts.save('worker.py','print("after")\n',worker['revision'],'test',('check.yml',SCHEMA,original['revision']))
    assert scripts.read('worker.py')['content']=='print("after")\n'
    assert read_parameters(scripts.read('check.yml')['content'])[0]['name']=='server_id'
    assert set(scripts.git('show','--pretty=','--name-only',result['commit']).splitlines())=={'check.yml','worker.py'}
    with pytest.raises(HTTPException) as error:
        scripts.save('worker.py','print("lost")\n',result['revision'],'test',('check.yml',SCHEMA,original['revision']))
    assert error.value.status_code==409
    assert scripts.read('worker.py')['content']=='print("after")\n'


def test_atomic_save_restores_both_files_when_git_commit_fails(tmp_path,monkeypatch):
    scripts=repository(tmp_path);(scripts.root/'worker.py').write_text('before\n')
    scripts.git('add','worker.py');scripts.git('-c','user.name=Test','-c','user.email=test@localhost','commit','-m','worker')
    primary=scripts.read('check.yml');worker=scripts.read('worker.py');git=scripts.git
    def failing(*args):
        if 'commit' in args: raise HTTPException(503,'test commit failure')
        return git(*args)
    monkeypatch.setattr(scripts,'git',failing)
    with pytest.raises(HTTPException): scripts.save('worker.py','after\n',worker['revision'],'test',('check.yml',SCHEMA,primary['revision']))
    assert scripts.read('worker.py')==worker and scripts.read('check.yml')==primary
    assert not git('status','--porcelain')


def test_parameter_editor_api_persists_defaults_used_by_run(client,tmp_path,monkeypatch):
    scripts=repository(tmp_path)
    monkeypatch.setenv('AUTOMATION_SCRIPTS_DIR',str(scripts.root));monkeypatch.setenv('AUTOMATION_SCRIPTS_REPOSITORY_ID','1')
    client.app.state.store.save('templates',{'name':'test','playbook':'check.yml','repository_id':1,'app':'ansible'},1)
    before=client.get('/api/v1/templates/1/script').json()
    body={k:before[k] for k in ['path','content','revision','parameters_revision']};body['parameters']=[{'name':'greeting','label':'内容','default':'saved default'}]
    response=client.post('/api/v1/templates/1/script',json=body);assert response.status_code==200
    assert client.get('/api/v1/templates/1/parameters').json()['parameters'][0]['default']=='saved default'
    task=client.post('/api/v1/tasks',json={'template_id':1}).json()
    assert task['parameters']=={'greeting':'saved default'}
    assert client.post('/api/v1/tasks',json={'template_id':1,'parameters':{'unknown':'x'}}).status_code==422


def test_download_is_plain_text_and_authenticated(client):
    client.app.state.store.save('tasks',{'template_id':1,'status':'success'},1)
    client.app.state.store.save('output',{'lines':['\x1b[32mhello\x1b[0m','world']},1)
    response=client.get('/api/v1/tasks/1/output.txt')
    assert response.status_code==200 and response.text=='hello\nworld'
    assert 'attachment' in response.headers['content-disposition']
    client.headers.pop('Authorization')
    assert client.get('/api/v1/tasks/1/output.txt').status_code==401
