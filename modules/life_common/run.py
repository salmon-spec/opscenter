import os
import uvicorn

module = os.environ['LIFE_MODULE']
if module not in {'subscriptions', 'home_inventory', 'life_reminders'}:
    raise RuntimeError('unknown life module')
uvicorn.run(module + '.main:app', host='0.0.0.0', port=8080, access_log=False)
