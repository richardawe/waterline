from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.security import require_admin

router = APIRouter(prefix="/admin-downloads", tags=["admin-downloads"], dependencies=[Depends(require_admin)])

DOWNLOAD_DIR = Path(__file__).resolve().parents[2] / "admin_downloads"
ALLOWED_FILES = {
    "WCDS_v0.1_Full_Specification.docx",
    "WCDS_v0.1_Anonymised_Lender_Datasets.zip",
    "WCDS_dataset_manifest.json",
}


@router.get("/{filename}")
def download_admin_file(filename: str):
    if filename not in ALLOWED_FILES:
        raise HTTPException(404, "download not found")
    path = DOWNLOAD_DIR / filename
    if not path.is_file():
        raise HTTPException(404, "download not available")
    return FileResponse(path, filename=filename)
