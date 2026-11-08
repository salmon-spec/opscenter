"""Authenticated portal catalogue and isolated, persistent module administration."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from runtime.app import create_module_app
from runtime.auth import SessionVerifier

VERSION = '0.6.0'
ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('PORTAL_DB_PATH', '/data/portal.sqlite3'))
PORTAL_URL = os.getenv('PORTAL_PUBLIC_URL', 'http://10.66.66.13:30092/').rstrip('/') + '/'


class AppEntry(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,120}$')
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2000)
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(default='', max_length=300)
    domain: str = ''
    enabled: bool = True
    status: str = Field(default='unverified', max_length=30)
    embedding: str = Field(default='unverified', max_length=30)
    checked_at: str | None = Field(default=None, max_length=60)
    code: int | None = None
    error: str = Field(default='', max_length=500)
    x_frame_options: str = Field(default='', max_length=1000)
    csp: str = Field(default='', max_length=8000)
    title: str = Field(default='', max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    parent_app: str | None = Field(default=None, max_length=120)
    module_key: str | None = Field(default=None, pattern=r'^[a-z0-9][a-z0-9-]{1,62}$')

    @field_validator('name', 'category')
    @classmethod
    def text_not_blank(cls, value):
        if not value.strip():
            raise ValueError('名称和分类不能为空')
        return value.strip()

    @field_validator('url')
    @classmethod
    def valid_url(cls, value):
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError:
            raise ValueError('地址格式不正确') from None
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('应用地址必须为不含账号密码的 HTTP(S) URL')
        if any(ord(c) < 32 for c in value) or '\\' in value:
            raise ValueError('地址不能包含控制字符或反斜线')
        return value

    @field_validator('domain')
    @classmethod
    def valid_domain(cls, value):
        if value not in {'', '平台支撑', '运维场景', '众创共享'}:
            raise ValueError('业务域无效')
        return value

    @model_validator(mode='after')
    def module_address(self):
        if self.module_key and urlsplit(self.url).path != f'/host/{self.module_key}/ui/':
            raise ValueError('内置模块地址必须使用该模块的门户入口')
        return self


class Catalogue(BaseModel):
    revision: int = Field(ge=1)
    apps: list[AppEntry] = Field(max_length=200)
    default_favorites: list[str] = Field(default_factory=list, max_length=7)

    @model_validator(mode='after')
    def unique_entries(self):
        ids = [a.id for a in self.apps]
        if len(ids) != len(set(ids)):
            raise ValueError('应用 ID 重复')
        if any(i not in ids for i in self.default_favorites):
            raise ValueError('快捷栏引用了不存在的应用')
        if any(a.parent_app and a.parent_app not in ids for a in self.apps):
            raise ValueError('功能入口引用了不存在的父应用')
        return self


def connect():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS catalogue (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, body TEXT NOT NULL, updated_at TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, actor TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL)')
        seed = json.loads((ROOT / 'seed.json').read_text(encoding='utf-8'))
        validated = Catalogue(revision=1, apps=seed['apps'], default_favorites=seed.get('default_favorites', []))
        db.execute('INSERT OR IGNORE INTO catalogue VALUES(1,1,?,?)',
                   (json.dumps(validated.model_dump(exclude={'revision'}), ensure_ascii=False), now()))


def now():
    return datetime.now(timezone.utc).isoformat()


def read_catalogue():
    with connect() as db:
        row = db.execute('SELECT * FROM catalogue WHERE id=1').fetchone()
    seed = json.loads((ROOT / 'seed.json').read_text(encoding='utf-8'))
    return {**json.loads(row['body']), 'revision': row['revision'], 'updated_at': row['updated_at'],
            'version': VERSION, 'checked_at': seed['checked_at'], 'source_count': seed['source_count'], 'portal_url': PORTAL_URL}


def write_catalogue(payload: Catalogue, actor: str):
    body = json.dumps(payload.model_dump(exclude={'revision'}), ensure_ascii=False, sort_keys=True)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT revision FROM catalogue WHERE id=1').fetchone()[0]
        if current != payload.revision:
            raise HTTPException(409, '配置已被其他窗口修改，请刷新后重新编辑')
        revision = current + 1
        db.execute('UPDATE catalogue SET revision=?,body=?,updated_at=? WHERE id=1', (revision, body, now()))
        db.execute('INSERT INTO audit(actor,revision,digest,created_at) VALUES(?,?,?,?)',
                   (actor, revision, hashlib.sha256(body.encode()).hexdigest(), now()))
    return read_catalogue()


def summary():
    data = read_catalogue()
    return {'status': 'ok', 'collected_at': now(), 'data': {'apps': len(data['apps']), 'revision': data['revision']}}


initialize()
app = create_module_app('ops-portal', VERSION, summary, lambda: {'status': 'ok', 'collected_at': now(), 'items': []},
                        capabilities=['catalogue:read', 'catalogue:manage'], readiness_provider=read_catalogue)
verifier = SessionVerifier()
bearer = HTTPBearer(auto_error=False)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not credentials:
        raise HTTPException(401, '请先登录运维门户')
    return verifier.verify(credentials.credentials)


def admin(user=Depends(current_user)):
    if user['role'] != 'admin':
        raise HTTPException(403, '只有管理员可以修改门户配置')
    return user


@app.middleware('http')
async def browser_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-src http: https:; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'self' http://10.66.66.13:30088 http://10.66.66.14:30088 http://10.66.66.15:30088 https://10.66.66.13 https://10.66.66.15"
    return response


@app.get('/api/v1/catalogue')
def catalogue(user=Depends(current_user)):
    return read_catalogue()


@app.post('/api/v1/catalogue')
def save_catalogue(payload: Catalogue, user=Depends(admin)):
    return write_catalogue(payload, user['username'])


@app.get('/api/v1/audit')
def audit(user=Depends(admin)):
    with connect() as db:
        return {'items': [dict(row) for row in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 50')]}


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1000)


@app.post('/session/login')
def login(payload: Login):
    try:
        response = requests.post(os.getenv('PORTAL_AUTH_URL', 'http://backend:9091/api/v2/auth/login'),
                                 json=payload.model_dump(), timeout=15, allow_redirects=False)
    except requests.RequestException:
        raise HTTPException(503, '统一认证服务暂不可用') from None
    if response.status_code != 200:
        raise HTTPException(429 if response.status_code == 429 else 401, '登录失败，请检查账号密码或稍后重试')
    token = response.json().get('access_token', '')
    verifier.verify(token)
    return {'access_token': token}


@app.get('/', response_class=HTMLResponse)
def portal():
    return (ROOT / 'static/index.html').read_text(encoding='utf-8')


@app.get('/ui/', response_class=HTMLResponse)
def management():
    # The existing host sandbox has an opaque origin, so bundle its own UI resources inline.
    html = (ROOT / 'static/manage.html').read_text(encoding='utf-8')
    return html.replace('/* MANAGEMENT_CSS */', (ROOT / 'static/manage.css').read_text(encoding='utf-8')).replace('/* MANAGEMENT_JS */', (ROOT / 'static/manage.js').read_text(encoding='utf-8'))


app.state.portal_management = management
from ops_portal.hosting import install
install(app, ROOT, current_user)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
