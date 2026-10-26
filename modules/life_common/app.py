from fastapi import Depends, FastAPI, HTTPException
from runtime.auth import ModuleAuth, SessionMiddleware
from runtime.app import require_internal_service
from .core import Store, timestamp
from .workspace import register_workspace
from .actions import register_actions


def create_app(key, upstream):
    app = FastAPI(title='OpsCenter ' + key, version='0.3.0')
    app.add_middleware(SessionMiddleware)
    store = Store()
    auth = ModuleAuth(Store)
    auth.register(app)
    app.state.store = store
    app.state.upstream = upstream
    register_workspace(app, upstream)
    register_actions(app, upstream)

    @app.on_event('startup')
    def startup():
        store.migrate()

    @app.get('/health/live')
    def live():
        return {'status': 'ok', 'module': key}

    @app.get('/health/ready')
    def ready():
        try:
            with store.connect() as db:
                db.execute('SELECT 1')
            path = {'wallos': '/api/subscriptions/get_subscriptions.php', 'homebox': '/users/self', 'vikunja': '/projects'}[upstream.name]
            upstream.get(path, params={'per_page':1} if upstream.name=='vikunja' else None)
        except Exception:
            raise HTTPException(503, '模块依赖暂不可用') from None
        return {'status': 'ok'}

    @app.get('/api/v1/module')
    def info():
        return {'key': key, 'version': '0.3.0', 'capabilities': ['read', 'write', 'workspace-api'], 'collected_at': timestamp()}

    return app
