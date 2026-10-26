"""Launch the pinned upstream UI with a short, one-use workbench ticket."""
import hashlib
import json
import os
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import jwt
import requests
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from .core import secret


def origins():
    return [os.getenv('LIFE_NATIVE_HTTP_ORIGIN', ''), os.getenv('LIFE_NATIVE_HTTPS_ORIGIN', '')]


def parent_origins():
    return os.getenv('LIFE_NATIVE_PARENTS', '').split()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cookie_name(key, secure):
    return 'ops_native_' + key.replace('-', '_') + ('_tls' if secure else '_http')


def upstream_cookies(raw, name):
    cookies = SimpleCookie()
    try:
        cookies.load(raw)
    except Exception:
        raise HTTPException(400, 'Cookie 格式无效') from None
    allowed = {'wallos': lambda k: k in {'PHPSESSID', 'wallos_login', 'language', 'sortOrder', 'colorTheme', 'theme'},
               'homebox': lambda k: k.startswith('hb.'),
               'vikunja': lambda k: k == 'vikunja_refresh_token'}[name]
    return '; '.join(k + '=' + v.coded_value for k, v in cookies.items() if allowed(k))


def native_login(name, origin, base):
    credentials = json.loads(secret('LIFE_NATIVE_LOGIN'))
    configured = urlsplit(base)
    base = configured.scheme + '://' + configured.netloc
    session = requests.Session()
    session.trust_env = False
    session.headers.update({'Host': urlsplit(origin).netloc, 'X-Forwarded-Proto': urlsplit(origin).scheme,
                            'Origin': origin})
    try:
        if name == 'wallos':
            response = session.post(base + '/login.php', data=credentials, timeout=(3, 15), allow_redirects=False)
            if response.status_code not in {302, 303} or not session.cookies.get('PHPSESSID'):
                raise ValueError('login rejected')
            token = ''
        else:
            path = '/api/v1/users/login' if name == 'homebox' else '/api/v2/login'
            response = session.post(base + path, json=credentials, timeout=(3, 15), allow_redirects=False)
            response.raise_for_status()
            token = response.json()['token']
            if not token:
                raise ValueError('empty token')
        return token, list(session.cookies)
    except (requests.RequestException, ValueError, KeyError):
        raise HTTPException(502, '原版应用登录失败，请核对服务账号配置') from None
    finally:
        session.close()


class LaunchRequest(BaseModel):
    parent_origin: str


def register_native(app, key, upstream):
    store = app.state.store

    @app.post('/api/v1/native/session')
    def issue(body: LaunchRequest, request: Request, user=Depends(app.state.require_admin)):
        if body.parent_origin not in parent_origins():
            raise HTTPException(403, '不允许此工作台来源')
        origin = origins()[int(body.parent_origin.startswith('https:'))]
        if not origin:
            raise HTTPException(503, '完整应用入口尚未配置')
        claims = jwt.decode(request.headers['authorization'].split(' ', 1)[1], options={'verify_signature': False})
        expires = min(int(claims['exp']), int(time.time()) + 8 * 3600)
        ticket = secrets.token_urlsafe(32)
        with store.connect() as db:
            db.execute("DELETE FROM life_preferences WHERE key LIKE 'native-ticket:%' AND (value->>'until')::bigint < %s", (int(time.time()),))
        store.set_preference('native-ticket:' + digest(ticket), {'until': int(time.time()) + 60,
                             'exp': expires, 'origin': origin, 'parent': body.parent_origin, 'actor': user['username']})
        return {'origin': origin, 'ticket': ticket, 'expires_in': 60}

    @app.post('/_ops/launch', response_class=HTMLResponse)
    async def launch(request: Request):
        parent = request.headers.get('origin', '')
        if parent not in parent_origins():
            raise HTTPException(403, '不允许此工作台来源')
        form = await request.form()
        ticket = str(form.get('ticket', ''))
        if len(ticket) != 43:
            raise HTTPException(401, '启动凭证无效')
        with store.connect() as db:
            row = db.execute('DELETE FROM life_preferences WHERE key=%s RETURNING value',
                             ('native-ticket:' + digest(ticket),)).fetchone()
        data = row['value'] if row else {}
        origin = origins()[int(parent.startswith('https:'))]
        if (data.get('until', 0) <= time.time() or data.get('exp', 0) <= time.time()
                or data.get('parent') != parent or data.get('origin') != origin):
            raise HTTPException(401, '启动凭证已使用或过期，请重新连接')
        token, cookies = await run_in_threadpool(native_login, upstream.name, origin, upstream.base)
        secure = origin.startswith('https:')
        session = jwt.encode({'iss': 'opscenter-native', 'aud': key, 'sub': data['actor'],
                              'exp': data['exp']}, secret('LIFE_CREDENTIAL_KEY'), algorithm='HS256')
        script = ''
        if upstream.name == 'vikunja':
            script = 'localStorage.setItem("token",' + json.dumps(token) + ');localStorage.setItem("API_URL",location.origin);'
        nonce = secrets.token_urlsafe(20)
        response = HTMLResponse('<!doctype html><meta charset="utf-8"><title>正在连接应用</title>'
                                '<p>正在连接完整应用…</p><script nonce="' + nonce + '">' + script
                                + 'location.replace("/");</script>', headers={
                                    'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                    'Content-Security-Policy': "default-src 'none'; script-src 'nonce-" + nonce
                                    + "'; frame-ancestors " + ' '.join(parent_origins())})
        response.set_cookie(cookie_name(key, secure), session, max_age=data['exp'] - int(time.time()),
                            httponly=True, secure=secure, samesite='lax')
        for cookie in cookies:
            response.set_cookie(cookie.name, cookie.value, path=cookie.path or '/', secure=secure,
                                httponly=any(k.lower() == 'httponly' for k in cookie._rest), samesite='lax')
        return response

    @app.get('/_ops/verify')
    def verify(request: Request):
        secure = request.headers.get('x-forwarded-proto') == 'https'
        token = request.cookies.get(cookie_name(key, secure), '')
        try:
            jwt.decode(token, secret('LIFE_CREDENTIAL_KEY'), algorithms=['HS256'], audience=key,
                       issuer='opscenter-native', options={'require': ['exp', 'sub', 'iss', 'aud']})
        except (jwt.InvalidTokenError, OSError):
            raise HTTPException(401, '请从运维工作台打开此应用') from None
        method = request.headers.get('x-forwarded-method', 'GET').upper()
        if method not in {'GET', 'HEAD', 'OPTIONS'}:
            source = request.headers.get('origin', '')
            if source not in origins():
                raise HTTPException(403, '此操作来源无效')
        return Response(status_code=204, headers={
            'X-Native-Cookie': upstream_cookies(request.headers.get('cookie', ''), upstream.name),
            'Cache-Control': 'no-store'})
