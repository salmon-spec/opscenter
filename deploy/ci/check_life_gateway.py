"""Exercise the actual Caddy image against an isolated mock upstream."""
import json
import re
import subprocess
import sys
import runpy
import tempfile
from pathlib import Path

tag = sys.argv[1]
assert re.fullmatch('[0-9a-f]{8}', tag)
network = 'opscenter-ci-life-' + tag
mock = network + '-upstream'
web = network + '-web'
native_web = network + '-native'


def docker(*args):
    return subprocess.check_output(['docker', *args], stderr=subprocess.STDOUT, timeout=45)


server = '''from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer as HTTPServer
import json,threading,base64,hashlib
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def do_GET(self):
  if self.path=='/_ops/verify':
   self.send_response(403 if self.headers.get('Upgrade') else 204 if self.headers.get('Cookie')=='test-gate=valid' else 401);self.send_header('X-Native-Cookie','native-session=private');self.send_header('Content-Length','0');self.end_headers();return
  if self.headers.get('Upgrade','').lower()=='websocket':
   assert self.headers.get('Cookie')=='native-session=private'
   accept=base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
   self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers();self.wfile.flush();self.rfile.read(2);self.wfile.write(bytes([136,2,3,232]));self.wfile.flush();self.close_connection=True;return
  body=json.dumps({'path':self.path,'cookie':self.headers.get('Cookie','')}).encode()
  self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.send_header('X-Frame-Options','DENY');self.send_header('Set-Cookie','PHPSESSID=server; HttpOnly; Path=/');self.end_headers();self.wfile.write(body)
threading.Thread(target=HTTPServer(('0.0.0.0',8085),Handler).serve_forever,daemon=True).start()
HTTPServer(('0.0.0.0',8080),Handler).serve_forever()
'''
client = '''import requests,time
s=requests.Session();s.trust_env=False
for attempt in range(30):
 try:
  r=s.get('http://life-web:80/v3/',timeout=2)
  if r.status_code==200:break
 except requests.RequestException:pass
 time.sleep(.2)
else:raise AssertionError('Caddy did not start')
for key in ['subscriptions','home-inventory','life-reminders']:
 r=s.get('http://life-web/modules/'+key+'/api/v1/module',timeout=4)
 assert r.status_code==200,(key,r.status_code)
 assert r.json()['path']=='/api/v1/module'
 for path in ['/health/ready','/api/v1/internal/due']:
  r=s.get('http://life-web/modules/'+key+path,timeout=4);assert r.status_code==404,(key,path,r.status_code)
print('9 life gateway route checks passed')
'''
native_client = '''import requests,time,ssl
from websockets.sync.client import connect
s=requests.Session();s.trust_env=False
for scheme,port in [('http',8082),('https',8443)]:
 base=scheme+'://life-native-web:'+str(port)
 for attempt in range(30):
  try:r=s.get(base+'/',verify='/run/test/tls.crt',timeout=2);break
  except requests.RequestException:time.sleep(.2)
 else:raise AssertionError('native Caddy did not start')
 assert r.status_code==401
 for path in ['/','/assets/file.js','/api/v2/projects']:
  r=s.get(base+path,headers={'Cookie':'test-gate=valid'},verify='/run/test/tls.crt',timeout=3)
  assert r.status_code==200 and r.json()['path']==path
  assert r.json()['cookie']=='native-session=private'
  assert r.headers['Set-Cookie'].startswith(('tls_' if scheme=='https' else 'http_')+'PHPSESSID=')
  assert 'X-Frame-Options' not in r.headers
  assert 'frame-ancestors http://10.66.66.13:30088 https://10.66.66.13' in r.headers['Content-Security-Policy']
 with connect(base.replace('http','ws',1)+'/ws',additional_headers={'Cookie':'test-gate=valid'},open_timeout=4,close_timeout=.2,**({'ssl':ssl.create_default_context(cafile='/run/test/tls.crt')} if scheme=='https' else {})) as ws:
  assert ws.state.name=='OPEN'
print('10 native gateway route checks passed')
'''
created = False
try:
    docker('network', 'create', '--internal', network)
    created = True
    aliases = []
    for key in ['subscriptions', 'home-inventory', 'life-reminders']:
        aliases += ['--network-alias', 'module-' + key + '.opscenter-life.svc']
    docker('run', '-d', '--rm', '--security-opt', 'apparmor=unconfined', '--name', mock,
           '--network', network, *aliases, 'opscenter/backend:' + tag, 'python', '-c', server)
    docker('run', '-d', '--rm', '--security-opt', 'apparmor=unconfined', '--name', web,
           '--network', network, '--network-alias', 'life-web', 'opscenter/frontend:' + tag)
    print(docker('run', '--rm', '--security-opt', 'apparmor=unconfined', '--network', network,
                 'opscenter/backend:' + tag, 'python', '-c', client).decode().strip())
    with tempfile.TemporaryDirectory(prefix='life-native-ci-') as directory:
        root = Path(directory)
        root.chmod(0o755)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=life-native-web', '-addext', 'subjectAltName=DNS:life-native-web',
                        '-keyout', str(root/'tls.key'), '-out', str(root/'tls.crt')], check=True, capture_output=True)
        config = runpy.run_path(str(Path(__file__).resolve().parents[1]/'k8s/life_native.py'))['gateway'](8085)
        config = config.replace('127.0.0.1:', mock + ':')
        (root/'Caddyfile').write_text(config)
        for file in root.iterdir():
            file.chmod(0o644)
        docker('run', '-d', '--rm', '--security-opt', 'apparmor=unconfined', '--name', native_web,
               '--user', '10001:10001', '--cap-drop=ALL', '--cap-add=NET_BIND_SERVICE',
               '--security-opt', 'no-new-privileges', '--read-only', '--tmpfs', '/tmp',
               '-e', 'XDG_CONFIG_HOME=/tmp', '-e', 'XDG_DATA_HOME=/tmp',
               '--network', network, '--network-alias', 'life-native-web',
               '-v', directory + ':/run/native-tls:ro', '-v', str(root/'Caddyfile') + ':/etc/caddy/Caddyfile:ro',
               'opscenter/frontend:' + tag)
        print(docker('run', '--rm', '--security-opt', 'apparmor=unconfined', '--network', network,
                     '-v', directory + ':/run/test:ro', 'opscenter/backend:' + tag,
                     'python', '-c', native_client).decode().strip())
finally:
    if created:
        subprocess.run(['docker', 'rm', '-f', native_web, web, mock], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        docker('network', 'rm', network)
