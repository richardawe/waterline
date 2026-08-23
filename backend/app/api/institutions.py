from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tier1 import (
    CapitalMarketInstrument,
    DisbursementMetric,
    FccpcDigitalLender,
    Institution,
    PortfolioMix,
    PortfolioSnapshot,
    ProductSnapshot,
    RatingAction,
)

router = APIRouter(prefix="/institutions", tags=["institutions"])


def _institution_to_dict(inst: Institution) -> dict:
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
        "headquarters": inst.headquarters,
        "website": inst.website,
        "notes": inst.notes,
        "source_name": inst.source_name,
        "source_url": inst.source_url,
        "retrieved_date": inst.retrieved_date.isoformat() if inst.retrieved_date else None,
    }


def _snapshot_to_dict(s: PortfolioSnapshot) -> dict:
    return {
        "institution_id": s.institution_id,
        "period_end": s.period_end.isoformat() if s.period_end else None,
        "period_type": s.period_type,
        "unit": s.unit,
        "gross_loans": float(s.gross_loans) if s.gross_loans is not None else None,
        "net_loans": float(s.net_loans) if s.net_loans is not None else None,
        "npl_ratio": float(s.npl_ratio) if s.npl_ratio is not None else None,
        "npl_coverage": float(s.npl_coverage) if s.npl_coverage is not None else None,
        "confidence": s.confidence,
        "notes": s.notes,
        "source_name": s.source_name,
        "source_url": s.source_url,
        "retrieved_date": s.retrieved_date.isoformat() if s.retrieved_date else None,
    }


def _rating_to_dict(r: RatingAction) -> dict:
    return {
        "institution_id": r.institution_id,
        "action_date": r.action_date.isoformat() if r.action_date else None,
        "action_date_is_estimated": r.action_date_is_estimated,
        "agency": r.agency,
        "rating": r.rating,
        "rating_type": r.rating_type,
        "outlook": r.outlook,
        "prior_rating": r.prior_rating,
        "rating_expiry": r.rating_expiry.isoformat() if r.rating_expiry else None,
        "notes": r.notes,
        "source_name": r.source_name,
        "source_url": r.source_url,
        "retrieved_date": r.retrieved_date.isoformat() if r.retrieved_date else None,
    }


@router.get("")
def list_institutions(db: Session = Depends(get_db), sector_tag: str | None = None, q: str | None = None):
    """Institution list enriched with each one's latest portfolio snapshot and
    latest rating action — the two facts the market-intel table/stats need,
    joined server-side so the frontend doesn't have to fetch and cross-reference
    separate tables itself."""
    stmt = select(Institution)
    if sector_tag:
        stmt = stmt.where(Institution.sector_tag == sector_tag)
    if q:
        stmt = stmt.where(Institution.legal_name.ilike(f"%{q}%"))
    institutions = db.execute(stmt.order_by(Institution.legal_name)).scalars().all()

    snaps_by_inst: dict[str, PortfolioSnapshot] = {}
    for s in db.execute(select(PortfolioSnapshot).order_by(PortfolioSnapshot.period_end.desc())).scalars():
        snaps_by_inst.setdefault(s.institution_id, s)

    ratings_by_inst: dict[str, RatingAction] = {}
    for r in db.execute(select(RatingAction).order_by(RatingAction.action_date.desc())).scalars():
        ratings_by_inst.setdefault(r.institution_id, r)

    rows = []
    for inst in institutions:
        d = _institution_to_dict(inst)
        snap = snaps_by_inst.get(inst.institution_id)
        rating = ratings_by_inst.get(inst.institution_id)
        d["gross_loans"] = float(snap.gross_loans if snap and snap.gross_loans is not None else snap.net_loans) if snap and (snap.gross_loans is not None or snap.net_loans is not None) else None
        d["gross_loans_unit"] = snap.unit if snap else None
        d["npl_ratio"] = float(snap.npl_ratio) if snap and snap.npl_ratio is not None else None
        d["snapshot_confidence"] = snap.confidence if snap else None
        d["rating"] = rating.rating if rating else None
        d["rating_type"] = rating.rating_type if rating else None
        rows.append(d)
    return rows


@router.get("/stats")
def institution_stats(db: Session = Depends(get_db)):
    fccpc_count = db.execute(select(FccpcDigitalLender)).scalars().all()
    rating_count = db.execute(select(RatingAction)).scalars().all()
    cmi_count = db.execute(select(CapitalMarketInstrument)).scalars().all()
    return {
        "fccpc_lenders_count": len(fccpc_count),
        "rating_actions_count": len(rating_count),
        "capital_market_instruments_count": len(cmi_count),
    }


@router.get("/{institution_id}/detail")
def get_institution_detail(institution_id: str, db: Session = Depends(get_db)):
    """Full detail bundle for one institution PLUS any subsidiaries that roll
    up to it (group_holdco_id == institution_id) — mirrors how the database
    page groups a holdco with its operating subsidiaries into one profile."""
    primary = db.get(Institution, institution_id)
    if primary is None:
        raise HTTPException(404, "institution not found")

    subsidiaries = db.execute(
        select(Institution).where(Institution.group_holdco_id == institution_id)
    ).scalars().all()
    member_ids = [institution_id] + [s.institution_id for s in subsidiaries]

    snaps = db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.institution_id.in_(member_ids))
        .order_by(PortfolioSnapshot.period_end.desc())
    ).scalars().all()
    ratings = db.execute(
        select(RatingAction)
        .where(RatingAction.institution_id.in_(member_ids))
        .order_by(RatingAction.action_date.desc())
    ).scalars().all()
    mix = db.execute(
        select(PortfolioMix)
        .where(PortfolioMix.institution_id.in_(member_ids))
        .order_by(PortfolioMix.period_end.desc())
    ).scalars().all()
    products = db.execute(
        select(ProductSnapshot).where(ProductSnapshot.institution_id.in_(member_ids))
    ).scalars().all()
    cmis = db.execute(
        select(CapitalMarketInstrument)
        .where(CapitalMarketInstrument.institution_id.in_(member_ids))
        .order_by(CapitalMarketInstrument.issue_date.desc())
    ).scalars().all()
    disb = db.execute(
        select(DisbursementMetric)
        .where(DisbursementMetric.institution_id.in_(member_ids))
        .order_by(DisbursementMetric.period_end.desc())
    ).scalars().all()

    return {
        "institution": _institution_to_dict(primary),
        "subsidiaries": [_institution_to_dict(s) for s in subsidiaries],
        "portfolio_snapshots": [_snapshot_to_dict(s) for s in snaps],
        "rating_actions": [_rating_to_dict(r) for r in ratings],
        "portfolio_mix": [
            {
                "institution_id": m.institution_id,
                "period_end": m.period_end.isoformat() if m.period_end else None,
                "dimension_type": m.dimension_type,
                "dimension_value": m.dimension_value,
                "amount": float(m.amount) if m.amount is not None else None,
                "pct_of_book": float(m.pct_of_book) if m.pct_of_book is not None else None,
                "confidence": m.confidence,
                "notes": m.notes,
                "source_name": m.source_name,
                "source_url": m.source_url,
            }
            for m in mix
        ],
        "product_snapshots": [
            {
                "institution_id": p.institution_id,
                "product_name": p.product_name,
                "min_ticket": float(p.min_ticket) if p.min_ticket is not None else None,
                "max_ticket": float(p.max_ticket) if p.max_ticket is not None else None,
                "ticket_currency": p.ticket_currency,
                "min_tenor_days": p.min_tenor_days,
                "max_tenor_days": p.max_tenor_days,
                "interest_rate_description": p.interest_rate_description,
                "eligibility_notes": p.eligibility_notes,
                "collateral_required": p.collateral_required,
                "confidence": p.confidence,
                "notes": p.notes,
                "scrape_date": p.scrape_date.isoformat() if p.scrape_date else None,
                "source_name": p.source_name,
                "source_url": p.source_url,
            }
            for p in products
        ],
        "capital_market_instruments": [
            {
                "institution_id": c.institution_id,
                "instrument_type": c.instrument_type,
                "series": c.series,
                "issue_size": float(c.issue_size) if c.issue_size is not None else None,
                "unit": c.unit,
                "tenor": c.tenor,
                "coupon": float(c.coupon) if c.coupon is not None else None,
                "issue_date": c.issue_date.isoformat() if c.issue_date else None,
                "use_of_proceeds": c.use_of_proceeds,
                "confidence": c.confidence,
                "notes": c.notes,
                "source_name": c.source_name,
                "source_url": c.source_url,
            }
            for c in cmis
        ],
        "disbursement_metrics": [
            {
                "institution_id": d.institution_id,
                "period_end": d.period_end.isoformat() if d.period_end else None,
                "disbursement_volume": float(d.disbursement_volume) if d.disbursement_volume is not None else None,
                "unit": d.unit,
                "metric_description": d.metric_description,
                "self_reported": d.self_reported,
                "notes": d.notes,
                "source_name": d.source_name,
                "source_url": d.source_url,
            }
            for d in disb
        ],
    }


@router.get("/{institution_id}")
def get_institution(institution_id: str, db: Session = Depends(get_db)):
    inst = db.get(Institution, institution_id)
    if inst is None:
        raise HTTPException(404, "institution not found")
    return _institution_to_dict(inst)
