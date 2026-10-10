"""Serves private files (proofs, dispute evidence, logos) only through a
valid, unexpired signed URL."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.collections import crypto, storage

router = APIRouter(prefix="/collections/files", tags=["collections-files"])


@router.get("/{key:path}")
def get_file(key: str, exp: int = 0, sig: str = ""):
    if not sig or not crypto.verify_signature(key, exp, sig):
        raise HTTPException(404, "Not found.")
    path = storage.path_for(key)
    if path is None:
        raise HTTPException(404, "Not found.")
    ext = path.suffix.lstrip(".")
    return FileResponse(
        path, media_type=storage.CONTENT_TYPES.get(ext, "application/octet-stream"),
        headers={"Cache-Control": "private, no-store", "Content-Disposition": f'inline; filename="file.{ext}"'},
    )
