"""Private file storage for proofs of payment and dispute evidence.

Files live on the server's disk outside the web docroot and are only ever
served through short-lived HMAC-signed URLs (15 minutes)."""

import os
import uuid
from pathlib import Path

from app.collections import crypto
from app.config import get_settings

MAX_BYTES = 5 * 1024 * 1024
SIGNED_URL_TTL = 15 * 60

_SIGNATURES = {
    b"\xff\xd8\xff": ("jpg", "image/jpeg"),
    b"\x89PNG\r\n\x1a\n": ("png", "image/png"),
    b"%PDF-": ("pdf", "application/pdf"),
}

CONTENT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "pdf": "application/pdf"}


class FileRejected(ValueError):
    pass


def sniff(data: bytes) -> str:
    """Return the extension for an allowed file, judged by its bytes, not its name."""
    for magic, (ext, _) in _SIGNATURES.items():
        if data.startswith(magic):
            return ext
    raise FileRejected("Upload a JPG, PNG or PDF file.")


def root() -> Path:
    return Path(get_settings().collections_storage_dir).resolve()


def save(data: bytes, folder: str) -> str:
    if not data:
        raise FileRejected("The file is empty.")
    if len(data) > MAX_BYTES:
        raise FileRejected("The file is larger than 5 MB.")
    ext = sniff(data)
    key = f"{folder}/{uuid.uuid4().hex}.{ext}"
    path = root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def path_for(key: str) -> Path | None:
    base = root()
    path = (base / key).resolve()
    if base not in path.parents or not path.is_file():
        return None
    return path


def delete(key: str | None) -> None:
    if not key:
        return
    path = path_for(key)
    if path:
        os.remove(path)


def signed_url(key: str | None) -> str | None:
    if not key:
        return None
    base = get_settings().collections_api_base_url.rstrip("/")
    return f"{base}/collections/files/{key}?{crypto.signed_query(key, SIGNED_URL_TTL)}"
