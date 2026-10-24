import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from storage import AlertsStore
import main


def test_concurrent_alert_mutations_read_the_locked_latest_snapshot(monkeypatch):
    state = {name: [] for name in ('rules', 'events', 'silences', 'policies', 'deliveries', 'notes')}
    state['meta'] = {}
    guard = threading.Lock()
    barrier = threading.Barrier(2)

    class Connection:
        def __init__(self): self.locked = False
        def __enter__(self): return self
        def __exit__(self, *args):
            if self.locked: guard.release()
        def execute(self, sql, args=None):
            if 'pg_advisory_xact_lock' in sql:
                guard.acquire()
                self.locked = True
            return SimpleNamespace(fetchone=lambda:None)
    store = AlertsStore()
    monkeypatch.setattr(store, 'connect', Connection)
    monkeypatch.setattr(store, '_read', lambda db:(copy.deepcopy(state), None))
    def put(db, name, row):
        state[name] = [value for value in state[name] if value['id'] != row['id']] + [copy.deepcopy(row)]
    monkeypatch.setattr(store, '_put', put)
    monkeypatch.setattr(main, '_store', lambda:store)
    def snapshot(**kwargs):
        old = copy.deepcopy(state)
        barrier.wait(timeout=5)
        return old, None, None
    monkeypatch.setattr(main, '_snapshot', snapshot)
    def create(name):
        main._mutate('admin', 'create', name, lambda data:data['rules'].append({'id':name}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(create, ['rule-one','rule-two']))
    assert {row['id'] for row in state['rules']} == {'rule-one', 'rule-two'}
