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
    assert len(items)==3+len(hosting.NATIVE) and 'allow-same-origin' not in items[0]['manifest']['sandbox']
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


@pytest.mark.parametrize('key,path,method,allowed',[
 ('core-hosts','/modules/host-inventory/api/v1/hosts','POST',True),
 ('core-hosts','/api/v2/terminal/sessions','POST',True),
 ('core-kubernetes','/api/v2/clusters/c1/pods','GET',True),
 ('core-screen','/api/v2/screen-boards/1/publish','POST',True),
 ('core-screen','/api/v2/servers/host-1/metrics/timeseries','GET',True),
 ('core-screen','/api/v2/servers/host-1/metrics/timeseries','POST',False),
 ('core-services','/api/v2/services/all','GET',True),
 ('core-services','/api/v2/services/all','POST',False),
 ('core-screen','/modules/automation/api/v1/summary','GET',True),
 ('core-screen','/modules/automation/api/v1/tasks','POST',False),
 ('core-logs','/api/v2/servers/1','DELETE',False),
 ('core-hosts','/api/v2/auth/me','GET',False),
 ('core-hosts','/api/v2/servers/1/internal','GET',False),
 ('core-kubernetes','/api/v2/clusters-other','POST',False),
])
def test_native_permissions(module,monkeypatch,key,path,method,allowed):
    _,client,token=module
    from ops_portal import hosting
    class Reply:
        status_code=200
        content=b'{"ok":true}'
        headers={'Content-Type':'application/json'}
    monkeypatch.setattr(hosting,'upstream',lambda *a,**k:Reply())
    response=client.post('/session/relay',headers=token(),json={'module':key,'path':path,'method':method})
    assert response.status_code==(200 if allowed else 403)


def test_socket_tickets_are_scoped_and_single_use(module):
    _,client,token=module
    payload={'module':'core-terminal','path':'/ws/terminal/session-1'}
    assert client.post('/session/socket-ticket',json=payload).status_code==401
    assert client.post('/session/socket-ticket',json={**payload,'module':'core-logs'},headers=token()).status_code==403
    assert client.post('/session/socket-ticket',json={**payload,'path':'//other/ws'},headers=token()).status_code==400
    ticket=client.post('/session/socket-ticket',json=payload,headers=token()).json()['ticket']
    with client.websocket_connect('/session/socket/'+ticket) as socket:
        # No backend exists in the isolated test; the ticket still must be consumed.
        pass
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as failure:
        with client.websocket_connect('/session/socket/'+ticket): pass
    assert failure.value.code==4401


def test_terminal_upload_streams_authenticated_multipart(module,monkeypatch):
    _,client,token=module
    from ops_portal import hosting
    calls=[]
    class Reply:
        status_code=200
        content=b'{"ok":true}'
        headers={'Content-Type':'application/json'}
    def upstream(method,base,path,credential='',**kwargs):
        body=kwargs['data']
        assert hasattr(body,'read') and not isinstance(body,(bytes,bytearray))
        calls.append((path,credential,kwargs['params'],body.read()))
        return Reply()
    monkeypatch.setattr(hosting,'upstream',upstream)
    response=client.post('/session/upload/core-terminal',params={'path':'/api/v2/terminal/sessions/session-1/files/upload','directory':'/tmp'},headers=token(),files={'file':('sample.txt',b'portal upload sample','text/plain')})
    assert response.status_code==200 and response.json()=={'ok':True}
    assert calls[0][1] and calls[0][2]=={'path':'/tmp'}
    assert b'portal upload sample' in calls[0][3] and b'filename="sample.txt"' in calls[0][3]
