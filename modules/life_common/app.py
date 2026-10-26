from fastapi import Depends, FastAPI, HTTPException
from runtime.auth import ModuleAuth, SessionMiddleware
from runtime.app import require_internal_service
from .core import Store, timestamp
from .native import register_native


def create_app(key, upstream):
    app = FastAPI(title='OpsCenter ' + key, version='0.2.0')
    app.add_middleware(SessionMiddleware)
    store = Store()
    auth = ModuleAuth(Store)
    auth.register(app)
    app.state.store = store
    app.state.upstream = upstream
    register_native(app, key, upstream)

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
            path = {'wallos': '/api/status/version.php', 'homebox': '/status', 'vikunja': '/info'}[upstream.name]
            upstream.get(path)
        except Exception:
            raise HTTPException(503, '模块依赖暂不可用') from None
        return {'status': 'ok'}

    @app.get('/api/v1/module')
    def info():
        return {'key': key, 'version': '0.2.0', 'capabilities': ['read', 'write', 'native-ui'], 'collected_at': timestamp()}

    return app
