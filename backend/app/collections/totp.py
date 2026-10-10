"""RFC 6238 TOTP for the ops area's second factor (stdlib only)."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, for_time: float, step: int = 30, digits: int = 6) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded.upper())
    counter = struct.pack(">Q", int(for_time // step))
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def verify(secret: str, code: str, now: float | None = None, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    now = time.time() if now is None else now
    return any(hmac.compare_digest(code_at(secret, now + i * 30), code) for i in range(-window, window + 1))


def provisioning_uri(secret: str, email: str) -> str:
    return f"otpauth://totp/Waterline%20Collections:{quote(email)}?secret={secret}&issuer=Waterline%20Collections"
