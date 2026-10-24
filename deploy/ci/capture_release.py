"""Capture the actual K3s release, including source overrides, for a verified rollback.

Run on the cluster administrator host: python3 capture_release.py /protected/new-directory
Keep database/key backups separately. Base images must remain available on their nodes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def capture(destination):
    os.umask(0o077)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    files = {}
    def save(name, data):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()
    def kube(*args):
        return subprocess.run(['kubectl', '-n', 'opscenter', *args], check=True,
                              capture_output=True, timeout=120).stdout
    def clean(resource):
        return {key: value for key, value in resource.items() if key in {'apiVersion', 'kind', 'spec', 'data', 'binaryData', 'immutable'}} | {
            'metadata': {key: resource['metadata'][key] for key in ['name', 'namespace', 'labels', 'annotations'] if key in resource['metadata']}}
    deployments = json.loads(kube('get', 'deploy', '-o', 'json'))['items']
    pods = json.loads(kube('get', 'pods', '-o', 'json'))['items']
    configmaps = set()
    for deployment in deployments:
        name = deployment['metadata']['name']
        spec = deployment['spec']['template']['spec']
        configmaps.update(v['configMap']['name'] for v in spec.get('volumes', []) if 'configMap' in v)
        if name == 'web':
            roots = ['srv/v3']
        elif name == 'backend' or name in {'module-oss-browser', 'module-command-code-usage'}:
            roots = ['opt/opscenter/backend/app']
        elif name == 'module-automation':
            roots = ['app/modules/automation', 'app/runtime']
        else:
            roots = ['opt/module']
        save('sources/' + name + '.tar.gz', kube('exec', 'deployment/' + name, '-c', spec['containers'][0]['name'],
             '--', 'tar', '-C', '/', '-czf', '-', *roots))
    save('deployments.json', json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': [clean(d) for d in deployments]}).encode())
    save('configmaps.json', json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': [clean(json.loads(kube('get', 'configmap', name, '-o', 'json'))) for name in sorted(configmaps)]}).encode())
    images = {pod['metadata']['name']: {c['name']: c.get('imageID') for c in pod.get('status', {}).get('containerStatuses', [])} for pod in pods}
    save('images.json', json.dumps(images, indent=2).encode())
    save('restore.py', b'''import json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
subprocess.run(['kubectl','-n','opscenter','apply','-f',str(root/'configmaps.json')],check=True)
for deployment in json.loads((root/'deployments.json').read_text())['items']:
 name=deployment['metadata']['name']
 live=json.loads(subprocess.check_output(['kubectl','-n','opscenter','get','deployment',name,'-o','json']))
 deployment['metadata']['resourceVersion']=live['metadata']['resourceVersion']
 subprocess.run(['kubectl','-n','opscenter','replace','-f','-'],input=json.dumps(deployment).encode(),check=True)
 subprocess.run(['kubectl','-n','opscenter','rollout','status','deployment/'+name,'--timeout=180s'],check=True)
''')
    save('restore.sh', b'#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\nsha256sum -c SHA256SUMS\npython3 restore.py\n')
    for name, digest in files.items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest
    (destination / 'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name, digest in sorted(files.items())))
    print(json.dumps({'release_directory': str(destination), 'deployments': len(deployments), 'configmaps': len(configmaps), 'verified_files': len(files)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    capture(parser.parse_args().destination.resolve())
