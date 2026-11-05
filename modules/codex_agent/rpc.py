"""One private stdio connection to the official Codex app-server."""
import collections
import json
import os
import queue
import subprocess
import threading
from pathlib import Path

from fastapi import HTTPException


class AppServer:
    def __init__(self):
        self.lock = threading.RLock()
        self.writer = threading.Lock()
        self.start_lock = threading.Lock()
        self.pending = {}
        self.approvals = {}
        self.active_turns = {}
        self.active_calls = {}
        self.events = collections.deque(maxlen=1500)
        self.cursor = 0
        self.sequence = 0
        self.process = None
        self.generation = 0
        self.initialized = False

    def start(self):
        with self.start_lock:
            if self.process and self.process.poll() is None and self.initialized:
                return
            if self.process and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            self.initialized = False
            self.generation += 1
            self.approvals.clear()
            self.active_turns.clear()
            self.active_calls.clear()
            env = os.environ.copy()
            home = Path(env.get('CODEX_HOME', '/state/codex'))
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
            secret = os.getenv('CODEX_API_KEY_FILE', '')
            if secret:
                env['CODEX_PROVIDER_KEY'] = Path(secret).read_text().strip()
            args = [os.getenv('CODEX_BIN', 'codex'), 'app-server', '--listen', 'stdio://']
            args.extend(['-c','features.multi_agent=false'])
            base = os.getenv('CODEX_API_BASE', '')
            if base:
                for value in ['model_provider="opscenter"', 'model_providers.opscenter.name="Existing Agent API"',
                              'model_providers.opscenter.base_url="http://127.0.0.1:8080/provider/v1"',
                              'model_providers.opscenter.env_key="CODEX_PROVIDER_KEY"',
                              'model_providers.opscenter.wire_api="responses"',
                              'model_providers.opscenter.supports_websockets=false']:
                    args.extend(['-c', value])
                args.extend(['-c','web_search="disabled"'])
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                                            env=env, bufsize=1)
            threading.Thread(target=self.read, args=(self.process,), daemon=True).start()
            self.request('initialize', {'clientInfo': {'name':'opscenter_codex', 'title':'OpsCenter Codex', 'version':'0.1.1'}}, start=False)
            self.send({'method':'initialized', 'params':{}})
            self.initialized = True

    def send(self, message):
        with self.writer:
            try:
                self.process.stdin.write(json.dumps(message, ensure_ascii=False)+'\n')
                self.process.stdin.flush()
            except (OSError, AttributeError):
                raise HTTPException(503, 'Codex 服务连接已断开，请刷新后继续') from None

    def request(self, method, params=None, *, start=True):
        if start:
            self.start()
        with self.lock:
            self.sequence += 1
            ident = self.sequence
            response = queue.Queue(maxsize=1)
            self.pending[ident] = response
        try:
            self.send({'id':ident, 'method':method, 'params':params or {}})
            try:
                result = response.get(timeout=25)
            except queue.Empty:
                raise HTTPException(504, 'Codex 请求超时，请刷新状态确认结果后再操作') from None
            if 'error' in result:
                # Protocol errors have no submitted request body or credentials.
                message = str(result['error'].get('message', 'Codex 请求失败'))[:1200]
                raise HTTPException(502, message)
            return result.get('result', {})
        finally:
            with self.lock:
                self.pending.pop(ident, None)

    def read(self, process):
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    if 'id' in message and 'method' not in message:
                        target = self.pending.get(message['id'])
                        if target:
                            target.put_nowait(message)
                        continue
                    method = message.get('method', '')
                    params = message.get('params', {})
                    if 'id' in message:
                        self.approvals[str(message['id'])] = message
                    if method == 'serverRequest/resolved':
                        self.approvals.pop(str(params.get('requestId')), None)
                    if method == 'turn/completed':
                        self.active_turns.pop(params.get('threadId'),None)
                        self.active_calls.pop((params.get('threadId'),params.get('turn',{}).get('id')),None)
                        for key, approval in list(self.approvals.items()):
                            if approval.get('params', {}).get('threadId') == params.get('threadId'):
                                self.approvals.pop(key, None)
                    if method == 'turn/started':
                        self.active_turns[params['threadId']]=params['turn']['id']
                    if method=='item/started' and params.get('item',{}).get('type')=='commandExecution':
                        self.active_calls.setdefault((params.get('threadId'),params.get('turnId')),set()).add(params['item']['id'])
                    if method.startswith(('item/', 'turn/', 'thread/', 'error', 'account/', 'serverRequest/')):
                        self.cursor += 1
                        # Bound individual tool outputs in polling memory.
                        if len(line) > 128*1024:
                            message = {'method':method, 'params':{'threadId':params.get('threadId'), 'refresh':True}}
                        self.events.append({'cursor':self.cursor, **message})
        finally:
            with self.lock:
                for target in self.pending.values():
                    try: target.put_nowait({'error':{'message':'Codex 服务已停止'}})
                    except queue.Full: pass

    def reply(self, ident, result):
        with self.lock:
            message = self.approvals.pop(str(ident), None)
            if not message:
                raise HTTPException(409, '该请求已结束，请刷新')
            self.send({'id':message['id'], 'result':result})


server = AppServer()
