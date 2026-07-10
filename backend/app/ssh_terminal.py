#!/usr/bin/env python3
"""SSH Terminal module - manages SSH sessions via WebSocket"""

import uuid, time, logging, threading
from typing import Optional
import paramiko

logger = logging.getLogger("ssh_terminal")

_sessions: dict = {}
MAX_SESSIONS_PER_SERVER = 5
SESSION_TIMEOUT = 3600
RECONNECT_GRACE = 30  # seconds to wait for WebSocket reconnect after disconnect


class SSHTerminalSession:
    def __init__(self, session_id, server_id, server_name, host, port, user,
                 password=None, key_content=None):
        self.session_id = session_id
        self.server_id = server_id
        self.server_name = server_name
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.key_content = key_content
        self.channel = None
        self.client = None
        self.connected = False
        self.created_at = time.time()
        self.last_activity = time.time()
        self.pending_reconnect = False
        self._reconnect_timer = None

    def connect(self, cols=80, rows=24):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kwargs = {"hostname": self.host, "port": self.port,
                      "username": self.user, "timeout": 10}
            if self.key_content:
                import io
                kf = io.StringIO(self.key_content)
                pkey = None
                for cls in [paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey]:
                    try:
                        kf.seek(0)
                        pkey = cls.from_private_key(kf)
                        break
                    except Exception:
                        continue
                if pkey:
                    kwargs["pkey"] = pkey
            elif self.password:
                kwargs["password"] = self.password
            client.connect(**kwargs)
            self.client = client
            ch = client.invoke_shell(term="xterm-256color", width=cols, height=rows)
            ch.setblocking(0)
            self.channel = ch
            self.connected = True
            self.last_activity = time.time()
            logger.info(f"SSH session {self.session_id} connected to {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"SSH connect failed for {self.host}:{self.port}: {e}")
            self.close()
            return False

    def resize(self, cols, rows):
        if self.channel and self.connected:
            try:
                self.channel.resize_pty(width=cols, height=rows)
                self.last_activity = time.time()
            except Exception:
                pass

    def send(self, data):
        if self.channel and self.connected:
            try:
                self.channel.send(data)
                self.last_activity = time.time()
            except Exception:
                pass

    def recv(self, n=4096):
        if self.channel and self.connected:
            try:
                data = self.channel.recv(n)
                if data:
                    self.last_activity = time.time()
                return data
            except Exception:
                return b""
        return b""

    def close(self):
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
            self._reconnect_timer = None
        self.pending_reconnect = False
        try:
            if self.channel: self.channel.close()
        except Exception: pass
        try:
            if self.client: self.client.close()
        except Exception: pass
        self.connected = False

    def mark_pending_reconnect(self):
        """Mark session as pending reconnect, start grace timer"""
        self.pending_reconnect = True
        self._reconnect_timer = threading.Timer(RECONNECT_GRACE, self._reconnect_timeout)
        self._reconnect_timer.daemon = True
        self._reconnect_timer.start()
        logger.info(f"Session {self.session_id} pending reconnect, grace={RECONNECT_GRACE}s")

    def cancel_pending_reconnect(self):
        """Cancel reconnect timer, session resumed"""
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
            self._reconnect_timer = None
        self.pending_reconnect = False
        logger.info(f"Session {self.session_id} reconnected successfully")

    def _reconnect_timeout(self):
        """Grace period expired, destroy session"""
        logger.info(f"Session {self.session_id} reconnect grace expired, destroying")
        self.pending_reconnect = False
        remove_session(self.session_id)

    @property
    def is_alive(self):
        if self.pending_reconnect:
            return True  # Keep alive during grace period
        if not self.connected or not self.channel:
            return False
        try:
            if self.channel.exit_status_ready():
                return False
        except Exception:
            return False
        if time.time() - self.last_activity > SESSION_TIMEOUT:
            return False
        return True


def create_session(server_id, server_name, host, port, user,
                   password=None, key_content=None):
    _cleanup_dead()
    cnt = len([s for s in _sessions.values() if s.server_id == server_id and s.is_alive])
    if cnt >= MAX_SESSIONS_PER_SERVER:
        return "", f"\u670d\u52a1\u5668 {server_name} \u5df2\u6709 {MAX_SESSIONS_PER_SERVER} \u4e2a\u6d3b\u8dc3\u7ec8\u7aef\u4f1a\u8bdd"
    session_id = str(uuid.uuid4())
    s = SSHTerminalSession(session_id=session_id, server_id=server_id,
        server_name=server_name, host=host, port=port, user=user,
        password=password, key_content=key_content)
    _sessions[session_id] = s
    return session_id, ""


def get_session(session_id):
    return _sessions.get(session_id)


def remove_session(session_id):
    s = _sessions.pop(session_id, None)
    if s: s.close()


def _cleanup_dead():
    for sid in [sid for sid, s in _sessions.items() if not s.is_alive]:
        remove_session(sid)


def get_active_count():
    _cleanup_dead()
    return len([s for s in _sessions.values() if s.is_alive])
