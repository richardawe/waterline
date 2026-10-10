"""Field encryption, blind indexes, random tokens and signed URLs.

Phone numbers, emails and bank details are encrypted at rest (NDPA 2023).
Fields that must be looked up (login email, debtor phone for duplicate
checks) carry a separate HMAC "blind index" column instead of being stored
in the clear."""

import base64
import hashlib
import hmac
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

_DEV_SECRET = "waterline-collections-development-only-secret"


def _secret(name: str) -> str:
    settings = get_settings()
    value = getattr(settings, name)
    if value:
        return value
    if settings.environment.lower() == "production":
        raise RuntimeError(f"{name.upper()} must be set in production")
    return f"{_DEV_SECRET}:{name}"


def _fernet() -> Fernet:
    digest = hashlib.sha256(_secret("collections_encryption_key").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str | None) -> str | None:
    if value is None:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:  # wrong key or tampered row — never return ciphertext as data
        raise RuntimeError("could not decrypt collections field; check COLLECTIONS_ENCRYPTION_KEY") from exc


class EncryptedString(TypeDecorator):
    """Transparent Fernet encryption for a text column."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        return decrypt(value)


def blind_index(value: str | None) -> str | None:
    if value is None:
        return None
    key = _secret("collections_signing_key").encode()
    return hmac.new(key, b"idx:" + value.strip().lower().encode(), hashlib.sha256).hexdigest()


def new_token(nbytes: int = 32) -> str:
    """URL-safe random token; 32 bytes → 43 chars."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign(payload: str, expires_at: int) -> str:
    key = _secret("collections_signing_key").encode()
    return hmac.new(key, f"{payload}|{expires_at}".encode(), hashlib.sha256).hexdigest()


def signed_query(payload: str, ttl_seconds: int = 900) -> str:
    expires_at = int(time.time()) + ttl_seconds
    return f"exp={expires_at}&sig={sign(payload, expires_at)}"


def verify_signature(payload: str, exp: int, sig: str) -> bool:
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(sign(payload, exp), sig)


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
