"""Persist SSH host keys so first-use trust becomes verifiable afterwards."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import paramiko

from app.config import SSH_KNOWN_HOSTS_FILE

logger = logging.getLogger("ssh_host_keys")
_file_lock = threading.Lock()


def configure_host_keys(client: paramiko.SSHClient) -> None:
    """Load the product known-hosts file and permit TOFU for unknown hosts."""
    path = Path(SSH_KNOWN_HOSTS_FILE) if SSH_KNOWN_HOSTS_FILE else None
    if path and path.is_file():
        try:
            client.load_host_keys(str(path))
        except Exception as exc:
            logger.warning("Could not load SSH known-hosts file %s: %s", path, exc)
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())


def persist_host_keys(client: paramiko.SSHClient) -> None:
    """Best-effort save after a successful connection; never breaks SSH use."""
    if not SSH_KNOWN_HOSTS_FILE:
        return
    path = Path(SSH_KNOWN_HOSTS_FILE)
    try:
        with _file_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            client.save_host_keys(str(path))
            if os.name != "nt":
                path.chmod(0o600)
    except Exception as exc:
        logger.warning("Could not persist SSH host keys to %s: %s", path, exc)
