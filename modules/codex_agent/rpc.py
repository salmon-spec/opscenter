"""One private stdio connection to the official Codex app-server."""
import collections
import json
import os
import queue
import subprocess
import threading
import re
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
        self.active_commands = {}
        self.recent_commands = collections.OrderedDict()
        self.turn_modes = {}
        self.completed_turns = collections.OrderedDict()
        self.cancelled_turns = set()
        self.loaded_threads = set()
        self.public_states = collections.OrderedDict()
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
            self.active_commands.clear()
            self.recent_commands.clear()
            self.turn_modes.clear()
            self.completed_turns.clear()
            self.cancelled_turns.clear()
            self.loaded_threads.clear()
            self.public_states.clear()
            self.events.clear()
            self.cursor=0
            env = os.environ.copy()
            home = Path(env.get('CODEX_HOME', '/state/codex'))
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
            secret = os.getenv('CODEX_API_KEY_FILE', '')
            if secret:
                env['CODEX_PROVIDER_KEY'] = Path(secret).read_text().strip()
            args = [os.getenv('CODEX_BIN', 'codex'), 'app-server', '--listen', 'stdio://']
            args.extend(['-c','features.multi_agent=false','-c','tools.update_plan.enabled=true',
                         '-c','tools.experimental_request_user_input.enabled=true'])
            base = os.getenv('CODEX_API_BASE', '')
            if base:
                for value in ['model_provider="opscenter"', 'model_providers.opscenter.name="Existing Agent API"',
                              'model_providers.opscenter.base_url="http://127.0.0.1:8080/provider/v1"',
                              'model_providers.opscenter.env_key="CODEX_PROVIDER_KEY"',
                              'model_providers.opscenter.wire_api="responses"',
                              'model_providers.opscenter.request_max_retries=0',
                              'model_providers.opscenter.stream_max_retries=0',
                              'model_providers.opscenter.supports_websockets=false']:
                    args.extend(['-c', value])
                args.extend(['-c','web_search="disabled"'])
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                                            env=env, bufsize=1)
            threading.Thread(target=self.read, args=(self.process,), daemon=True).start()
            self.request('initialize', {'clientInfo': {'name':'opscenter_codex', 'title':'OpsCenter Codex', 'version':'0.7.4'},
                                        'capabilities':{'experimentalApi':True}}, start=False)
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
            value = result.get('result', {})
            with self.lock:
                if method in {'thread/start','thread/resume','thread/fork'}:
                    self.loaded_threads.add(value['thread']['id'])
                elif method in {'thread/archive','thread/delete'}:
                    self.loaded_threads.discard(params['threadId'])
            return value
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
                    self.observe_public(method,params)
                    if 'id' in message:
                        self.approvals[str(message['id'])] = message
                    if method == 'serverRequest/resolved':
                        self.approvals.pop(str(params.get('requestId')), None)
                    if method == 'turn/completed':
                        thread=params.get('threadId');turn=params.get('turn',{}).get('id');pair=(thread,turn)
                        # A fast completion can beat the turn/start caller's state write.
                        self.completed_turns[pair]=params.get('turn',{}).get('status','completed');self.completed_turns.move_to_end(pair)
                        while len(self.completed_turns)>200:self.completed_turns.popitem(last=False)
                        if self.active_commands.get(pair):
                            params=dict(params,backgroundRunning=True);message=dict(message,params=params)
                            if thread not in self.active_turns:self.active_turns[thread]=turn
                        else:self.release_turn(thread,turn)
                        for key, approval in list(self.approvals.items()):
                            pending=approval.get('params',{})
                            if pending.get('threadId')==thread and (thread not in self.active_turns or pending.get('turnId')==turn):
                                self.approvals.pop(key, None)
                    if method == 'turn/started':
                        if (params['threadId'],params['turn']['id']) not in self.completed_turns:
                            self.active_turns[params['threadId']]=params['turn']['id']
                    if method == 'thread/closed':
                        self.loaded_threads.discard(params.get('threadId'))
                    if method=='item/started' and params.get('item',{}).get('type')=='commandExecution':
                        pair=(params.get('threadId'),params.get('turnId'));item=params['item']
                        if item.get('status')=='inProgress':
                            self.active_commands.setdefault(pair,{})[item['id']]=dict(item)
                            self.active_calls.setdefault(pair,set()).add(item['id'])
                            if pair[0] not in self.active_turns:self.active_turns[pair[0]]=pair[1]
                            if pair in self.completed_turns:
                                params=dict(params,backgroundRunning=True,refresh=True);message=dict(message,params=params)
                    if method=='item/commandExecution/outputDelta':
                        pair=(params.get('threadId'),params.get('turnId'))
                        item=self.active_commands.get(pair,{}).get(params.get('itemId'))
                        if item is not None:item['aggregatedOutput']=((item.get('aggregatedOutput') or '')+params.get('delta',''))[-128*1024:]
                    if method=='item/completed' and params.get('item',{}).get('type')=='commandExecution':
                        pair=(params.get('threadId'),params.get('turnId'));ident=params['item']['id']
                        finished=dict(params['item'])
                        if isinstance(finished.get('aggregatedOutput'),str):finished['aggregatedOutput']=finished['aggregatedOutput'][-128*1024:]
                        key=(*pair,ident);self.recent_commands[key]=finished;self.recent_commands.move_to_end(key)
                        while len(self.recent_commands)>64:self.recent_commands.popitem(last=False)
                        commands=self.active_commands.get(pair,{})
                        commands.pop(ident,None)
                        if not commands:self.active_commands.pop(pair,None)
                        calls=self.active_calls.get(pair,set());calls.discard(ident)
                        if not calls:self.active_calls.pop(pair,None)
                        if pair in self.completed_turns:
                            if not commands:self.release_turn(*pair)
                            params=dict(params,backgroundRunning=bool(commands),refresh=True);message=dict(message,params=params)
                    if method.startswith(('item/', 'turn/', 'thread/', 'error', 'account/', 'serverRequest/')):
                        self.cursor += 1
                        # Bound individual tool outputs in polling memory.
                        if len(line) > 128*1024:
                            message = {'method':method, 'params':{'threadId':params.get('threadId'), 'refresh':True}}
                        self.events.append({'cursor':self.cursor, **message})
        finally:
            with self.lock:
                if process is self.process:
                    for target in self.pending.values():
                        try: target.put_nowait({'error':{'message':'Codex 服务已停止'}})
                        except queue.Full: pass

    def release_turn(self,thread,turn):
        """Release a native-finished turn only after its real commands finish."""
        pair=(thread,turn)
        self.turn_modes.pop(pair,None);self.active_calls.pop(pair,None)
        if self.active_turns.get(thread)==turn:
            other=next((key[1] for key,commands in self.active_commands.items() if key[0]==thread and commands),None)
            if other is None:self.active_turns.pop(thread,None)
            else:self.active_turns[thread]=other

    def reply(self, ident, result):
        with self.lock:
            message = self.approvals.pop(str(ident), None)
            if not message:
                raise HTTPException(409, '该请求已结束，请刷新')
            self.send({'id':message['id'], 'result':result})

    def emit(self, method, params):
        with self.lock:
            self.observe_public(method,params)
            self.cursor+=1
            self.events.append({'cursor':self.cursor,'method':method,'params':params})

    def observe_public(self, method, params):
        if method not in {'turn/plan/updated','turn/diff/updated'}: return
        thread=params.get('threadId','');turn=params.get('turnId','')
        if not all(isinstance(v,str) and re.fullmatch('[A-Za-z0-9_-]{1,100}',v) for v in [thread,turn]): return
        key=(thread,turn)
        with self.lock:
            data=dict(self.public_states.get(key) or self.load_public(thread,turn))
            if method=='turn/plan/updated':
                data['plan']=[{'step':str(p.get('step',''))[:1000],'status':p.get('status','pending')}
                              for p in params.get('plan',[])[:50] if isinstance(p,dict)]
                data['planExplanation']=str(params.get('explanation') or '')[:1000]
            else: data['diff']=str(params.get('diff') or '')[:256*1024]
            self.public_states[key]=data;self.public_states.move_to_end(key)
            while len(self.public_states)>200:self.public_states.popitem(last=False)
            root=Path(os.getenv('CODEX_GATEWAY_DATA','/state/gateway'))/'progress'/thread
            try:
                root.mkdir(parents=True,exist_ok=True,mode=0o700)
                target=root/(turn+'.json');temporary=root/(turn+'.tmp')
                temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8');temporary.chmod(0o600)
                os.replace(temporary,target)
            except OSError: pass

    def load_public(self, thread, turn):
        if not all(isinstance(v,str) and re.fullmatch('[A-Za-z0-9_-]{1,100}',v) for v in [thread,turn]):return {}
        with self.lock:
            if (thread,turn) in self.public_states:return self.public_states[(thread,turn)]
        path=Path(os.getenv('CODEX_GATEWAY_DATA','/state/gateway'))/'progress'/thread/(turn+'.json')
        try:
            data=json.loads(path.read_text(encoding='utf8'))
            return {k:data[k] for k in ['plan','planExplanation','diff'] if k in data}
        except (OSError,ValueError,TypeError):return {}


server = AppServer()
