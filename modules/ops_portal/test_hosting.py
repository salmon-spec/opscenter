from test_main import module
import pytest


def registry_items():
    return [{'key': key, 'name': key, 'version': '1', 'running': False,
             'entry_url': f'/modules/{key}/ui/',
             'manifest': {'api_prefixes': [f'/modules/{key}/api/v1/'], 'sandbox': ['allow-scripts', 'allow-same-origin']}}
            for key in ['automation', 'subscriptions', 'ops-portal']]


def test_registry_and_scoped_relay(module, monkeypatch):
    _, client, token = module
    from ops_portal import hosting
    monkeypatch.setattr(hosting, 'registry', lambda _: registry_items())
    calls=[]
    class Reply:
        status_code=200
        content=b'{"ok":true}'
        headers={'Content-Type':'application/json','Set-Cookie':'secret'}
    def upstream(method, base, path, credential='', **kwargs):
        calls.append((method,base,path,credential,kwargs));return Reply()
    monkeypatch.setattr(hosting, 'upstream', upstream)
    assert client.get('/session/modules').status_code == 401
    items=client.get('/session/modules',headers=token()).json()['items']
    assert len(items)==3 and 'allow-same-origin' not in items[0]['manifest']['sandbox']
    payload={'module':'automation','path':'/modules/automation/api/v1/tasks','method':'POST','body':{'x':1},'idempotency_key':'request-1'}
    assert client.post('/session/relay',json=payload).status_code==401
    response=client.post('/session/relay',json=payload,headers=token())
    assert response.json()=={'ok':True} and 'set-cookie' not in response.headers
    assert calls[-1][0:3]==('POST','http://web',payload['path'])
    assert calls[-1][3] and calls[-1][4]['headers']=={'Idempotency-Key':'request-1'}
    assert client.post('/session/relay',json={**payload,'method':'CONNECT'},headers=token()).status_code==405


@pytest.mark.parametrize('path',[
    '/api/v2/auth/login', '/modules/other/api/v1/state',
    '/modules/automation/api/v1/../internal/state',
    '/modules/automation/api/v1/%252e%252e/internal/state',
    '/modules/automation/api/v1/internal/state',
    '//evil.example/modules/automation/api/v1/state',
    'http://evil.example/modules/automation/api/v1/state',
    '/modules/automation/api/v1/state?token=x',
    '/modules/automation/api/v1/%5cstate',
])
def test_relay_rejects_escape(module, monkeypatch, path):
    _,client,token=module
    from ops_portal import hosting
    monkeypatch.setattr(hosting,'registry',lambda _:registry_items())
    monkeypatch.setattr(hosting,'upstream',lambda *a,**k:pytest.fail('unsafe path reached upstream'))
    assert client.post('/session/relay',headers=token(),json={'module':'automation','path':path}).status_code in {400,403}


def test_legacy_ui_nonce_adapter(module,monkeypatch):
    _,client,_=module
    from ops_portal import hosting
    class Reply:
        status_code=200
        text="<html><head></head><script>parent.postMessage({ type: 'ops-module-api-request', id:'1' },'*')</script></html>"
    monkeypatch.setattr(hosting,'upstream',lambda *a,**k:Reply())
    text=client.get('/host/electricity/ui/').text
    assert "nonce: window.__portalNonce" in text
    assert "e.source===parent" in text
