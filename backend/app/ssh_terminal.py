#!/usr/bin/env python3
"""SSH Terminal module - manages SSH sessions via WebSocket

v3.23.2 改动:
1. SSHClientPool: 按 (host,port,user) 维护常驻 paramiko.SSHClient, 复用避免重复握手
2. connect_in_background: 异步建连, POST 立即返回, WebSocket 等 ready
3. 状态机: status = 'connecting' | 'ready' | 'failed', 暴露 connect_error
4. 失败时打印 repr(e) 便于排障
"""

import uuid, time, logging, threading, io
from typing import Optional
import paramiko

logger = logging.getLogger("ssh_terminal")

_sessions: dict = {}
MAX_SESSIONS_PER_SERVER = 5
SESSION_TIMEOUT = 3600
RECONNECT_GRACE = 30  # seconds to wait for WebSocket reconnect after disconnect


# === SSH Client Pool (v3.23.2 新增) ===
class SSHClientPool:
    """按 (host,port,user) 维护常驻 SSHClient, 跨 session 复用避免重复 TCP+认证握手"""
    _lock = threading.Lock()
    _clients: dict = {}  # key -> {"client": paramiko.SSHClient, "last_use": float}

    @classmethod
    def _key(cls, host, port, user):
        return f"{host}:{port}:{user}"

    @classmethod
    def _is_alive(cls, client):
        try:
            transport = client.get_transport() if client else None
            return transport is not None and transport.is_active()
        except Exception:
            return False

    @classmethod
    def acquire(cls, host, port, user, password=None, key_content=None, timeout=10):
        """获取一个可用 SSHClient, 优先复用池里已建好的。返回 (client, reused_bool)"""
        key = cls._key(host, port, user)
        with cls._lock:
            entry = cls._clients.get(key)
            if entry and cls._is_alive(entry["client"]):
                entry["last_use"] = time.time()
                logger.info(f"SSH pool reuse: {key}")
                return entry["client"], True
            if entry:
                try: entry["client"].close()
                except Exception: pass
                del cls._clients[key]
        # 释放锁后再建连, 避免 connect 耗时阻塞其他 acquire
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {"hostname": host, "port": port, "username": user, "timeout": timeout}
        if key_content:
            key_data = key_content
            if not key_data.endswith("\n"):
                key_data += "\n"
            kf = io.StringIO(key_data)
            pkey = None
            for cls_k in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
                try:
                    kf.seek(0)
                    pkey = cls_k.from_private_key(kf)
                    break
                except Exception:
                    continue
            if pkey:
                kwargs["pkey"] = pkey
        elif password:
            kwargs["password"] = password
        client.connect(**kwargs)
        try:
            transport = client.get_transport()
            if transport:
                transport.set_keepalive(30)  # 30s 心跳保活
        except Exception:
            pass
        with cls._lock:
            cls._clients[key] = {"client": client, "last_use": time.time()}
        logger.info(f"SSH pool new connection: {key}")
        return client, False

    @classmethod
    def invalidate(cls, host, port, user):
        """标某连接失效并移除 (失败重连场景)"""
        key = cls._key(host, port, user)
        with cls._lock:
            entry = cls._clients.pop(key, None)
        if entry:
            try: entry["client"].close()
            except Exception: pass


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
        self.client = None  # 复用池里的 client, 不归此 session 关闭
        self.connected = False
        self.created_at = time.time()
        self.last_activity = time.time()
        self.pending_reconnect = False
        self._reconnect_timer = None
        # v3.23.2: 懒连接状态机
        self.status = "connecting"  # connecting | ready | failed
        self.connect_error = ""
        self.connect_event = threading.Event()
        self._connect_thread = None

    def connect(self, cols=80, rows=24):
        """同步建连 (内部用池), 供旧接口和后台线程调用"""
        try:
            client, reused = SSHClientPool.acquire(
                self.host, self.port, self.user,
                password=self.password, key_content=self.key_content)
            ch = client.invoke_shell(term="xterm-256color", width=cols, height=rows)
            ch.setblocking(0)
            self.client = client
            self.channel = ch
            self.connected = True
            self.last_activity = time.time()
            self.status = "ready"
            self.connect_event.set()
            logger.info(f"SSH session {self.session_id} connected to {self.host}:{self.port} (reused={reused})")
            return True
        except Exception as e:
            err = str(e) or e.__class__.__name__
            logger.error(f"SSH connect failed for {self.host}:{self.port}: {e!r}")
            self.connect_error = err
            self.status = "failed"
            self.connect_event.set()
            self.close()
            # 失败时让池清掉这条连接, 下次重新建
            try:
                SSHClientPool.invalidate(self.host, self.port, self.user)
            except Exception:
                pass
            return False

    def connect_in_background(self, cols=80, rows=24):
        """启动后台线程异步建连, 立即返回不阻塞调用方"""
        if self._connect_thread and self._connect_thread.is_alive():
            return
        self.status = "connecting"
        self.connect_event.clear()
        self.connect_error = ""
        self._connect_thread = threading.Thread(
            target=self.connect, args=(cols, rows), daemon=True,
            name=f"ssh-connect-{self.session_id[:8]}"
        )
        self._connect_thread.start()

    def wait_ready(self, timeout=15):
        """阻塞等待连接就绪, 返回 (ok: bool, err: str)"""
        if not self.connect_event.wait(timeout=timeout):
            return False, "SSH connect timeout"
        if self.status == "ready":
            return True, ""
        return False, self.connect_error or "SSH connection failed"

    @property
    def is_connecting(self):
        return self.status == "connecting"

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

    def get_sftp(self):
        """Get or create SFTP client from existing SSH connection"""
        if not self.client or not self.connected:
            return None
        try:
            if not hasattr(self, '_sftp') or self._sftp is None:
                self._sftp = self.client.open_sftp()
            return self._sftp
        except Exception as e:
            logger.error(f"SFTP open failed: {e}")
            return None

    def sftp_list(self, path="."):
        """List directory contents"""
        sftp = self.get_sftp()
        if not sftp:
            return [], "SFTP not available"
        try:
            entries = []
            for attr in sftp.listdir_attr(path):
                entries.append({
                    "name": attr.filename,
                    "size": attr.st_size,
                    "is_dir": attr.st_mode and (attr.st_mode & 0o040000) != 0,
                    "mode": oct(attr.st_mode)[2:] if attr.st_mode else "0",
                    "mtime": attr.st_mtime if attr.st_mtime else 0,
                })
            entries.sort(key=lambda x: (not x["is_dir"], x["name"]))
            return entries, ""
        except Exception as e:
            return [], str(e)

    def sftp_download(self, remote_path):
        """Download file as bytes"""
        sftp = self.get_sftp()
        if not sftp:
            return None, "SFTP not available"
        try:
            buf = io.BytesIO()
            sftp.getfo(remote_path, buf)
            buf.seek(0)
            return buf.read(), ""
        except Exception as e:
            return None, str(e)

    def sftp_upload(self, remote_path, data):
        """Upload bytes to remote path"""
        sftp = self.get_sftp()
        if not sftp:
            return False, "SFTP not available"
        try:
            buf = io.BytesIO(data)
            sftp.putfo(buf, remote_path)
            return True, ""
        except Exception as e:
            return False, str(e)

    def sftp_mkdir(self, path):
        """Create directory"""
        sftp = self.get_sftp()
        if not sftp:
            return False, "SFTP not available"
        try:
            sftp.mkdir(path)
            return True, ""
        except Exception as e:
            return False, str(e)

    def sftp_remove(self, path):
        """Remove file or directory"""
        sftp = self.get_sftp()
        if not sftp:
            return False, "SFTP not available"
        try:
            import stat
            attr = sftp.stat(path)
            if stat.S_ISDIR(attr.st_mode):
                # Recursively remove directory
                for item in sftp.listdir(path):
                    item_path = path.rstrip('/') + '/' + item
                    self.sftp_remove(item_path)
                sftp.rmdir(path)
            else:
                sftp.remove(path)
            return True, ""
        except Exception as e:
            return False, str(e)

    def sftp_rename(self, old_path, new_path):
        """Rename file or directory"""
        sftp = self.get_sftp()
        if not sftp:
            return False, "SFTP not available"
        try:
            sftp.rename(old_path, new_path)
            return True, ""
        except Exception as e:
            return False, str(e)

    def close(self):
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
            self._reconnect_timer = None
        self.pending_reconnect = False
        try:
            if self.channel: self.channel.close()
        except Exception: pass
        # 注意: client 由 SSHClientPool 统一管理, session.close 不 close client
        try:
            if hasattr(self, '_sftp') and self._sftp: self._sftp.close()
        except Exception: pass
        self._sftp = None
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
        # v3.23.2: 连接中也算 alive, 让前端能等到 ready
        if self.is_connecting:
            return True
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
