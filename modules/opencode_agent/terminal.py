"""HTTP polling bridge to native OpenCode PTYs for the workbench frame."""
import base64
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi import HTTPException
import websocket

_lock = threading.Lock()
_streams = {}
LIMIT = 512 * 1024


class Stream:
    def __init__(self, url, password):
        auth = base64.b64encode(("opencode:" + password).encode()).decode()
        try:
            self.socket = websocket.create_connection(url, header=["Authorization: Basic " + auth], timeout=5)
        except Exception:
            raise HTTPException(502, "终端连接失败") from None
        self.socket.settimeout(1)
        self.lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.output = ""
        self.cursor = 0
        self.closed = False
        self.last_read = time.monotonic()
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            while not self.closed:
                if time.monotonic() - self.last_read > 1800:
                    break
                try:
                    data = self.socket.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not data:
                    break
                if isinstance(data, bytes):
                    # Native binary messages carry replay cursor metadata.
                    continue
                with self.lock:
                    self.output = (self.output + data)[-LIMIT:]
                    self.cursor += len(data)
        except Exception:
            pass
        finally:
            self.closed = True
            self.socket.close()

    def poll(self, cursor):
        self.last_read = time.monotonic()
        with self.lock:
            start = max(0, self.cursor - len(self.output))
            return {"output": self.output[max(0, cursor - start):], "cursor": self.cursor,
                    "truncated": cursor < start, "closed": self.closed}

    def send(self, data):
        self.last_read = time.monotonic()
        if self.closed:
            raise HTTPException(409, "终端连接已关闭，请重新打开")
        try:
            with self.send_lock:
                self.socket.send(data)
        except Exception:
            raise HTTPException(502, "终端输入失败") from None


def stream(pty_id, runner_url, workspace, password_file):
    with _lock:
        existing = _streams.get(pty_id)
        if existing and not existing.closed:
            return existing
        if len([s for s in _streams.values() if not s.closed]) >= 4:
            raise HTTPException(409, "最多连接四个终端，请关闭不用的终端")
        password = Path(password_file).read_text().strip()
        if not password:
            raise HTTPException(503, "终端凭据不可用")
        url = runner_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        value = Stream(url + "/pty/" + pty_id + "/connect?" + urlencode({"directory": workspace, "cursor": 0}), password)
        _streams[pty_id] = value
        return value


def close(pty_id):
    with _lock:
        value = _streams.pop(pty_id, None)
        if value:
            value.closed = True
            value.socket.close()
