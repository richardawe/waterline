#!/usr/bin/env python3
"""Demo: screen an ingested facility book into an eligible pool, size tranches,
and run the cashflow waterfall. Run scripts/validate_sample.py first to ingest
a tape into Postgres.

Usage: python scripts/run_spv_demo.py LENDER_C
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.models.wcds import Facility, FacilityParty, FacilitySnapshot  # noqa: E402
from app.spv.eligibility import EligibilityCriteria, FacilityCandidate, screen_pool  # noqa: E402
from app.spv.sizing import SizingAssumptions, size_pool  # noqa: E402
from app.spv.waterfall import WaterfallAssumptions, run_waterfall  # noqa: E402


def main() -> None:
    institution_id = sys.argv[1] if len(sys.argv) > 1 else "LENDER_C"
    as_of = date(2026, 8, 21)

    db = SessionLocal()
    try:
        facilities = db.query(Facility).filter(Facility.institution_id == institution_id).all()
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
    finally:
        db.close()

    print(f"loaded {len(candidates)} candidate facilities for {institution_id}")

    criteria = EligibilityCriteria()
    outcomes = screen_pool(candidates, criteria, as_of)
    eligible_ids = {o.facility_id for o in outcomes if o.eligible}
    eligible = [c for c in candidates if c.facility_id in eligible_ids]

    excluded = [o for o in outcomes if not o.eligible]
    print(f"\neligible: {len(eligible)} / {len(candidates)}")
    reason_counts: dict[str, int] = {}
    for o in excluded:
        for reason in o.reasons:
            key = reason.split(" ")[0]
            reason_counts[key] = reason_counts.get(key, 0) + 1
    print(f"exclusion reason categories: {reason_counts}")

    sizing = size_pool(eligible, as_of, SizingAssumptions())
    print(f"\npool par: {sizing.eligible_pool_par:,.2f}  facilities: {sizing.eligible_facility_count}")
    print(f"weighted avg rate: {sizing.weighted_avg_rate:.4%}  weighted avg life: {sizing.weighted_avg_life_months:.1f}mo  weighted avg dpd: {sizing.weighted_avg_dpd:.1f}")
    print(f"overcollateralization ratio: {sizing.overcollateralization_ratio:.3f}x")
    for t in sizing.tranches:
        print(f"  {t.name:12} balance={t.initial_balance:>16,.2f}  coupon={t.coupon_rate}  advance_rate={t.advance_rate}")

    wf = run_waterfall(eligible, sizing, as_of, WaterfallAssumptions())
    print(f"\nsenior fully repaid month: {wf.senior_fully_repaid_month}")
    print(f"mezz fully repaid month: {wf.mezz_fully_repaid_month}")
    print(f"equity IRR (annualized): {wf.equity_irr_annual}")
    print(f"senior interest coverage min: {wf.senior_interest_coverage_min}")
    print("\nfirst 6 months:")
    for p in wf.periods[:6]:
        print(
            f"  m{p.period_index:02} coll_p={p.collections_principal:>12,.2f} coll_i={p.collections_interest:>10,.2f} "
            f"def={p.defaults:>9,.2f} rec={p.recoveries:>8,.2f} fee={p.servicing_fee:>7,.2f} "
            f"sr_bal={p.senior_balance_end:>14,.2f} mezz_bal={p.mezz_balance_end:>12,.2f} eq_dist={p.equity_distribution:>9,.2f}"
        )
    print("\nlast 3 months:")
    for p in wf.periods[-3:]:
        print(
            f"  m{p.period_index:02} coll_p={p.collections_principal:>12,.2f} sr_bal={p.senior_balance_end:>14,.2f} "
            f"mezz_bal={p.mezz_balance_end:>12,.2f} pool_bal={p.pool_balance_end:>14,.2f} eq_dist={p.equity_distribution:>9,.2f}"
        )


if __name__ == "__main__":
    main()
