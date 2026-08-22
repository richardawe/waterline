#!/usr/bin/env python3
"""Migrate Tier 1 market-intelligence JSON (data/market-intelligence/*.json) into
Postgres. Idempotent: re-running clears and reloads every Tier 1 table (this is
seed data, not user-entered data — safe to fully replace on each run) so the DB
always mirrors whatever's currently in the JSON files, matching how database.html
already treats them as the source of truth.
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.models.tier1 import (  # noqa: E402
    CapitalMarketInstrument,
    DisbursementMetric,
    FccpcDigitalLender,
    IndustryAggregate,
    Institution,
    PortfolioMix,
    PortfolioSnapshot,
    ProductSnapshot,
    RatingAction,
)

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "market-intelligence"


def _load(name: str) -> dict:
    return json.loads((DATA_DIR / f"{name}.json").read_text())


def _date(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def _src(rec: dict) -> dict:
    s = rec.get("source") or {}
    return {
        "source_name": s.get("source_name"),
        "source_url": s.get("source_url"),
        "retrieved_date": _date(s.get("retrieved_date")),
    }


def seed_institutions(db) -> None:
    data = _load("institutions")
    for r in data["institutions"]:
        db.add(
            Institution(
                institution_id=r["institution_id"],
                legal_name=r["legal_name"],
                group_holdco_id=r.get("group_holdco_id"),
                type=r.get("type"),
                sector_tag=r.get("sector_tag"),
                cbn_authorization=r.get("cbn_authorization"),
                ngx_ticker=r.get("ngx_ticker"),
                ngx_listed=r.get("ngx_listed"),
                status=r.get("status"),
                headquarters=r.get("headquarters"),
                website=r.get("website"),
                notes=r.get("notes"),
                country_code="NG",
                **_src(r),
            )
        )
    db.flush()
    print(f"  institutions: {len(data['institutions'])}")


def seed_portfolio_snapshots(db) -> None:
    data = _load("portfolio_snapshots")
    n = 0
    for r in data["portfolio_snapshots"]:
        db.add(
            PortfolioSnapshot(
                institution_id=r["institution_id"],
                period_end=_date(r["period_end"]),
                period_type=r.get("period_type"),
                unit=r.get("unit"),
                gross_loans=r.get("gross_loans"),
                net_loans=r.get("net_loans"),
                npl_ratio=r.get("npl_ratio"),
                npl_coverage=r.get("npl_coverage"),
                avg_yield_on_loans=r.get("avg_yield_on_loans"),
                cost_of_funds=r.get("cost_of_funds"),
                confidence=r.get("confidence"),
                notes=r.get("notes"),
                **_src(r),
            )
        )
        n += 1
    print(f"  portfolio_snapshots: {n}")


def seed_portfolio_mix(db) -> None:
    data = _load("portfolio_mix")
    n = 0
    for r in data["portfolio_mix"]:
        mix = r.get("sector_mix") or r.get("geography_mix") or r.get("product_mix") or {}
        for dim_value, pct in mix.items():
            db.add(
                PortfolioMix(
                    institution_id=r["institution_id"],
                    period_end=_date(r["period_end"]),
                    dimension_type=r.get("dimension_type", "sector"),
                    dimension_value=dim_value,
                    pct_of_book=pct,
                    confidence=r.get("confidence"),
                    notes=r.get("notes"),
                    **_src(r),
                )
            )
            n += 1
    print(f"  portfolio_mix: {n}")


def seed_rating_actions(db) -> None:
    data = _load("rating_actions")
    n = 0
    for r in data["rating_actions"]:
        db.add(
            RatingAction(
                institution_id=r["institution_id"],
                action_date=_date(r.get("action_date") or r.get("action_date_estimated")),
                action_date_is_estimated="action_date_estimated" in r and "action_date" not in r,
                agency=r.get("agency"),
                rating=r.get("rating"),
                rating_type=r.get("rating_type"),
                outlook=r.get("outlook"),
                prior_rating=r.get("prior_rating"),
                rating_expiry=_date(r.get("rating_expiry")),
                notes=r.get("notes"),
                **_src(r),
            )
        )
        n += 1
    print(f"  rating_actions: {n}")


def seed_capital_market_instruments(db) -> None:
    data = _load("capital_market_instruments")
    n = 0
    for r in data["capital_market_instruments"]:
        tenor = f"{r['tenor_years']}y" if r.get("tenor_years") is not None else None
        db.add(
            CapitalMarketInstrument(
                institution_id=r["institution_id"],
                issuer_name=r.get("issuer_name"),
                instrument_type=r.get("instrument_type"),
                series=r.get("series"),
                issue_size=r.get("issue_size"),
                unit=r.get("unit"),
                tenor=tenor,
                coupon=r.get("coupon_rate"),
                issue_date=_date(r.get("issue_date")),
                use_of_proceeds=r.get("use_of_proceeds"),
                collateral_description=r.get("collateral_description"),
                trustee=r.get("trustee"),
                issuing_house=r.get("issuing_house"),
                confidence=r.get("confidence"),
                notes=r.get("notes"),
                **_src(r),
            )
        )
        n += 1
    print(f"  capital_market_instruments: {n}")


def seed_disbursement_metrics(db) -> None:
    data = _load("disbursement_metrics")
    n = 0
    for r in data["disbursement_metrics"]:
        db.add(
            DisbursementMetric(
                institution_id=r["institution_id"],
                period_end=_date(r.get("period_end")),
                period_type=r.get("period_type"),
                disbursement_volume=r.get("disbursement_volume"),
                unit=r.get("unit"),
                active_customers=r.get("active_customers"),
                avg_ticket_size=r.get("avg_ticket_size"),
                portfolio_at_risk=r.get("portfolio_at_risk"),
                metric_description=r.get("metric_description"),
                self_reported=bool(r.get("self_reported", False)),
                notes=r.get("notes"),
                **_src(r),
            )
        )
        n += 1
    print(f"  disbursement_metrics: {n}")


def seed_industry_aggregates(db) -> None:
    data = _load("industry_aggregates")
    n = 0
    for r in data["industry_aggregates"]:
        db.add(
            IndustryAggregate(
                period_end=_date(r["period_end"]),
                segment=r["segment"],
                metric=r["metric"],
                value=r.get("value"),
                unit=r.get("unit"),
                confidence=r.get("confidence"),
                notes=r.get("notes"),
                **_src(r),
            )
        )
        n += 1
    print(f"  industry_aggregates: {n}")


def seed_product_snapshots(db) -> None:
    data = _load("product_snapshots")
    n = 0
    for r in data["product_snapshots"]:
        db.add(
            ProductSnapshot(
                institution_id=r["institution_id"],
                min_ticket=r.get("min_ticket"),
                max_ticket=r.get("max_ticket"),
                ticket_currency=r.get("ticket_currency"),
                min_tenor_days=r.get("min_tenor_days"),
                max_tenor_days=r.get("max_tenor_days"),
                interest_rate_description=r.get("interest_rate_description"),
                eligibility_notes=r.get("eligibility_notes"),
                collateral_required=r.get("collateral_required"),
                confidence=r.get("confidence"),
                notes=r.get("notes"),
                **_src(r),
            )
        )
        n += 1
    print(f"  product_snapshots: {n}")


def seed_fccpc_digital_lenders(db) -> None:
    data = _load("fccpc_digital_lenders")
    n = 0
    for r in data["digital_lenders"]:
        s = r.get("source") or {}
        db.add(
            FccpcDigitalLender(
                app_or_entity_name=r.get("lender_name") or r.get("app_name") or "unknown",
                approval_status=r.get("approval_status"),
                retrieved_date=_date(s.get("retrieved_date")),
                raw=json.dumps(r),
            )
        )
        n += 1
    print(f"  fccpc_digital_lenders: {n}")


def main() -> None:
    db = SessionLocal()
    try:
        print("Clearing existing Tier 1 rows...")
        for model in (
            FccpcDigitalLender,
            ProductSnapshot,
            IndustryAggregate,
            DisbursementMetric,
            CapitalMarketInstrument,
            RatingAction,
            PortfolioMix,
            PortfolioSnapshot,
        ):
            db.query(model).delete()
        db.query(Institution).delete()
        db.flush()

        print("Seeding Tier 1 tables from data/market-intelligence/*.json...")
        seed_institutions(db)
        seed_portfolio_snapshots(db)
        seed_portfolio_mix(db)
        seed_rating_actions(db)
        seed_capital_market_instruments(db)
        seed_disbursement_metrics(db)
        seed_industry_aggregates(db)
        seed_product_snapshots(db)
        seed_fccpc_digital_lenders(db)

        db.commit()
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
