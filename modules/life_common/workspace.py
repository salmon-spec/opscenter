"""Named business operations for the workbench; upstream credentials stay server-side."""
import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query
from pydantic import Field, ValidationError, create_model

from .core import Model


def field(key, label, type='text', required=False, default='', **kwargs):
    return dict(key=key, label=label, type=type, required=required, default=default, **kwargs)


NAME = field('name', '名称', required=True)
TITLE = field('title', '名称', required=True)
DESCRIPTION = field('description', '说明', 'textarea')
COLOR = field('hex_color', '颜色（六位色值）', default='2563eb', pattern='[0-9a-fA-F]{6}')


def resources(name):
    if name == 'wallos':
        result = []
        for key, title, fields in [
            ('categories', '订阅分类', [NAME]),
            ('payment_methods', '付款方式', [NAME, field('enabled', '启用', 'checkbox', default=True)]),
            ('household', '家庭成员', [NAME, field('email', '邮箱', 'email')]),
            ('currencies', '币种与汇率', [NAME, field('code', '币种代码', required=True), field('symbol', '符号', required=True), field('rate', '汇率', 'number', True, 1, min=0.000001)]),
        ]:
            result.append(dict(key=key, title=title, path=f'/api/{key}/get_{key}.php', write=f'/api/{key}/set_{key}.php', root=key, fields=fields))
        result.append(dict(key='budget', title='支出预算', path='/api/users/get_user.php', write='/api/users/set_budget.php', root='user', singleton=True,
                           fields=[field('budget', '每月预算', 'number', True, 0, min=0), field('period_budget', '周期预算', 'number', True, 0, min=0),
                                   field('budget_period_type', '预算周期', 'select', True, 'monthly', options=[['weekly','每周'],['fortnightly','每两周'],['monthly','每月']]),
                                   field('budget_period_anchor_date', '周期起始日期', 'date')]))
        return result
    if name == 'homebox':
        return [
            dict(key='tags', title='分类标签', path='/tags', fields=[NAME, DESCRIPTION, field('color','颜色（六位色值）',default='2563eb'),field('icon','图标')]),
            dict(key='entity-types', title='物品类型', path='/entity-types', fields=[NAME, field('icon','图标',default='mdi:package-variant'),field('isLocation','作为存放位置','checkbox',default=False),field('defaultTemplateId','默认模板','reference',source='templates')]),
            dict(key='templates', title='物品模板', path='/templates', detail=True, fields=[NAME, DESCRIPTION, field('notes','备注','textarea'), field('defaultName','默认物品名称'),field('defaultManufacturer','默认品牌'),field('defaultModelNumber','默认型号'),
                field('defaultQuantity','默认数量','number',True,1,min=1),field('defaultInsured','默认已投保','checkbox',default=False),field('defaultLifetimeWarranty','默认终身保修','checkbox',default=False),
                field('includePurchaseFields','包含购买信息','checkbox',default=True),field('includeWarrantyFields','包含保修信息','checkbox',default=True),field('includeSoldFields','包含转售信息','checkbox',default=False)]),
            dict(key='maintenance',title='维护计划与记录',path='/maintenance',params={'status':'both'},create=False,fields=[NAME,DESCRIPTION,field('cost','费用','number',True,0,min=0),field('scheduledDate','计划日期','datetime-local'),field('completedDate','完成日期','datetime-local')]),
            dict(key='group',title='家庭集合设置',path='/groups',singleton=True,fields=[NAME,field('currency','记账币种',required=True,default='CNY')]),
            dict(key='members',title='集合成员',path='/groups/members',readonly=True,fields=[NAME,field('email','邮箱')]),
        ]
    return [
        dict(key='projects',title='项目与清单',path='/projects',detail=True,fields=[TITLE,DESCRIPTION,COLOR,field('parent_project_id','上级项目','reference',source='projects',numeric=True),field('is_archived','归档','checkbox',default=False),field('is_favorite','收藏','checkbox',default=False)]),
        dict(key='labels',title='任务标签',path='/labels',detail=True,fields=[TITLE,DESCRIPTION,COLOR]),
        dict(key='teams',title='协作团队',path='/teams',detail=True,fields=[NAME,DESCRIPTION]),
        dict(key='comments',title='任务评论',path='/tasks/{scope}/comments',scope='tasks',scopeLabel='选择任务',fields=[field('comment','评论','textarea',True)]),
        dict(key='assignees',title='任务负责人',path='/tasks/{scope}/assignees',scope='tasks',scopeLabel='选择任务',edit=False,fields=[field('user_id','负责人','reference',True,source='users',numeric=True)],idField='id'),
        dict(key='project-teams',title='项目团队权限',path='/projects/{scope}/teams',scope='projects',scopeLabel='选择项目',idField='team_id',fields=[field('team_id','团队','reference',True,source='teams',numeric=True,immutable=True),field('permission','权限','select',True,0,numeric=True,options=[[0,'只读'],[1,'可编辑'],[2,'管理员']])]),
        dict(key='project-users',title='项目成员权限',path='/projects/{scope}/users',scope='projects',scopeLabel='选择项目',idField='username',idKind='username',fields=[field('username','成员','select',True,source='users',valueKey='username',immutable=True),field('permission','权限','select',True,0,numeric=True,options=[[0,'只读'],[1,'可编辑'],[2,'管理员']])]),
        dict(key='views',title='项目视图',path='/projects/{scope}/views',scope='projects',scopeLabel='选择项目',detail=True,fields=[TITLE,field('view_kind','视图类型','select',True,'list',options=[['list','列表'],['gantt','甘特'],['table','表格'],['kanban','看板']])]),
        dict(key='users',title='协作用户',path='/users',readonly=True,fields=[field('username','用户名'),field('name','显示名称')]),
    ]


def identifier(value, kind='number'):
    try:
        if kind=='username':
            if str(value) in {'.','..'} or not re.fullmatch(r'[\w.@+-]{1,64}',str(value)):
                raise ValueError()
            return str(value)
        if kind == 'uuid':
            return str(UUID(str(value)))
        number = int(str(value))
        if number <= 0 or not re.fullmatch('[1-9][0-9]*', str(value)):
            raise ValueError()
        return str(number)
    except (ValueError, TypeError):
        raise HTTPException(422, '请选择有效记录') from None


def validate_fields(spec, values):
    definitions = {}
    for f in spec['fields']:
        typ = bool if f['type'] == 'checkbox' else float if f['type'] == 'number' else int if f.get('numeric') and f['type']!='reference' else str
        options = f.get('options')
        if options:
            typ = Literal[tuple(x[0] for x in options)]
        constraints = {'ge': f.get('min', 0), 'le': 100000000} if f['type'] == 'number' else {}
        if typ is str:
            constraints |= {'min_length': 1 if f['required'] else 0, 'max_length': 4000 if f['type']=='textarea' else 255}
            if f.get('pattern'):
                constraints['pattern'] = '^' + f['pattern'] + '$'
        definitions[f['key']] = (typ, Field(default=... if f['required'] else f['default'], **constraints))
    try:
        result = create_model('WorkspaceFields', __base__=Model, **definitions)(**values).model_dump()
    except ValidationError:
        raise HTTPException(422, '字段缺失或格式无效，请检查表单') from None
    for f in spec['fields']:
        v = result[f['key']]
        if f['type'] == 'reference':
            if not v:
                result[f['key']] = 0 if f.get('numeric') else None
            else:
                result[f['key']] = int(identifier(v)) if f.get('numeric') else identifier(v, 'uuid')
        if f['type'] in {'date', 'datetime-local'} and v:
            try:
                datetime.fromisoformat(v.replace('Z', '+00:00'))
            except ValueError:
                raise HTTPException(422, '日期格式无效') from None
    return result


class Change(Model):
    operation: Literal['create', 'update', 'delete']
    id: str = Field(default='', max_length=80)
    scope: str = Field(default='', max_length=40)
    values: dict[str, Any] = Field(default_factory=dict)
    expected_revision: str = Field(default='', max_length=80)


def revision(row):
    return str(row.get('updatedAt') or row.get('updated') or hashlib.sha256(json.dumps(row,sort_keys=True,default=str).encode()).hexdigest())


def resource_revision(spec,row):
    if spec['key'] in {'project-users','project-teams'}:
        return hashlib.sha256(json.dumps(row,sort_keys=True,default=str).encode()).hexdigest()
    return revision(row)


def register_workspace(app, upstream):
    specs = {s['key']: s for s in resources(upstream.name)}
    store = app.state.store

    def spec_for(key):
        if key not in specs:
            raise HTTPException(404, '未提供此业务功能')
        return specs[key]

    def path_for(spec, scope):
        return spec['path'].replace('{scope}', identifier(scope)) if spec.get('scope') else spec['path']

    def read(spec, scope=''):
        path = path_for(spec, scope)
        if upstream.name == 'vikunja':
            result = []
            for page in range(1, 51):
                batch = upstream.get(path,params={'page':page,'per_page':100}) or []
                result.extend(batch)
                if len(batch)<100:
                    return result
            raise HTTPException(422, '记录超过5000条，请归档后重试')
        data = upstream.get(path,params=spec.get('params'))
        if spec.get('root'):
            data = data[spec['root']]
        return [data] if spec.get('singleton') else data or []

    @app.get('/api/v1/workspace/catalog')
    def catalog():
        return {'resources': [{k:v for k,v in s.items() if k in {'key','title','fields','scope','scopeLabel','readonly','create','edit','singleton','idField'}} for s in specs.values()]}

    @app.get('/api/v1/workspace/{key}')
    def listing(key: str, scope: str = Query(default='',max_length=40)):
        spec = spec_for(key)
        items = []
        for row in read(spec,scope):
            data = {f['key']:row.get(f['key'], f['default']) for f in spec['fields']}
            data |= {'id':str(row.get(spec.get('idField','id')) or row.get('id') or '1'),'revision':resource_revision(spec,row)}
            if spec.get('idField'):
                data[spec['idField']] = row.get(spec['idField']) or row.get('id')
            if spec.get('detail'):
                # Summary responses may omit fields; fetch the original record when editing.
                data['summary'] = True
            items.append(data)
        return {'items':items}

    @app.get('/api/v1/workspace/{key}/{item_id}')
    def detail(key: str,item_id: str,scope: str = ''):
        spec = spec_for(key)
        item_id = identifier(item_id,spec.get('idKind') or ('uuid' if upstream.name=='homebox' else 'number'))
        if not spec.get('detail'):
            raise HTTPException(404,'此功能没有详情')
        row = upstream.get(path_for(spec,scope)+'/'+item_id)
        return {'id':item_id,'revision':resource_revision(spec,row),'values':{f['key']:row.get(f['key'],f['default']) for f in spec['fields']}}

    @app.post('/api/v1/workspace/{key}')
    def change(key: str,body: Change,idempotency_key: UUID = Header(),user=Depends(app.state.require_editor)):
        spec = spec_for(key)
        op = body.operation
        if spec.get('readonly') or (op=='create' and (spec.get('singleton') or spec.get('create') is False)) or (op=='update' and spec.get('edit') is False) or (op=='delete' and spec.get('singleton')):
            raise HTTPException(405,'此功能不提供该操作')
        item_id = identifier(body.id,spec.get('idKind') or ('uuid' if upstream.name=='homebox' else 'number')) if op!='create' else ''
        values = validate_fields(spec,body.values) if op!='delete' else {}
        path = path_for(spec,body.scope)
        def operation():
            with store.target_lock('workspace:'+key+':'+body.scope+':'+item_id):
                current = None
                if op!='create':
                    if spec.get('detail'):
                        current=upstream.get(path+'/'+item_id)
                    else:
                        current=next((r for r in read(spec,body.scope) if str(r.get(spec.get('idField','id')) or r.get('id') or '1')==item_id),None)
                    if current is None:
                        raise HTTPException(404,'记录已不存在，请刷新')
                    if not body.expected_revision or resource_revision(spec,current)!=body.expected_revision:
                        raise HTTPException(409,'记录已更新，请刷新后重试')
                if op=='update' and any(f.get('immutable') and str(values[f['key']])!=str(current.get(f['key'],current.get('id'))) for f in spec['fields']):
                    raise HTTPException(422,'已有成员不可替换，请新增对应成员后调整权限')
                if upstream.name=='wallos':
                    form={k:int(v) if isinstance(v,bool) else v for k,v in values.items()}
                    if key=='budget' and not form.get('budget_period_anchor_date'):
                        form.pop('budget_period_anchor_date',None)
                    if not spec.get('singleton'):
                        form |= {'action':{'create':'add','update':'edit','delete':'delete'}[op],'id':item_id}
                    return upstream.request('POST',spec['write'],form=form)
                target=path if op=='create' or spec.get('singleton') else path+'/'+item_id
                method='DELETE' if op=='delete' else ('PUT' if op=='create' else 'POST') if upstream.name=='vikunja' else ('POST' if op=='create' else 'PUT')
                payload=(current or {}) | values
                if upstream.name=='homebox' and key=='maintenance':
                    payload['cost']=str(values['cost'])
                if upstream.name=='homebox' and key=='templates':
                    payload.setdefault('fields',[])
                    if current:
                        payload['defaultLocationId']=(current.get('defaultLocation') or {}).get('id')
                        payload['defaultTagIds']=[t['id'] for t in current.get('defaultTags') or []]
                result=upstream.request(method,target,body=payload if op!='delete' else None)
                return {'ok':True,'id':(result or {}).get('id',item_id)}
        return store.once(idempotency_key,'workspace:'+key+':'+op,body.model_dump(mode='json'),user['username'],operation)
