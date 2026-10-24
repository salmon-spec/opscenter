from datetime import datetime, timezone
from fastapi.testclient import TestClient
from collector import KubernetesReader, KubernetesSnapshot
import main


def test_restart_window_survives_a_poll_without_new_restarts(monkeypatch):
    monkeypatch.setenv('K8S_API_URL','https://kubernetes.invalid')
    monkeypatch.setenv('K8S_TOKEN_FILE','/unused')
    monkeypatch.setenv('K8S_CA_PATH','/unused')
    count = [4]
    def get(self, path):
        if path.endswith('/nodes'):
            return {'items':[{'metadata':{'name':'node'},'status':{'conditions':[{'type':'Ready','status':'True'}]}}]}
        if path.endswith('/pods'):
            return {'items':[{'metadata':{'name':'web','namespace':'test','uid':'pod-id'},'status':{'phase':'Running','containerStatuses':[{'name':'web','restartCount':count[0]}]}}]}
        return {'items':[]}
    monkeypatch.setattr(KubernetesReader,'get',get)
    cache = KubernetesSnapshot()
    cache.refresh()
    restart = lambda:next(row['value'] for row in cache.alert_targets()['items'] if row['metric']=='k3s_restarts')
    assert restart() is None
    count[0] += 2
    cache.refresh()
    assert restart() == 2
    cache.refresh()
    assert restart() == 2


def test_internal_targets_require_the_module_token(monkeypatch, tmp_path):
    path = tmp_path/'token'
    path.write_text('test-internal')
    monkeypatch.setenv('OPS_MODULE_INTERNAL_TOKEN_FILE', str(path))
    monkeypatch.setattr(main.snapshot,'alert_targets',lambda:{'status':'ok','collected_at':datetime.now(timezone.utc),'items':[]})
    client = TestClient(main.app)
    assert client.get('/api/v1/internal/alert-targets').status_code == 401
    assert client.get('/api/v1/internal/alert-targets',headers={'X-OpsCenter-Module-Token':'test-internal'}).status_code == 200
