from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tier1 import Institution

router = APIRouter(prefix="/institutions", tags=["institutions"])


def _to_dict(inst: Institution) -> dict:
    return {
        "institution_id": inst.institution_id,
        "legal_name": inst.legal_name,
        "type": inst.type,
        "sector_tag": inst.sector_tag,
        "cbn_authorization": inst.cbn_authorization,
        "ngx_ticker": inst.ngx_ticker,
        "ngx_listed": inst.ngx_listed,
        "status": inst.status,
        "group_holdco_id": inst.group_holdco_id,
    }


@router.get("")
def list_institutions(db: Session = Depends(get_db), sector_tag: str | None = None, q: str | None = None):
    stmt = select(Institution)
    if sector_tag:
        stmt = stmt.where(Institution.sector_tag == sector_tag)
    if q:
        stmt = stmt.where(Institution.legal_name.ilike(f"%{q}%"))
    rows = db.execute(stmt.order_by(Institution.legal_name)).scalars().all()
    return [_to_dict(r) for r in rows]


@router.get("/{institution_id}")
def get_institution(institution_id: str, db: Session = Depends(get_db)):
    inst = db.get(Institution, institution_id)
    if inst is None:
        raise HTTPException(404, "institution not found")
    return _to_dict(inst)
