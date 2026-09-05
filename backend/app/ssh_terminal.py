#!/usr/bin/env python3
"""SSH Terminal module - manages SSH sessions via WebSocket"""

import uuid, time, logging, threading
from typing import Optional
import paramiko

logger = logging.getLogger("ssh_terminal")

_sessions: dict = {}
MAX_SESSIONS_PER_SERVER = 5
SESSION_TIMEOUT = 14400  # v4.8: 4h idle timeout (was 3600)
RECONNECT_GRACE = 300  # v4.8: 5min reconnect grace (was 30)


class SSHTerminalSession:
    def __init__(self, session_id, server_id, server_name, host, port, user,
                 password=None, key_content=None, initial_command=None):
        self.session_id = session_id
        self.server_id = server_id
        self.server_name = server_name
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.key_content = key_content
        self.initial_command = initial_command
        self.channel = None
        self.client = None
        self.connected = False
        self.created_at = time.time()
        self.last_activity = time.time()
        self.pending_reconnect = False
        self._reconnect_timer = None
        self.reconnect_started_at = 0.0

    def connect(self, cols=80, rows=24):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kwargs = {"hostname": self.host, "port": self.port,
                      "username": self.user, "timeout": 10,
                      "banner_timeout": 10, "auth_timeout": 10,
                      "allow_agent": False, "look_for_keys": False}
            if self.key_content:
                import io
                # Ensure key ends with newline (required by paramiko parser)
                key_data = self.key_content
                if not key_data.endswith("\n"):
                    key_data += "\n"
                kf = io.StringIO(key_data)
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
            if self.initial_command:
                ch.send(self.initial_command + "\n")
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

    def touch(self):
        """v4.8: application-level ping - update activity without writing to shell."""
        self.last_activity = time.time()

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
            import io as _io
            buf = _io.BytesIO()
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
            import io as _io
            buf = _io.BytesIO(data)
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
        try:
            if self.client: self.client.close()
        except Exception: pass
        try:
            if hasattr(self, '_sftp') and self._sftp: self._sftp.close()
        except Exception: pass
        self._sftp = None
        self.connected = False

    def mark_pending_reconnect(self):
        """Mark session as pending reconnect, start grace timer"""
        self.pending_reconnect = True
        self.reconnect_started_at = time.time()
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


class LocalTerminalSession(SSHTerminalSession):
    """Local Linux PTY session for the host running OpsCenter.

    This deliberately runs as the OpsCenter service account and does not use
    stored SSH credentials or elevate privileges.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.process = None
        self.master_fd = None

    def connect(self, cols=80, rows=24):
        try:
            import os
            import pty
            import subprocess

            master_fd, slave_fd = pty.openpty()
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            shell = env.get("SHELL") or "/bin/bash"
            self.process = subprocess.Popen(
                [shell, "-l"], stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                close_fds=True, start_new_session=True, env=env,
            )
            os.close(slave_fd)
            os.set_blocking(master_fd, False)
            self.master_fd = master_fd
            self.channel = self  # keeps the shared lifecycle checks compatible
            self.connected = True
            self.resize(cols, rows)
            self.last_activity = time.time()
            if self.initial_command:
                self.send(self.initial_command + "\n")
            logger.info("Local PTY session %s started as current service user", self.session_id)
            return True
        except Exception as exc:
            logger.error("Local PTY start failed: %s", exc)
            self.close()
            return False

    def resize(self, cols, rows):
        if self.master_fd is None:
            return
        try:
            import fcntl
            import struct
            import termios
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            self.last_activity = time.time()
        except Exception:
            pass

    def send(self, data):
        if self.master_fd is None or not self.connected:
            return
        try:
            import os
            os.write(self.master_fd, data.encode() if isinstance(data, str) else data)
            self.last_activity = time.time()
        except (BlockingIOError, OSError):
            pass

    def touch(self):
        """v4.8: application-level ping - update activity without writing to shell."""
        self.last_activity = time.time()

    def recv(self, n=4096):
        if self.master_fd is None or not self.connected:
            return b""
        try:
            import os
            data = os.read(self.master_fd, n)
            if data:
                self.last_activity = time.time()
            return data
        except (BlockingIOError, OSError):
            return b""

    def exit_status_ready(self):
        return self.process is None or self.process.poll() is not None

    def get_sftp(self):
        return None

    def close(self):
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
            self._reconnect_timer = None
        self.pending_reconnect = False
        try:
            if self.master_fd is not None:
                import os
                os.close(self.master_fd)
        except Exception:
            pass
        self.master_fd = None
        try:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=2)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        self.process = None
        self.channel = None
        self.connected = False


def create_session(server_id, server_name, host, port, user,
                   password=None, key_content=None, initial_command=None, local=False):
    _cleanup_dead()
    cnt = len([s for s in _sessions.values() if s.server_id == server_id and s.is_alive])
    if cnt >= MAX_SESSIONS_PER_SERVER:
        return "", f"\u670d\u52a1\u5668 {server_name} \u5df2\u6709 {MAX_SESSIONS_PER_SERVER} \u4e2a\u6d3b\u8dc3\u7ec8\u7aef\u4f1a\u8bdd"
    session_id = str(uuid.uuid4())
    session_class = LocalTerminalSession if local else SSHTerminalSession
    s = session_class(session_id=session_id, server_id=server_id,
        server_name=server_name, host=host, port=port, user=user,
        password=password, key_content=key_content, initial_command=initial_command)
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
