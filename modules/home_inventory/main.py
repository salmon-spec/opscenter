from __future__ import annotations

import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import Depends, Header, HTTPException, UploadFile, File
from fastapi.responses import Response
from pydantic import Field
from runtime.app import require_internal_service
from life_common.app import create_app
from life_common.core import Model, Upstream, check_revision, date_value, summary

upstream = Upstream('homebox')
app = create_app('home-inventory', upstream)
store = app.state.store
editor = app.state.require_editor


class Item(Model):
    name: str = Field(min_length=1, max_length=255)
    entityTypeId: UUID
    parentId: UUID | None = None
    description: str = Field(default='', max_length=1000)
    manufacturer: str = Field(default='', max_length=255)
    modelNumber: str = Field(default='', max_length=255)
    serialNumber: str = Field(default='', max_length=255)
    purchaseDate: date | None = None
    purchasePrice: Decimal | None = Field(default=None, ge=0, le=100000000, max_digits=12, decimal_places=2)
    purchaseFrom: str = Field(default='', max_length=255)
    warrantyExpires: date | None = None
    lifetimeWarranty: bool = False
    warrantyDetails: str = Field(default='', max_length=4000)
    notes: str = Field(default='', max_length=4000)
    archived: bool = False
    tagIds: list[UUID] = Field(default_factory=list, max_length=30)
    expected_updated: str = Field(default='', max_length=80)
    reminder_enabled: bool = True

    def update_body(self):
        values = self.model_dump(mode='json', exclude={'expected_updated', 'reminder_enabled'})
        values['purchasePrice'] = float(self.purchasePrice) if self.purchasePrice is not None else None
        for key in ['purchaseDate', 'warrantyExpires']:
            values[key] = values[key] + 'T00:00:00Z' if values[key] else '0001-01-01T00:00:00Z'
        return values


@lru_cache(maxsize=512)
def cached_detail(item_id, revision):
    return upstream.get('/entities/' + item_id)


def hydrated(row):
    if not (row.get('entityType') or {}).get('isLocation'):
        return row | cached_detail(row['id'], row['updatedAt'])
    return row.copy()


def all_items():
    rows = []
    for page in range(1, 51):
        result = upstream.get('/entities', params={'page': page, 'pageSize': 100})
        batch = result['items']
        rows.extend(batch)
        if len(rows) >= result.get('total', len(rows)) or len(batch) < 100:
            # The summary endpoint omits warranty/model fields; invalidate by upstream revision.
            with ThreadPoolExecutor(max_workers=4) as pool:
                rows = list(pool.map(hydrated, rows))
            preferences = store.preferences('reminder:')
            for row in rows:
                row['reminder_enabled'] = preferences.get('reminder:' + row['id'], True)
            return rows
    raise HTTPException(422, '物品超过5000条，请缩小查询范围')


@app.get('/api/v1/items')
def items():
    return {'items': all_items()}


@app.get('/api/v1/catalog')
def catalogs():
    return {'types': upstream.get('/entity-types'), 'tags': upstream.get('/tags'),
            'locations': [x for x in all_items() if x.get('entityType', {}).get('isLocation')]}


class Tag(Model):
    title: str = Field(min_length=1, max_length=100)


@app.post('/api/v1/labels')
def add_tag(body: Tag, idempotency_key: UUID = Header(), user=Depends(editor)):
    return store.once(idempotency_key, 'tag:create', body.model_dump(), user['username'],
                      lambda: upstream.request('POST', '/tags', body={'name': body.title}))


@app.get('/api/v1/items/{item_id}')
def detail(item_id: UUID):
    row = upstream.get('/entities/' + str(item_id))
    row['reminder_enabled'] = store.preference('reminder:' + str(item_id), True)
    return row


@app.get('/api/v1/summary')
def overview():
    rows = all_items()
    objects = [x for x in rows if not x.get('entityType', {}).get('isLocation')]
    return summary({'total': len(objects), 'locations': len(rows) - len(objects),
                    'with_warranty': sum(bool(x.get('warrantyExpires')) or x.get('lifetimeWarranty', False) for x in objects)})


@app.post('/api/v1/items')
def create(body: Item, idempotency_key: UUID = Header(), user=Depends(editor)):
    def operation():
        payload = body.update_body()
        first = {k: payload[k] for k in ['name', 'entityTypeId', 'parentId', 'description', 'manufacturer', 'modelNumber', 'tagIds']}
        row = upstream.request('POST', '/entities', body=first | {'quantity': 1})
        # Persist the new ID before the second request so an interrupted create can be located.
        store.set_preference('pending-item:' + str(idempotency_key), {'id': row['id'], 'name': body.name})
        result = upstream.request('PUT', '/entities/' + row['id'], body=payload | {'quantity': 1})
        store.set_preference('reminder:' + row['id'], body.reminder_enabled)
        store.set_preference('pending-item:' + str(idempotency_key), None)
        return result
    return store.once(idempotency_key, 'item:create', body.model_dump(mode='json'), user['username'], operation)


@app.put('/api/v1/items/{item_id}')
def update(item_id: UUID, body: Item, idempotency_key: UUID = Header(), user=Depends(editor)):
    def apply_update():
        current = detail(item_id)
        check_revision(current, body.expected_updated, 'updatedAt')
        # Homebox PUT replaces fields; retain fields outside the workbench form.
        current['entityTypeId'] = current.get('entityType', {}).get('id', str(body.entityTypeId))
        current['tagIds'] = [t['id'] for t in current.get('tags', [])]
        result = upstream.request('PUT', '/entities/' + str(item_id), body=current | body.update_body())
        store.set_preference('reminder:' + str(item_id), body.reminder_enabled)
        return result
    def operation():
        with store.target_lock('item:' + str(item_id)):
            return apply_update()
    return store.once(idempotency_key, f'item:{item_id}:update', body.model_dump(mode='json'), user['username'], operation)


@app.get('/api/v1/items/{item_id}/maintenance')
def maintenance(item_id: UUID):
    return {'items': upstream.get('/entities/' + str(item_id) + '/maintenance', params={'status': 'both'})}


class Maintenance(Model):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default='', max_length=4000)
    completedDate: date
    cost: Decimal = Field(default=Decimal(0), ge=0, le=100000000, max_digits=12, decimal_places=2)


@app.post('/api/v1/items/{item_id}/maintenance')
def add_maintenance(item_id: UUID, body: Maintenance, idempotency_key: UUID = Header(), user=Depends(editor)):
    return store.once(idempotency_key, f'item:{item_id}:maintenance', body.model_dump(mode='json'), user['username'],
                      lambda: upstream.request('POST', '/entities/' + str(item_id) + '/maintenance',
                                               body=body.model_dump(mode='json') | {'completedDate': str(body.completedDate) + 'T00:00:00Z'}))


def attachment_type(content):
    if content.startswith(b'%PDF-'):
        return 'application/pdf', '.pdf'
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', '.png'
    if content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', '.jpg'
    if content.startswith(b'RIFF') and content[8:12] == b'WEBP':
        return 'image/webp', '.webp'
    raise HTTPException(422, '仅支持PDF、PNG、JPEG和WebP附件')


@app.post('/api/v1/items/{item_id}/attachments')
def upload(item_id: UUID, idempotency_key: UUID = Header(), file: UploadFile = File(), user=Depends(editor)):
    content = file.file.read(5 * 1024 * 1024 + 1)
    if not content or len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, '附件不能为空或超过5MiB')
    media, ext = attachment_type(content)
    name = (file.filename or '附件').replace('\\', '/').split('/')[-1][:120]
    if not name.lower().endswith(ext) and not (ext == '.jpg' and name.lower().endswith('.jpeg')):
        raise HTTPException(422, '附件扩展名与内容不匹配')
    return store.once(idempotency_key, f'item:{item_id}:attachment',
                      {'name': name, 'sha256': hashlib.sha256(content).hexdigest()}, user['username'],
                      lambda: upstream.request('POST', '/entities/' + str(item_id) + '/attachments',
                                               form={'name': name, 'type': 'attachment'}, files={'file': (name, content, media)}))


@app.get('/api/v1/items/{item_id}/attachments/{attachment_id}')
def download(item_id: UUID, attachment_id: UUID):
    content, media = upstream.get(f'/entities/{item_id}/attachments/{attachment_id}', binary=True)
    return Response(content, media_type=media, headers={'Content-Disposition': 'attachment; filename="attachment"',
                                                     'X-Content-Type-Options': 'nosniff'})


@app.get('/api/v1/internal/due', dependencies=[Depends(require_internal_service)])
def due():
    return {'items': [{'id': x['id'], 'title': x['name'] + ' · 保修到期', 'date': date_value(x['warrantyExpires']),
                       'offsets': [30, 7, 0], 'link': '/extensions/home-inventory'} for x in all_items()
                      if x.get('warrantyExpires') and not x.get('lifetimeWarranty') and not x.get('archived') and x.get('reminder_enabled', True)]}


@app.get('/api/v1/export.zip')
def export_zip():
    rows = [detail(UUID(x['id'])) for x in all_items()]
    output = io.BytesIO()
    size = 0
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('inventory.json', json.dumps(rows, ensure_ascii=False))
        for item in rows:
            for attachment in item.get('attachments', []):
                content, _ = upstream.get(f"/entities/{item['id']}/attachments/{attachment['id']}", binary=True)
                size += len(content)
                if size > 50 * 1024 * 1024:
                    raise HTTPException(413, '附件总量超过50MiB，请使用管理员完整备份')
                archive.writestr(item['id'] + '/' + attachment['id'], content)
    return Response(output.getvalue(), media_type='application/zip',
                    headers={'Content-Disposition': 'attachment; filename="home-inventory.zip"'})
