"""Render the three life modules. Provision module-specific Secrets before applying."""
import json
import re
import sys
from pathlib import Path
import runpy

native = runpy.run_path(str(Path(__file__).with_name('life_native.py')))

UPSTREAM_IMAGES = {
    'wallos': 'ghcr.io/ellite/wallos@sha256:8873c2328e745c7ca8ff0e0f5508215219ec5c02a3469c0d8049602be2a2bca3',
    'homebox': 'ghcr.io/sysadminsmedia/homebox@sha256:9d0b64776c694f018c0ca06faf52105a33ca376024f46bb58e6ab0e2b728bb61',
    'vikunja': 'ghcr.io/go-vikunja/vikunja@sha256:f7dc8428d10aef17f3075886d3c1c022f566ec456f88985f02c137f4fc534576',
}


def manifests(tag):
    if not re.fullmatch(r'[a-f0-9]{8,40}', tag):
        raise ValueError('an immutable source commit tag is required')
    ns = 'opscenter-life'
    objects = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns}}]
    for key, module, upstream, port, prefix, size in [
        ('subscriptions', 'subscriptions', 'wallos', 80, '', '1Gi'),
        ('home-inventory', 'home_inventory', 'homebox', 7745, '/api/v1', '5Gi'),
        ('life-reminders', 'life_reminders', 'vikunja', 3456, '/api/v1', '2Gi'),
    ]:
        name = 'module-' + key
        http, https = native['PORTS'][key]
        env = {'LIFE_MODULE': module, 'LIFE_UPSTREAM_URL': f'http://127.0.0.1:{port}' + prefix,
               'LIFE_UPSTREAM_TOKEN_FILE': '/run/life/upstream-token', 'MODULE_DATABASE_URL_FILE': '/run/life/db-url',
               'OPS_JWT_PUBLIC_KEY_FILE': '/run/life/jwt-public.pem', 'OPS_MODULE_INTERNAL_TOKEN_FILE': '/run/life/internal-token',
               'LIFE_CREDENTIAL_KEY_FILE': '/run/life/credential-key'}
        env |= {'LIFE_NATIVE_HTTP_ORIGIN': f'http://10.66.66.13:{http}',
                'LIFE_NATIVE_HTTPS_ORIGIN': f'https://10.66.66.13:{https}',
                'LIFE_NATIVE_ORIGIN_ALIASES': json.dumps({'http://10.66.66.15:30088': f'http://10.66.66.15:{http}'}),
                'LIFE_NATIVE_PARENTS': ' '.join(native['PARENTS']), 'LIFE_NATIVE_LOGIN_FILE': '/run/life/native-login'}
        if key == 'life-reminders':
            env |= {'LIFE_SUBSCRIPTIONS_URL': 'http://module-subscriptions:8080',
                    'LIFE_SUBSCRIPTIONS_TOKEN_FILE': '/run/life/subscriptions-token',
                    'LIFE_HOME_INVENTORY_URL': 'http://module-home-inventory:8080',
                    'LIFE_HOME_INVENTORY_TOKEN_FILE': '/run/life/home-inventory-token'}
        adapter = {'name': 'adapter', 'image': 'opscenter/module-' + key + ':' + tag, 'imagePullPolicy': 'Never',
                   'env': [{'name': k, 'value': v} for k, v in env.items()] + [
                       {'name': 'OPS_ADMIN_USER', 'valueFrom': {'secretKeyRef': {'name': name, 'key': 'admin-user'}}}],
                   'securityContext': {'runAsUser': 10001, 'runAsGroup': 10001, 'allowPrivilegeEscalation': False,
                                       'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                   'resources': {'requests': {'cpu': '25m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}},
                   'volumeMounts': [{'name': 'secrets', 'mountPath': '/run/life', 'readOnly': True}, {'name': 'tmp', 'mountPath': '/tmp'}],
                   'readinessProbe': {'httpGet': {'path': '/health/ready', 'port': 8080}, 'initialDelaySeconds': 8, 'periodSeconds': 15, 'timeoutSeconds': 20},
                   'livenessProbe': {'httpGet': {'path': '/health/live', 'port': 8080}, 'initialDelaySeconds': 60, 'periodSeconds': 30}}
        up_env = {'TZ': 'Asia/Shanghai'}
        secret_env = {}
        if upstream == 'wallos':
            mounts = [('/var/www/html/db', 'db'), ('/var/www/html/images/uploads/logos', 'logos')]
        elif upstream == 'homebox':
            up_env |= {'HBOX_OPTIONS_ALLOW_REGISTRATION': 'true', 'HBOX_OPTIONS_TRUST_PROXY': 'true', 'HBOX_LOG_LEVEL': 'warn'}
            secret_env = {'HBOX_AUTH_API_KEY_PEPPER': 'homebox-pepper'}
            mounts = [('/data', 'data')]
        else:
            up_env |= {'VIKUNJA_DATABASE_TYPE': 'sqlite', 'VIKUNJA_DATABASE_PATH': '/db/vikunja.db',
                       'VIKUNJA_SERVICE_ENABLEREGISTRATION': 'true', 'VIKUNJA_SERVICE_PUBLICURL': f'http://10.66.66.13:{http}/',
                       'VIKUNJA_CORS_ENABLE': 'false', 'VIKUNJA_SERVICE_TIMEZONE': 'Asia/Shanghai',
                       'VIKUNJA_MAILER_ENABLED': 'false'}
            secret_env = {'VIKUNJA_SERVICE_JWTSECRET': 'vikunja-jwt'}
            mounts = [('/db', 'db'), ('/app/vikunja/files', 'files')]
        up = {'name': upstream, 'image': UPSTREAM_IMAGES[upstream],
              'env': [{'name': k, 'value': v} for k, v in up_env.items()] + [
                  {'name': k, 'valueFrom': {'secretKeyRef': {'name': name, 'key': v}}} for k, v in secret_env.items()],
              'resources': {'requests': {'cpu': '25m', 'memory': '128Mi'}, 'limits': {'cpu': '500m', 'memory': '512Mi'}},
              'volumeMounts': [{'name': 'data', 'mountPath': path, 'subPath': sub} for path, sub in mounts],
              'readinessProbe': {'tcpSocket': {'port': port}, 'initialDelaySeconds': 8, 'periodSeconds': 10},
              'securityContext': {'allowPrivilegeEscalation': False}}
        init = []
        proxy = {'name': 'native-gateway', 'image': 'opscenter/frontend:' + tag, 'imagePullPolicy': 'Never',
                 'command': ['caddy', 'run', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'],
                 'env': [{'name': 'XDG_CONFIG_HOME', 'value': '/tmp'}, {'name': 'XDG_DATA_HOME', 'value': '/tmp'}],
                 'securityContext': {'runAsUser': 10001, 'runAsGroup': 10001, 'allowPrivilegeEscalation': False,
                                     'readOnlyRootFilesystem': True,
                                     'capabilities': {'drop': ['ALL'], 'add': ['NET_BIND_SERVICE']}},
                 'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '500m', 'memory': '128Mi'}},
                 'volumeMounts': [{'name': 'native-config', 'mountPath': '/etc/caddy', 'readOnly': True},
                                  {'name': 'native-tls', 'mountPath': '/run/native-tls', 'readOnly': True}, {'name': 'tmp', 'mountPath': '/tmp'}],
                 'readinessProbe': {'tcpSocket': {'port': 8082}, 'periodSeconds': 10}}
        if upstream == 'vikunja':
            init = [{'name': 'prepare-data', 'image': UPSTREAM_IMAGES['wallos'],
                     'command': ['sh', '-ec', 'mkdir -p /work/db /work/files; chown 1000:1000 /work/db /work/files'],
                     'volumeMounts': [{'name': 'data', 'mountPath': '/work'}],
                     'resources': {'requests': {'cpu': '10m', 'memory': '16Mi'}, 'limits': {'cpu': '100m', 'memory': '64Mi'}}}]
        objects.extend([
            {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {'name': name, 'namespace': ns},
             'spec': {'accessModes': ['ReadWriteOnce'], 'storageClassName': 'local-path', 'resources': {'requests': {'storage': size}}}},
            {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': name, 'namespace': ns},
             'spec': {'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': {'app': name}},
                      'template': {'metadata': {'labels': {'app': name}}, 'spec': {
                          'nodeSelector': {'kubernetes.io/hostname': 'k8s-worker-03'}, 'automountServiceAccountToken': False,
                          'securityContext': {'fsGroup': 1000}, 'initContainers': init, 'containers': [adapter, up, proxy],
                          'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': name}},
                                      {'name': 'secrets', 'secret': {'secretName': name, 'defaultMode': 292}},
                                      {'name': 'native-config', 'configMap': {'name': name + '-native'}},
                                      {'name': 'native-tls', 'secret': {'secretName': 'life-native-tls', 'defaultMode': 292}},
                                      {'name': 'tmp', 'emptyDir': {'sizeLimit': '128Mi'}}]}}}},
            {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': name, 'namespace': ns},
             'spec': {'selector': {'app': name}, 'ports': [{'name': 'http', 'port': 8080}]}},
            {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': name, 'namespace': 'opscenter'},
             'spec': {'type': 'ExternalName', 'externalName': name + '.' + ns + '.svc.cluster.local'}},
        ])
        objects.extend(native['resources'](key, tag, port))
    objects += [
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': {'name': 'life-boundary', 'namespace': ns}, 'spec': {
            'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'],
            'ingress': [{'from': [{'podSelector': {}}, {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'opscenter'}}, 'podSelector': {'matchExpressions': [{'key': 'app', 'operator': 'In', 'values': ['web', 'backend']}]}}], 'ports': [{'protocol': 'TCP', 'port': 8080}]},
                        {'ports': [{'protocol': 'TCP', 'port': 8082}, {'protocol': 'TCP', 'port': 8443}]}],
            'egress': [
                {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}}}], 'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]},
                {'to': [{'podSelector': {}}], 'ports': [{'protocol': 'TCP', 'port': 8080}]},
                {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'opscenter'}}, 'podSelector': {'matchLabels': {'app': 'db'}}}], 'ports': [{'protocol': 'TCP', 'port': 5432}]},
                {'to': [{'ipBlock': {'cidr': '0.0.0.0/0', 'except': ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '169.254.0.0/16']}}], 'ports': [{'protocol': 'TCP', 'port': 443}]},
            ]}},
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': {'name': 'life-postgres', 'namespace': 'opscenter'}, 'spec': {
            'podSelector': {'matchLabels': {'app': 'db'}}, 'policyTypes': ['Ingress'],
            'ingress': [{'from': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': ns}}}], 'ports': [{'protocol': 'TCP', 'port': 5432}]}]}},
    ]
    return {'apiVersion': 'v1', 'kind': 'List', 'items': objects}


if __name__ == '__main__':
    print(json.dumps(manifests(sys.argv[1]), ensure_ascii=False, indent=2))
