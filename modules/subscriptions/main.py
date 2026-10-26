from __future__ import annotations

import csv
import io
import hashlib
import json
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid5

from fastapi import Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import Field, HttpUrl
from runtime.app import require_internal_service
from life_common.app import create_app
from life_common.core import Model, Upstream, date_value, summary

upstream = Upstream('wallos')
app = create_app('subscriptions', upstream)
store = app.state.store
editor = app.state.require_editor


class Subscription(Model):
    name: str = Field(min_length=1, max_length=160)
    price: Decimal = Field(ge=0, le=100000000, max_digits=12, decimal_places=2)
    currency_id: int = Field(gt=0)
    frequency: int = Field(default=1, ge=1, le=365)
    cycle: int = Field(default=3, ge=1, le=4)
    next_payment: date
    start_date: date | None = None
    auto_renew: bool = True
    inactive: bool = False
    category_id: int | None = Field(default=None, gt=0)
    payment_method_id: int | None = Field(default=None, gt=0)
    notes: str = Field(default='', max_length=4000)
    url: HttpUrl | None = None
    cancellation_date: date | None = None
    reminder_enabled: bool = True
    expected_revision: str = Field(default='', max_length=64)

    def form(self, create=False):
        values = {k: int(v) if isinstance(v, bool) else str(v) if v is not None else '' for k, v in self.model_dump(exclude={'reminder_enabled','expected_revision'}).items()}
        if not self.start_date:
            values.pop('start_date')
        if create and self.payment_method_id is None:
            values.pop('payment_method_id')
        return values | {'notify': 0}


def subscriptions():
    rows = upstream.get('/api/subscriptions/get_subscriptions.php')['subscriptions']
    preferences = store.preferences('reminder:')
    for row in rows:
        row['workbench_revision'] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        row['reminder_enabled'] = preferences.get('reminder:' + str(row['id']), True)
    return rows


def catalog(path, key):
    data = upstream.get('/api/' + path)
    return data.get(key, [])


@app.get('/api/v1/catalog')
def catalogs():
    return {'currencies': catalog('currencies/get_currencies.php', 'currencies'),
            'categories': catalog('categories/get_categories.php', 'categories'),
            'payment_methods': catalog('payment_methods/get_payment_methods.php', 'payment_methods')}


@app.get('/api/v1/subscriptions')
def list_subscriptions():
    return {'items': subscriptions()}


@app.get('/api/v1/summary')
def overview():
    items = subscriptions()
    amounts = {}
    for row in items:
        if row.get('inactive'):
            continue
        currency = str(row['currency_id'])
        price = Decimal(str(row['price']))
        frequency = Decimal(str(row['frequency']))
        annual = price * {1: Decimal(365), 2: Decimal(52), 3: Decimal(12), 4: Decimal(1)}[int(row['cycle'])] / frequency
        amounts[currency] = amounts.get(currency, Decimal(0)) + annual
    return summary({'total': len(items), 'active': sum(not x.get('inactive') for x in items),
                    'estimated_annual': {k: str(v.quantize(Decimal('.01'))) for k, v in amounts.items()}})


@app.post('/api/v1/subscriptions')
def create(body: Subscription, idempotency_key: UUID = Header(), user=Depends(editor)):
    def operation():
        result = upstream.request('POST', '/api/subscriptions/set_subscriptions.php', form=body.form(create=True) | {'action': 'add'})
        store.set_preference('reminder:' + str(result['subscriptionId']), body.reminder_enabled)
        return result
    return store.once(idempotency_key, 'subscription:create', body.model_dump(mode='json'), user['username'], operation)


@app.put('/api/v1/subscriptions/{item_id}')
def update(item_id: int, body: Subscription, idempotency_key: UUID = Header(), user=Depends(editor)):
    if item_id <= 0:
        raise HTTPException(422, '无效订阅编号')
    def operation():
        with store.target_lock('subscription:' + str(item_id)):
            if body.expected_revision:
                row = next((x for x in subscriptions() if x['id'] == item_id), None)
                if not row:
                    raise HTTPException(404, '订阅不存在')
                if row['workbench_revision'] != body.expected_revision:
                    raise HTTPException(409, '订阅已更新，请刷新后重新编辑')
            result = upstream.request('POST', '/api/subscriptions/set_subscriptions.php', form=body.form() | {'action': 'edit', 'id': item_id})
            store.set_preference('reminder:' + str(item_id), body.reminder_enabled)
            return result
    return store.once(idempotency_key, f'subscription:{item_id}:update', body.model_dump(mode='json'), user['username'], operation)


@app.get('/api/v1/internal/due', dependencies=[Depends(require_internal_service)])
def due():
    items = []
    for row in subscriptions():
        if row.get('inactive') or not row.get('reminder_enabled', True):
            continue
        for field, label in [('next_payment', '续费'), ('cancellation_date', '取消截止')]:
            if row.get(field):
                items.append({'id': str(row['id']) + ':' + field, 'title': row['name'] + ' · ' + label,
                              'date': date_value(row[field]), 'offsets': [7, 1, 0],
                              'link': '/extensions/subscriptions'})
    return {'items': items}


def safe_cell(value):
    text = str(value if value is not None else '')
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text


CSV_FIELDS = ['name', 'price', 'currency_id', 'frequency', 'cycle', 'next_payment', 'start_date',
              'auto_renew', 'inactive', 'category_id', 'payment_method_id', 'notes', 'url', 'cancellation_date']


@app.get('/api/v1/export.csv')
def export_csv():
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for row in subscriptions():
        writer.writerow({key: safe_cell(row.get(key)) for key in CSV_FIELDS})
    return Response('\ufeff' + output.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="subscriptions.csv"'})


class ImportBody(Model):
    csv_text: str = Field(min_length=1, max_length=1024 * 1024)


def preview_csv(text):
    rows, errors, seen = [], [], set()
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not {'name','price','currency_id','next_payment'}.issubset(reader.fieldnames or []):
        raise HTTPException(422, 'CSV缺少名称、金额、币种或付款日期表头')
    for index, row in enumerate(reader, 2):
        if index > 501:
            raise HTTPException(422, '每批最多500条')
        try:
            values = {k: v for k, v in row.items() if v != ''}
            body = Subscription.model_validate(values)
            duplicate = body.name.casefold() in seen
            seen.add(body.name.casefold())
            rows.append({'line': index, 'data': body.model_dump(mode='json'), 'duplicate': duplicate})
        except (ValueError, TypeError):
            errors.append({'line': index, 'error': '字段格式或金额/日期无效'})
    existing = {r['name'].casefold() for r in subscriptions()}
    for row in rows:
        row['duplicate'] |= row['data']['name'].casefold() in existing
    return {'rows': rows, 'errors': errors}


@app.post('/api/v1/import/preview')
def preview(body: ImportBody, user=Depends(editor)):
    return preview_csv(body.csv_text)


@app.post('/api/v1/import/confirm')
def confirm(body: ImportBody, idempotency_key: UUID = Header(), user=Depends(editor)):
    def apply():
        batch = preview_csv(body.csv_text)
        if batch['errors'] or any(r['duplicate'] for r in batch['rows']):
            raise HTTPException(422, '请先修正格式错误或重名记录，再导入')
        for row in batch['rows']:
            create(Subscription.model_validate(row['data']), uuid5(idempotency_key, str(row['line'])), user)
        return {'imported': len(batch['rows'])}
    return store.once(idempotency_key, 'subscription:import', body.model_dump(), user['username'], apply)
