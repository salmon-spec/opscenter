"""Exercise the actual Caddy image against an isolated mock upstream."""
import json
import re
import subprocess
import sys

tag = sys.argv[1]
assert re.fullmatch('[0-9a-f]{8}', tag)
network = 'opscenter-ci-life-' + tag
mock = network + '-upstream'
web = network + '-web'


def docker(*args):
    return subprocess.check_output(['docker', *args], stderr=subprocess.STDOUT, timeout=45)


server = '''from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer as HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def do_GET(self):
  body=json.dumps({'path':self.path,'cookie':self.headers.get('Cookie','')}).encode()
  self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.send_header('X-Frame-Options','DENY');self.send_header('Set-Cookie','PHPSESSID=server; HttpOnly; Path=/');self.end_headers();self.wfile.write(body)
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
finally:
    if created:
        subprocess.run(['docker', 'rm', '-f', web, mock], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        docker('network', 'rm', network)
