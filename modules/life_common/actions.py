from uuid import UUID

from fastapi import Depends, Header, HTTPException
from pydantic import Field

from .core import Model
from .workspace import identifier, revision


class RecordAction(Model):
    id: str = Field(max_length=40)
    expected_revision: str = Field(max_length=80)


def register_actions(app, upstream):
    store=app.state.store

    @app.post('/api/v1/record-actions/{action}')
    def apply(action: str,body: RecordAction,idempotency_key: UUID=Header(),user=Depends(app.state.require_editor)):
        if action not in {'delete','duplicate'} or (action=='duplicate' and upstream.name!='homebox'):
            raise HTTPException(404,'未提供此记录操作')
        item_id=identifier(body.id,'uuid' if upstream.name=='homebox' else 'number')
        def operation():
            with store.target_lock({'wallos':'subscription:','homebox':'item:','vikunja':'task:'}[upstream.name]+item_id):
                if upstream.name=='wallos':
                    rows=upstream.get('/api/subscriptions/get_subscriptions.php')['subscriptions']
                    current=next((r for r in rows if str(r['id'])==item_id),None)
                else:
                    path=('/entities/' if upstream.name=='homebox' else '/tasks/')+item_id
                    current=upstream.get(path)
                if not current:
                    raise HTTPException(404,'记录已不存在')
                if not body.expected_revision or revision(current)!=body.expected_revision:
                    raise HTTPException(409,'记录已更新，请刷新后重试')
                if upstream.name=='wallos':
                    return upstream.request('POST','/api/subscriptions/set_subscriptions.php',form={'action':'delete','id':item_id})
                if action=='duplicate':
                    return upstream.request('POST',path+'/duplicate',body={'copyMaintenance':True,'copyAttachments':True,'copyCustomFields':True,'copyPrefix':'副本'})
                upstream.request('DELETE',path)
                return {'ok':True}
        return store.once(idempotency_key,'record:'+action,body.model_dump(),user['username'],operation)
