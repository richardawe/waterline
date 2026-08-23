from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.deal import Deal
from app.models.tier1 import Institution
from app.schemas import DealCreate

router = APIRouter(prefix="/deals", tags=["deals"])


def _to_dict(d: Deal) -> dict:
    return {
        "deal_id": d.deal_id,
        "originator_institution_id": d.originator_institution_id,
        "name": d.name,
        "deal_stage": d.deal_stage,
        "spv_name": d.spv_name,
        "notes": d.notes,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


@router.get("")
def list_deals(db: Session = Depends(get_db)):
    return [_to_dict(d) for d in db.query(Deal).order_by(Deal.created_at.desc()).all()]


@router.post("", status_code=201)
def create_deal(payload: DealCreate, db: Session = Depends(get_db)):
    if db.get(Institution, payload.originator_institution_id) is None:
        db.add(Institution(institution_id=payload.originator_institution_id, legal_name=payload.originator_institution_id))
        db.flush()
    deal = Deal(**payload.model_dump())
    db.add(deal)
    db.commit()
    db.refresh(deal)
    return _to_dict(deal)


@router.get("/{deal_id}")
def get_deal(deal_id: str, db: Session = Depends(get_db)):
    deal = db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "deal not found")
    d = _to_dict(deal)
    d["loan_tapes"] = [
        {
            "id": t.id,
            "original_filename": t.original_filename,
            "row_count": t.row_count,
            "status": t.status,
            "received_date": t.received_date.isoformat() if t.received_date else None,
        }
        for t in sorted(deal.loan_tapes, key=lambda t: t.received_date, reverse=True)
    ]
    d["spvs"] = [
        {
            "spv_id": s.spv_id,
            "name": s.name,
            "status": s.status,
            "as_of_date": s.as_of_date.isoformat() if s.as_of_date else None,
            "eligible_pool_par": float(s.eligible_pool_par) if s.eligible_pool_par is not None else None,
            "eligible_facility_count": s.eligible_facility_count,
        }
        for s in sorted(deal.spvs, key=lambda s: s.created_at, reverse=True)
    ]
    return d
