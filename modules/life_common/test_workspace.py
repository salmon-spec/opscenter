from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from life_common.workspace import identifier, register_workspace, resources, validate_fields


class FakeUpstream:
    name = 'vikunja'

    def __init__(self):
        self.calls = []
        self.row = {'id': 2, 'title': '原项目', 'description': '说明', 'hex_color': '2563eb', 'updated': 'revision', 'parent_project_id': 0, 'is_archived': False, 'is_favorite': False, 'background_information': {'id': 'keep'}}

    def get(self, path, **kwargs):
        self.calls.append(('GET', path))
        return self.row.copy() if path == '/projects/2' else [self.row.copy()]

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return {'id': 2}


def setup():
    app = FastAPI()
    app.state.store = SimpleNamespace(target_lock=lambda _: nullcontext(), once=lambda k,a,p,u,f:f())
    app.state.require_editor = lambda: {'username':'editor'}
    upstream = FakeUpstream()
    register_workspace(app, upstream)
    return TestClient(app), upstream


def test_update_preserves_unmanaged_fields_and_rejects_stale_write():
    c, upstream = setup()
    body={'operation':'update','id':'2','expected_revision':'old','values':{'title':'新名称'}}
    headers={'Idempotency-Key':str(uuid4())}
    assert c.post('/api/v1/workspace/projects',json=body,headers=headers).status_code==409
    body['expected_revision']='revision'
    assert c.post('/api/v1/workspace/projects',json=body,headers=headers).status_code==200
    sent=upstream.calls[-1]
    assert sent[:2]==('POST','/projects/2')
    assert sent[2]['body']['background_information']=={'id':'keep'}
    assert sent[2]['body']['title']=='新名称'


def test_scope_and_operation_allowlist_precede_upstream_call():
    c, upstream = setup()
    for url in ['/api/v1/workspace/passwords','/api/v1/workspace/comments?scope=../admin','/api/v1/workspace/comments?scope=https://evil.test']:
        assert c.get(url).status_code in {404,422}
    assert not upstream.calls
    assert c.post('/api/v1/workspace/projects',json={'operation':'execute','values':{}},headers={'Idempotency-Key':str(uuid4())}).status_code==422


def test_unknown_fields_and_nonfinite_money_are_rejected():
    budget=next(s for s in resources('wallos') if s['key']=='budget')
    for values in [{'budget':'NaN','period_budget':0,'budget_period_type':'monthly'},{'budget':1,'period_budget':0,'budget_period_type':'monthly','api_key':'overwrite'}]:
        with pytest.raises(HTTPException):
            validate_fields(budget,values)


def test_safe_output_projects_only_exposes_form_fields():
    c, upstream = setup()
    upstream.row |= {'api_key':'secret-value','password':'secret-password'}
    output=c.get('/api/v1/workspace/projects').json()
    assert 'secret-' not in str(output)
    assert output['items'][0]['revision']=='revision'


@pytest.mark.parametrize('value',['../3','0','-1','1?x=2','http://example.com'])
def test_identifier_cannot_change_upstream_path(value):
    with pytest.raises(HTTPException):identifier(value)


def test_item_and_task_fields_reject_upstream_invalid_values():
    from home_inventory.main import Item
    from life_reminders.main import Task
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Item(name='家电',entityTypeId=uuid4(),assetId='ABC-123')
    with pytest.raises(ValidationError):
        Task(title='事项',project_id=1,start_date='2026-10-27T01:00:00Z',end_date='2026-10-26T01:00:00Z')
    assert Item(name='家电',entityTypeId=uuid4(),quantity=3,assetId='000-123').update_body()['quantity']==3
    assert Item(name='耗材',entityTypeId=uuid4(),quantity=1.5).update_body()['quantity']==1.5


def test_readiness_checks_authenticated_api_and_fails_closed(monkeypatch):
    import life_common.app as factory
    store=SimpleNamespace(connect=lambda:nullcontext(SimpleNamespace(execute=lambda _:None)))
    monkeypatch.setattr(factory,'Store',lambda:store)
    def rejected(path,**kwargs):
        assert path=='/users/self'
        raise HTTPException(401,'invalid upstream key')
    app=factory.create_app('home-inventory',SimpleNamespace(name='homebox',get=rejected))
    assert TestClient(app).get('/health/ready').status_code==503


def test_deployment_only_exposes_authenticated_adapter_service():
    import runpy
    from pathlib import Path
    manifest=runpy.run_path(str(Path(__file__).resolve().parents[2]/'deploy/k8s/life_modules.py'))['manifests']('12345678')['items']
    for obj in manifest:
        if obj['kind']=='Deployment':
            assert len(obj['spec']['template']['spec']['containers'])==2
        if obj['kind']=='Service':
            assert obj['spec'].get('type')!='NodePort'


def test_homebox_maintenance_uses_its_string_money_contract():
    app=FastAPI()
    app.state.store=SimpleNamespace(target_lock=lambda _:nullcontext(),once=lambda k,a,p,u,f:f())
    app.state.require_editor=lambda:{'username':'editor'}
    upstream=FakeUpstream();upstream.name='homebox'
    upstream.row={'id':str(uuid4()),'name':'维护','cost':'5.00','description':'','scheduledDate':'0001-01-01T00:00:00Z','completedDate':'2026-10-26T00:00:00Z','updatedAt':'revision'}
    register_workspace(app,upstream)
    r=TestClient(app).post('/api/v1/workspace/maintenance',json={'operation':'update','id':upstream.row['id'],'expected_revision':'revision','values':{'name':'维护','cost':7.25}},headers={'Idempotency-Key':str(uuid4())})
    assert r.status_code==200
    assert upstream.calls[-1][2]['body']['cost']=='7.25'


def test_username_cannot_be_a_parent_path_segment():
    for value in ['.','..','../admin','name%2fadmin']:
        with pytest.raises(HTTPException):identifier(value,'username')
