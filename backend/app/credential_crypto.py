"""Stable encryption helper for managed connection secrets."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import CREDENTIAL_KEY
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator


def _fernet() -> Fernet:
    raw = CREDENTIAL_KEY.strip()
    if not raw:
        raise RuntimeError("CREDENTIAL_KEY 未配置，不能保存数据库凭证")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt((value or "").encode("utf-8")).decode("ascii") if value else ""


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("数据库凭证无法解密，请检查 CREDENTIAL_KEY") from exc


class SSHCredential(TypeDecorator):
    """Encrypt at rest while keeping legacy rows readable during migration."""
    impl = Text
    cache_ok = True
    prefix = "encrypted:ssh:v1:"

    def process_bind_param(self, value, dialect):
        if not value:
            return value
        # Always treat application input as plaintext, even if it resembles a prefix.
        return self.prefix + encrypt_secret(value)

    def process_result_value(self, value, dialect):
        if value and value.startswith(self.prefix):
            return decrypt_secret(value[len(self.prefix):])
        return value
