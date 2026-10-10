"""Scheduled-job trigger for cron (cPanel cron or GitHub Actions), guarded by
COLLECTIONS_CRON_TOKEN. Same pattern as the blog pipeline: the job runs in
the backend process, which already has localhost database access."""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.collections import billing
from app.config import get_settings
from app.db import get_db

router = APIRouter(prefix="/collections/jobs", tags=["collections-jobs"])


def require_cron(request: Request) -> None:
    expected = get_settings().collections_cron_token
    header = request.headers.get("authorization", "")
    supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "Invalid job token.")


@router.post("/daily", dependencies=[Depends(require_cron)])
def daily(db: Session = Depends(get_db)):
    result = billing.run_daily(db)
    db.commit()
    return result
