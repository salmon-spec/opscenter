"""Portal host transport. Upstreams and module permissions come from the core registry."""
import re
from urllib.parse import unquote, urlsplit

import requests
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

CORE = 'http://backend:9091'
WEB = 'http://web'
LIFE = {'subscriptions', 'home-inventory', 'life-reminders'}
KEY = re.compile(r'^[a-z0-9][a-z0-9-]{1,62}$')


def safe_path(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise HTTPException(400, '接口路径无效')
    decoded = value
    for _ in range(4):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    parts = urlsplit(decoded)
    if (not decoded.startswith('/') or decoded.startswith('//') or parts.scheme or parts.netloc
            or parts.query or parts.fragment or '%' in decoded or '\\' in decoded
            or any(ord(c) < 32 for c in decoded) or any(p in {'.', '..'} for p in decoded.split('/'))):
        raise HTTPException(400, '接口路径无效')
    return decoded


def upstream(method, base, path, token='', **kwargs):
    try:
        response = requests.request(method, base + path, headers={
            **({'Authorization': 'Bearer ' + token} if token else {}),
            **kwargs.pop('headers', {}),
        }, timeout=120, allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise HTTPException(503, '模块服务暂不可用') from None
    return response


def registry(token):
    response = upstream('GET', CORE, '/api/v2/modules', token)
    if response.status_code != 200:
        raise HTTPException(response.status_code, '模块目录读取失败')
    return response.json().get('items', [])


def host_info(item):
    key = item['key']
    entry = item.get('entry_url', '')
    if not KEY.fullmatch(key):
        return None
    if key in LIFE:
        prefixes = [f'/modules/{key}/api/v1/']
        sandbox = ['allow-scripts', 'allow-forms', 'allow-downloads', 'allow-modals']
    elif (entry.startswith(f'/modules/{key}/ui') or entry.startswith(f'/v3/extension-modules/{key}/')):
        prefixes = item.get('manifest', {}).get('api_prefixes', [])
        sandbox = [v for v in item.get('manifest', {}).get('sandbox', ['allow-scripts'])
                   if v in {'allow-scripts', 'allow-forms', 'allow-downloads'}]
    else:
        return None
    return {**{k: item.get(k) for k in ['key', 'name', 'version', 'description', 'running']},
            'entry_url': f'/host/{key}/ui/',
            'manifest': {'api_prefixes': prefixes, 'sandbox': sandbox}}


class Relay(BaseModel):
    module: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{1,62}$')
    path: str
    method: str = 'GET'
    query: dict = Field(default_factory=dict)
    body: object = None
    idempotency_key: str = Field(default='', max_length=200)
    binary: bool = False


def install(app, root, current_user):
    def access(request, key, path):
        token = request.headers.get('authorization', '').removeprefix('Bearer ')
        item = next((i for i in registry(token) if i['key'] == key), None)
        info = host_info(item) if item else None
        if not info:
            raise HTTPException(403, '该模块当前不可用')
        path = safe_path(path)
        prefixes = [safe_path(p).rstrip('/') + '/' for p in info['manifest']['api_prefixes']]
        own = f'/modules/{key}/'
        if not any(p.startswith(own) and path.startswith(p) for p in prefixes):
            raise HTTPException(403, '模块无权访问该接口')
        if any(segment in {'internal', 'health', 'metrics'} for segment in path.split('/')[3:]):
            raise HTTPException(403, '内部接口不可访问')
        return token, path

    def result(response):
        # Forward business status and content, never upstream cookies, redirects or auth headers.
        return Response(response.content, status_code=response.status_code,
                        headers={'Content-Type': response.headers.get('Content-Type', 'application/octet-stream')})

    @app.get('/session/modules')
    def modules(request: Request, user=Depends(current_user)):
        token = request.headers['authorization'].removeprefix('Bearer ')
        return {'items': [info for item in registry(token) if (info := host_info(item))]}

    @app.post('/session/relay')
    def relay(payload: Relay, request: Request, user=Depends(current_user)):
        token, path = access(request, payload.module, payload.path)
        method = payload.method.upper()
        if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}:
            raise HTTPException(405, '不支持的请求方法')
        headers = {'Idempotency-Key': payload.idempotency_key} if payload.idempotency_key else {}
        return result(upstream(method, WEB, path, token, params=payload.query,
                               **({'json': payload.body} if method != 'GET' else {}), headers=headers))

    @app.post('/session/upload/{key}')
    async def upload(key: str, request: Request, user=Depends(current_user)):
        token, path = access(request, key, request.query_params.get('path', ''))
        if key not in LIFE or not re.fullmatch(rf'/modules/{key}/api/v1/(items|tasks)/[a-zA-Z0-9-]+/attachments', path):
            raise HTTPException(403, '模块无权上传到该接口')
        if int(request.headers.get('content-length', '0')) > 6 * 1024 * 1024:
            raise HTTPException(413, '附件不能超过5MiB')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 6 * 1024 * 1024:
                raise HTTPException(413, '附件不能超过5MiB')
        content_type = request.headers.get('content-type', '')
        if not content_type.startswith('multipart/form-data;'):
            raise HTTPException(415, '需要附件表单')
        headers = {'Content-Type': content_type}
        if request.headers.get('idempotency-key'):
            headers['Idempotency-Key'] = request.headers['idempotency-key'][:200]
        # Blocking upstream I/O runs in a worker, keeping the async upload endpoint responsive.
        from starlette.concurrency import run_in_threadpool
        return result(await run_in_threadpool(upstream, 'POST', WEB, path, token, data=bytes(data), headers=headers))

    @app.get('/host/{key}/ui/', response_class=HTMLResponse)
    def page(key: str):
        if key in LIFE:
            css = (root / 'native/style.css').read_text(encoding='utf-8')
            js = (root / 'native/app.js').read_text(encoding='utf-8')
            return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>' + css + '\n.portal-native{padding:16px}.portal-notice{position:fixed;bottom:24px;left:30%;background:#18334b;color:white;padding:12px 24px;border-radius:8px;z-index:9999}</style><div id="app"></div><script>' + js.replace('</script', '<\\/script') + '</script></html>'
        # UI documents are public assets. Data always uses the authenticated, scoped relay.
        if not KEY.fullmatch(key):
            raise HTTPException(404)
        if key == 'ops-portal':
            return app.state.portal_management()
        if key in {'oss-browser', 'command-code-usage'}:
            path = f'/v3/extension-modules/{key}/index.html'
        elif key == 'electricity':
            path = '/modules/electricity/ui'
        else:
            path = f'/modules/{key}/ui/'
        response = upstream('GET', WEB, path)
        if response.status_code != 200:
            raise HTTPException(503, '模块页面暂不可用')
        html = response.text
        # Automation's relative assets are bundled just as in its workbench host.
        if key == 'automation':
            css = upstream('GET', WEB, '/modules/automation/static/app.css')
            js = upstream('GET', WEB, '/modules/automation/static/app.js')
            if css.status_code != 200 or js.status_code != 200:
                raise HTTPException(503, '模块资源暂不可用')
            html = html.replace('<link rel="stylesheet" href="../static/app.css">', '<style>' + css.text + '</style>')
            html = html.replace('<script src="../static/app.js" defer></script>', '')
            html = html.replace('</body>', '<script>' + js.text.replace('</script', '<\\/script') + '</script></body>')
        if key in {'electricity', 'oss-browser', 'command-code-usage'}:
            bootstrap = "<script>window.__portalNonce='';addEventListener('message',e=>{if(e.source===parent&&e.data?.type==='ops-module-info')window.__portalNonce=e.data.nonce;});</script>"
            html = html.replace('<head>', '<head>' + bootstrap, 1)
            html = re.sub(r"(type:\s*['\"]ops-module-(?:api|download)-request['\"],)",
                          r"\1 nonce: window.__portalNonce,", html)
        return html
