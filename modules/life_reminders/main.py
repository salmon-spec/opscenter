from __future__ import annotations

import calendar
import os
import threading
import logging
import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Annotated
from uuid import UUID, uuid5, NAMESPACE_URL

from fastapi import Depends, Header, HTTPException, UploadFile, File
from fastapi.responses import Response
from pydantic import Field, model_validator
from life_common.app import create_app
from life_common.core import Model, Upstream, check_revision, summary, timestamp
from life_common.files import attachment_type
from life_reminders.notifications import scan, deliveries, settings, save_settings, acknowledge, snooze, source_events

upstream = Upstream('vikunja')
app = create_app('life-reminders', upstream)
store = app.state.store
editor = app.state.require_editor


def tasks():
    rows = []
    for page in range(1, 51):
        batch = upstream.get('/tasks', params={'page': page, 'per_page': 100})
        rows.extend(batch or [])
        if len(batch or []) < 100:
            policies = store.preferences('repeat:')
            for row in rows:
                policy = policies.get('repeat:' + str(row['id']), {})
                inferred = 'monthly' if row.get('repeat_mode') == 1 else {86400:'daily',604800:'weekly'}.get(row.get('repeat_after'), 'none')
                row['workbench_repeat'] = policy.get('kind', inferred)
                row['reminder_enabled'] = policy.get('enabled', True)
            return rows
    raise HTTPException(422, '任务超过5000条，请归档历史任务')


class Task(Model):
    title: str = Field(min_length=1, max_length=250)
    description: str = Field(default='', max_length=4000)
    project_id: int = Field(gt=0)
    priority: int = Field(default=0, ge=0, le=5)
    percent_done: float = Field(default=0,ge=0,le=1)
    is_favorite: bool = False
    start_date: datetime | None = None
    end_date: datetime | None = None
    due_date: datetime | None = None
    reminder: datetime | None = None
    repeat: Literal['none', 'daily', 'weekly', 'monthly', 'yearly'] = 'none'
    parent_id: int | None = Field(default=None, gt=0)
    expected_updated: str = Field(default='', max_length=80)
    reminder_enabled: bool = True
    label_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def valid_dates(self):
        for value in [self.due_date, self.reminder,self.start_date,self.end_date]:
            if value and (value.utcoffset() is None or value.year < 1970):
                raise ValueError('日期必须包含时区且不早于1970年')
        if self.repeat != 'none' and not self.due_date:
            raise ValueError('重复任务必须填写截止时间')
        if self.start_date and self.end_date and self.end_date<self.start_date:
            raise ValueError('结束时间不能早于开始时间')
        return self

    def payload(self):
        period = {'none': 0, 'daily': 86400, 'weekly': 604800, 'monthly': 1, 'yearly': 31536000}[self.repeat]
        values = {'title': self.title, 'description': self.description, 'project_id': self.project_id,
                'percent_done':self.percent_done,'is_favorite':self.is_favorite,
                'start_date':self.start_date.isoformat() if self.start_date else '0001-01-01T00:00:00Z',
                'end_date':self.end_date.isoformat() if self.end_date else '0001-01-01T00:00:00Z',
                'priority': self.priority, 'due_date': self.due_date.isoformat() if self.due_date else '0001-01-01T00:00:00Z',
                'reminders': [{'reminder': self.reminder.isoformat()}] if self.reminder else [],
                'repeat_after': period, 'repeat_mode': 1 if self.repeat == 'monthly' else 0}
        for key in ['percent_done','is_favorite','start_date','end_date']:
            if key not in self.model_fields_set:
                values.pop(key,None)
        return values


@app.get('/api/v1/tasks')
def list_tasks():
    return {'items': tasks()}


@app.get('/api/v1/tasks/{task_id}')
def task_detail(task_id: int):
    if task_id<=0:
        raise HTTPException(422,'无效任务编号')
    return upstream.get(f'/tasks/{task_id}')


@app.post('/api/v1/tasks/{task_id}/attachments')
def upload_attachment(task_id: int,idempotency_key: UUID=Header(),file: UploadFile=File(),user=Depends(editor)):
    task_detail(task_id)
    content=file.file.read(5*1024*1024+1)
    if not content or len(content)>5*1024*1024:
        raise HTTPException(413,'附件不能为空或超过5MiB')
    media,ext=attachment_type(content)
    name=(file.filename or 'attachment').replace('\\','/').split('/')[-1][:120]
    if not name.lower().endswith(ext) and not (ext=='.jpg' and name.lower().endswith('.jpeg')):
        raise HTTPException(422,'附件扩展名与内容不匹配')
    return store.once(idempotency_key,f'task:{task_id}:attachment',{'sha256':hashlib.sha256(content).hexdigest(),'name':name},user['username'],
                      lambda:upstream.request('PUT',f'/tasks/{task_id}/attachments',files={'files':(name,content,media)}))


@app.get('/api/v1/tasks/{task_id}/attachments/{attachment_id}')
def download_attachment(task_id: int,attachment_id: int):
    if task_id<=0 or attachment_id<=0:
        raise HTTPException(422,'无效附件编号')
    content,media=upstream.get(f'/tasks/{task_id}/attachments/{attachment_id}',binary=True)
    return Response(content,media_type=media,headers={'Content-Disposition':'attachment; filename="attachment"','X-Content-Type-Options':'nosniff'})


@app.get('/api/v1/catalog')
def catalogs():
    return {'projects': project_lists(), 'labels': upstream.get('/labels', params={'per_page': 100}) or []}


def project_lists():
    return [x for x in upstream.get('/projects') or [] if x['id'] > 0 and not x.get('is_archived')]


class Project(Model):
    title: str = Field(min_length=1, max_length=200)


@app.post('/api/v1/projects')
def add_project(body: Project, idempotency_key: UUID = Header(), user=Depends(editor)):
    return store.once(idempotency_key, 'project:create', body.model_dump(), user['username'],
                      lambda: upstream.request('PUT', '/projects', body=body.model_dump()))


@app.post('/api/v1/labels')
def add_label(body: Project, idempotency_key: UUID = Header(), user=Depends(editor)):
    return store.once(idempotency_key, 'label:create', body.model_dump(), user['username'],
                      lambda: upstream.request('PUT', '/labels', body=body.model_dump()))


def record_repeat(task_id, body):
    anchor = body.due_date.astimezone(timezone(timedelta(hours=8))) if body.due_date else None
    store.set_preference('repeat:' + str(task_id), {'kind': body.repeat,
                         'month': anchor.month if anchor else 1,
                         'day': anchor.day if anchor else 1, 'enabled': body.reminder_enabled})


@app.post('/api/v1/tasks')
def create(body: Task, idempotency_key: UUID = Header(), user=Depends(editor)):
    def operation():
        row = upstream.request('PUT', f'/projects/{body.project_id}/tasks', body=body.payload())
        store.set_preference('pending-task:' + str(idempotency_key), {'id': row['id'], 'title': body.title})
        record_repeat(row['id'], body)
        if body.label_ids:
            upstream.request('POST', f"/tasks/{row['id']}/labels/bulk", body={'labels': [{'id': i} for i in body.label_ids]})
        if body.parent_id:
            upstream.request('PUT', f"/tasks/{body.parent_id}/relations",
                             body={'relation_kind': 'subtask', 'other_task_id': row['id']})
        store.set_preference('pending-task:' + str(idempotency_key), None)
        return row
    return store.once(idempotency_key, 'task:create', body.model_dump(mode='json'), user['username'], operation)


@app.put('/api/v1/tasks/{task_id}')
def update(task_id: int, body: Task, idempotency_key: UUID = Header(), user=Depends(editor)):
    if task_id <= 0:
        raise HTTPException(422, '无效任务编号')
    def apply_update():
        row = upstream.get(f'/tasks/{task_id}')
        check_revision(row, body.expected_updated, 'updated')
        result = upstream.request('POST', f'/tasks/{task_id}', body=row | body.payload())
        upstream.request('POST', f'/tasks/{task_id}/labels/bulk', body={'labels': [{'id': i} for i in body.label_ids]})
        record_repeat(task_id, body)
        return result
    def operation():
        with store.target_lock('task:' + str(task_id)):
            return apply_update()
    return store.once(idempotency_key, f'task:{task_id}:update', body.model_dump(mode='json'), user['username'], operation)


class Completion(Model):
    done: bool


def calendar_interval(value, policy):
    current = datetime.fromisoformat(value.replace('Z', '+00:00'))
    local = current.astimezone(timezone(timedelta(hours=8)))
    if policy.get('kind') == 'monthly':
        year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    else:
        year, month = local.year + 1, policy['month']
    target = local.replace(year=year, month=month,
                           day=min(policy['day'], calendar.monthrange(year, month)[1]))
    return int((target - local).total_seconds())


@app.post('/api/v1/tasks/{task_id}/completion')
def complete(task_id: int, body: Completion, idempotency_key: UUID = Header(), user=Depends(editor)):
    if task_id <= 0:
        raise HTTPException(422, '无效任务编号')
    def apply_completion():
        row = upstream.get(f'/tasks/{task_id}')
        policy = store.preference('repeat:' + str(task_id), {})
        if body.done and policy.get('kind') in {'yearly', 'monthly'}:
            row['repeat_after'] = calendar_interval(row['due_date'], policy)
            row['repeat_mode'] = 0
            # Vikunja advances with the stored interval, so persist this occurrence first.
            row['done'] = False
            row = upstream.request('POST', f'/tasks/{task_id}', body=row)
        row['done'] = body.done
        return upstream.request('POST', f'/tasks/{task_id}', body=row)
    def operation():
        with store.target_lock('task:' + str(task_id)):
            return apply_completion()
    return store.once(idempotency_key, f'task:{task_id}:completion', body.model_dump(), user['username'], operation)


class LinkedTask(Model):
    source: Literal['subscriptions', 'home-inventory']
    source_id: str = Field(min_length=1, max_length=120)


@app.post('/api/v1/linked-task')
def linked_task(body: LinkedTask, user=Depends(editor)):
    try:
        event = next(x for x in source_events(body.source) if str(x['id']) == body.source_id)
    except StopIteration:
        raise HTTPException(404, '来源事项不存在或已停用') from None
    projects = project_lists()
    if not projects:
        raise HTTPException(422, '请先创建一个生活清单')
    key = uuid5(NAMESPACE_URL, f"opscenter:{body.source}:{body.source_id}:{event['date']}")
    return create(Task(title=event['title'], project_id=projects[0]['id'],
                       description='来源：' + body.source + ' / ' + body.source_id,
                       due_date=datetime.fromisoformat(event['date'] + 'T09:00:00+08:00')), key, user)


@app.get('/api/v1/summary')
def overview():
    rows = tasks()
    return summary({'total': len(rows), 'open': sum(not x['done'] for x in rows),
                    'notification': settings(store), 'scheduler': store.preference('scheduler', {})})


@app.get('/api/v1/reminders')
def reminders():
    return deliveries(store)


@app.post('/api/v1/reminders/scan')
def refresh(user=Depends(editor)):
    return scan(store, tasks)


@app.get('/api/v1/notification-settings')
def notification_settings(user=Depends(app.state.require_admin)):
    return settings(store)


class NotificationSettings(Model):
    url: str = Field(default='', max_length=2000)
    enabled: bool = False


@app.put('/api/v1/notification-settings')
def set_notifications(body: NotificationSettings, user=Depends(app.state.require_admin)):
    return save_settings(store, body.model_dump())


class Snooze(Model):
    minutes: int = Field(default=60, ge=5, le=10080)


@app.post('/api/v1/reminders/{delivery_id}/ack')
def ack(delivery_id: str, user=Depends(editor)):
    return acknowledge(store, delivery_id)


@app.post('/api/v1/reminders/{delivery_id}/snooze')
def later(delivery_id: str, body: Snooze, user=Depends(editor)):
    return snooze(store, delivery_id, body.minutes)


stop = threading.Event()


@app.on_event('startup')
def start_scheduler():
    if os.getenv('LIFE_SCHEDULER_ENABLED', 'true') != 'true':
        return
    stop.clear()
    def run():
        while not stop.is_set():
            try:
                scan(store, tasks)
            except Exception:
                try:
                    store.set_preference('scheduler', {'checked_at': timestamp(), 'error': '提醒扫描失败，请检查依赖'})
                except Exception:
                    logging.getLogger(__name__).warning('reminder persistence temporarily unavailable')
            stop.wait(60)
    app.state.scheduler_thread = threading.Thread(target=run, name='life-reminders', daemon=True)
    app.state.scheduler_thread.start()


@app.on_event('shutdown')
def stop_scheduler():
    stop.set()
    if hasattr(app.state, 'scheduler_thread'):
        app.state.scheduler_thread.join(timeout=20)
