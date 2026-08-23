import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.deal import SPV, Deal, PoolFacility, Tranche, WaterfallPeriod, WaterfallRun
from app.models.wcds import Facility, FacilityParty, FacilitySnapshot
from app.schemas import SPVStructureRequest
from app.spv.eligibility import EligibilityCriteria, FacilityCandidate, screen_pool
from app.spv.sizing import SizingAssumptions, size_pool
from app.spv.waterfall import WaterfallAssumptions, run_waterfall

router = APIRouter(tags=["spv"])


def _load_candidates(db: Session, institution_id: str, loan_tape_snapshot_id: str | None) -> list[FacilityCandidate]:
    q = db.query(Facility).filter(Facility.institution_id == institution_id)
    if loan_tape_snapshot_id:
        q = q.filter(Facility.loan_tape_snapshot_id == loan_tape_snapshot_id)
    facilities = q.all()

    candidates: list[FacilityCandidate] = []
    for f in facilities:
        snap = (
            db.query(FacilitySnapshot)
            .filter(FacilitySnapshot.facility_id == f.facility_id)
            .order_by(FacilitySnapshot.snapshot_date.desc())
            .first()
        )
        if snap is None:
            continue
        fp = db.query(FacilityParty).filter_by(facility_id=f.facility_id, role="BORROWER").first()
        candidates.append(
            FacilityCandidate(
                facility_id=f.facility_id,
                party_id=fp.party_id if fp else None,
                sector_code=f.sector_code,
                currency=f.currency,
                facility_status=f.facility_status,
                outstanding_principal=float(snap.outstanding_principal),
                days_past_due=snap.days_past_due,
                default_flag=snap.default_flag,
                writeoff_flag=snap.writeoff_flag,
                secured_flag=f.secured_flag,
                maturity_date=f.maturity_date,
                interest_rate=float(f.interest_rate) if f.interest_rate is not None else None,
            )
        )
    return candidates


def _spv_to_dict(spv: SPV) -> dict:
    return {
        "spv_id": spv.spv_id,
        "deal_id": spv.deal_id,
        "name": spv.name,
        "as_of_date": spv.as_of_date.isoformat(),
        "status": spv.status,
        "eligible_pool_par": float(spv.eligible_pool_par) if spv.eligible_pool_par is not None else None,
        "eligible_facility_count": spv.eligible_facility_count,
        "weighted_avg_rate": float(spv.weighted_avg_rate) if spv.weighted_avg_rate is not None else None,
        "weighted_avg_life_months": float(spv.weighted_avg_life_months) if spv.weighted_avg_life_months is not None else None,
        "weighted_avg_dpd": float(spv.weighted_avg_dpd) if spv.weighted_avg_dpd is not None else None,
        "tranches": [
            {
                "tranche_id": t.tranche_id,
                "name": t.name,
                "seniority_rank": t.seniority_rank,
                "initial_balance": float(t.initial_balance),
                "coupon_rate": float(t.coupon_rate) if t.coupon_rate is not None else None,
                "advance_rate": float(t.advance_rate) if t.advance_rate is not None else None,
            }
            for t in sorted(spv.tranches, key=lambda t: t.seniority_rank)
        ],
    }


@router.post("/deals/{deal_id}/spv", status_code=201)
def structure_spv(deal_id: str, payload: SPVStructureRequest, db: Session = Depends(get_db)):
    deal = db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "deal not found")

    candidates = _load_candidates(db, deal.originator_institution_id, payload.loan_tape_snapshot_id)
    if not candidates:
        raise HTTPException(400, "no ingested facilities found for this deal's originator institution")

    criteria = EligibilityCriteria(
        allowed_currencies=payload.allowed_currencies,
        allowed_statuses=payload.allowed_statuses,
        max_days_past_due=payload.max_days_past_due,
        exclude_default_flag=payload.exclude_default_flag,
        exclude_writeoff_flag=payload.exclude_writeoff_flag,
        min_outstanding_principal=payload.min_outstanding_principal,
        max_outstanding_principal=payload.max_outstanding_principal,
        min_remaining_tenor_days=payload.min_remaining_tenor_days,
        require_secured=payload.require_secured,
        max_single_obligor_pct=payload.max_single_obligor_pct,
        max_sector_concentration_pct=payload.max_sector_concentration_pct,
    )
    outcomes = screen_pool(candidates, criteria, payload.as_of_date)
    eligible_ids = {o.facility_id for o in outcomes if o.eligible}
    eligible = [c for c in candidates if c.facility_id in eligible_ids]
    if not eligible:
        raise HTTPException(400, "eligibility criteria excluded every candidate facility; loosen criteria")

    sizing_assumptions = SizingAssumptions(
        senior_advance_rate=payload.senior_advance_rate,
        mezz_advance_rate=payload.mezz_advance_rate,
        senior_coupon_rate=payload.senior_coupon_rate,
        mezz_coupon_rate=payload.mezz_coupon_rate,
    )
    sizing = size_pool(eligible, payload.as_of_date, sizing_assumptions)

    waterfall_assumptions = WaterfallAssumptions(
        horizon_months=payload.horizon_months,
        annual_default_rate=payload.annual_default_rate,
        annual_prepayment_rate=payload.annual_prepayment_rate,
        recovery_rate=payload.recovery_rate,
        recovery_lag_months=payload.recovery_lag_months,
        servicing_fee_annual_rate=payload.servicing_fee_annual_rate,
    )
    wf = run_waterfall(eligible, sizing, payload.as_of_date, waterfall_assumptions)

    spv = SPV(
        deal_id=deal_id,
        name=payload.name,
        as_of_date=payload.as_of_date,
        loan_tape_snapshot_id=payload.loan_tape_snapshot_id,
        eligible_pool_par=sizing.eligible_pool_par,
        eligible_facility_count=sizing.eligible_facility_count,
        weighted_avg_rate=sizing.weighted_avg_rate,
        weighted_avg_life_months=sizing.weighted_avg_life_months,
        weighted_avg_dpd=sizing.weighted_avg_dpd,
        eligibility_criteria_json=json.dumps(payload.model_dump(mode="json")),
        sizing_assumptions_json=json.dumps(
            {"senior_advance_rate": payload.senior_advance_rate, "mezz_advance_rate": payload.mezz_advance_rate}
        ),
        status="structured",
    )
    db.add(spv)
    db.flush()

    for t in sizing.tranches:
        db.add(
            Tranche(
                spv_id=spv.spv_id,
                name=t.name,
                seniority_rank=t.seniority_rank,
                initial_balance=t.initial_balance,
                coupon_rate=t.coupon_rate,
                advance_rate=t.advance_rate,
            )
        )

    for o in outcomes:
        db.add(
            PoolFacility(
                spv_id=spv.spv_id,
                facility_id=o.facility_id,
                eligible=o.eligible,
                exclusion_reasons=o.reasons_json() if o.reasons else None,
            )
        )

    run = WaterfallRun(
        spv_id=spv.spv_id,
        run_at=datetime.now(timezone.utc),
        assumptions_json=json.dumps(payload.model_dump(mode="json")),
        horizon_months=payload.horizon_months,
        senior_fully_repaid_month=wf.senior_fully_repaid_month,
        mezz_fully_repaid_month=wf.mezz_fully_repaid_month,
        equity_irr=wf.equity_irr_annual,
        senior_dscr_min=wf.senior_interest_coverage_min,
    )
    db.add(run)
    db.flush()

    for p in wf.periods:
        db.add(
            WaterfallPeriod(
                run_id=run.run_id,
                period_index=p.period_index,
                period_date=p.period_date,
                collections_principal=p.collections_principal,
                collections_interest=p.collections_interest,
                defaults=p.defaults,
                recoveries=p.recoveries,
                servicing_fee=p.servicing_fee,
                senior_interest_paid=p.senior_interest_paid,
                senior_principal_paid=p.senior_principal_paid,
                senior_balance_end=p.senior_balance_end,
                mezz_interest_paid=p.mezz_interest_paid,
                mezz_principal_paid=p.mezz_principal_paid,
                mezz_balance_end=p.mezz_balance_end,
                equity_distribution=p.equity_distribution,
                pool_balance_end=p.pool_balance_end,
            )
        )

    db.commit()
    db.refresh(spv)

    return {
        **_spv_to_dict(spv),
        "excluded_facility_count": len(candidates) - len(eligible),
        "waterfall_run_id": run.run_id,
        "senior_fully_repaid_month": wf.senior_fully_repaid_month,
        "mezz_fully_repaid_month": wf.mezz_fully_repaid_month,
        "equity_irr_annual": wf.equity_irr_annual,
        "senior_interest_coverage_min": wf.senior_interest_coverage_min,
    }


@router.get("/deals/{deal_id}/spv")
def list_spvs(deal_id: str, db: Session = Depends(get_db)):
    deal = db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "deal not found")
    spvs = db.query(SPV).filter(SPV.deal_id == deal_id).order_by(SPV.created_at.desc()).all()
    return [_spv_to_dict(s) for s in spvs]


@router.get("/spv/{spv_id}")
def get_spv(spv_id: str, db: Session = Depends(get_db)):
    spv = db.get(SPV, spv_id)
    if spv is None:
        raise HTTPException(404, "spv not found")
    return _spv_to_dict(spv)


@router.get("/spv/{spv_id}/waterfall")
def get_latest_waterfall(spv_id: str, db: Session = Depends(get_db)):
    run = (
        db.query(WaterfallRun)
        .filter(WaterfallRun.spv_id == spv_id)
        .order_by(WaterfallRun.run_at.desc())
        .first()
    )
    if run is None:
        raise HTTPException(404, "no waterfall run found for this spv")
    periods = db.query(WaterfallPeriod).filter(WaterfallPeriod.run_id == run.run_id).order_by(WaterfallPeriod.period_index).all()
    return {
        "run_id": run.run_id,
        "senior_fully_repaid_month": run.senior_fully_repaid_month,
        "mezz_fully_repaid_month": run.mezz_fully_repaid_month,
        "equity_irr": float(run.equity_irr) if run.equity_irr is not None else None,
        "senior_dscr_min": float(run.senior_dscr_min) if run.senior_dscr_min is not None else None,
        "periods": [
            {
                "period_index": p.period_index,
                "period_date": p.period_date.isoformat(),
                "collections_principal": float(p.collections_principal),
                "collections_interest": float(p.collections_interest),
                "defaults": float(p.defaults),
                "recoveries": float(p.recoveries),
                "servicing_fee": float(p.servicing_fee),
                "senior_interest_paid": float(p.senior_interest_paid),
                "senior_principal_paid": float(p.senior_principal_paid),
                "senior_balance_end": float(p.senior_balance_end),
                "mezz_interest_paid": float(p.mezz_interest_paid),
                "mezz_principal_paid": float(p.mezz_principal_paid),
                "mezz_balance_end": float(p.mezz_balance_end),
                "equity_distribution": float(p.equity_distribution),
                "pool_balance_end": float(p.pool_balance_end),
            }
            for p in periods
        ],
    }
