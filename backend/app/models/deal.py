"""Deal lifecycle + SPV structuring models.

Per docs/loan-database-plan.md §1.2: WCDS defines the loan data itself (models/wcds.py);
`Deal` and `LoanTapeSnapshot` sit above it to track deal-lifecycle state and which
ingested loan tape backs which deal. SPV/Tranche/WaterfallRun are new — the Step 3
"structure funding" pipeline described on the site.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid

MONEY = Numeric(20, 2)


class Deal(Base, TimestampMixin):
    __tablename__ = "deal"

    deal_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    originator_institution_id: Mapped[str] = mapped_column(ForeignKey("institution.institution_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    deal_stage: Mapped[str] = mapped_column(
        String(20), nullable=False, default="discovery"
    )  # discovery/standardize/structure/validate/close/live
    spv_name: Mapped[Optional[str]] = mapped_column(String(255))
    senior_tranche_size: Mapped[Optional[float]] = mapped_column(MONEY)
    equity_tranche_size: Mapped[Optional[float]] = mapped_column(MONEY)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    loan_tapes: Mapped[list["LoanTapeSnapshot"]] = relationship(back_populates="deal")
    spvs: Mapped[list["SPV"]] = relationship(back_populates="deal")


class LoanTapeSnapshot(Base, TimestampMixin):
    """One ingested loan-tape upload/version for a deal. Facilities/snapshots ingested
    from this file link back via Facility.loan_tape_snapshot_id."""

    __tablename__ = "loan_tape_snapshot"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    deal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("deal.deal_id"))
    institution_id: Mapped[Optional[str]] = mapped_column(ForeignKey("institution.institution_id"))
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    nda_reference: Mapped[Optional[str]] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="uploaded"
    )  # uploaded/mapped/validated/reconciled/failed
    mapping_json: Mapped[Optional[str]] = mapped_column(Text)  # source_column -> wcds_field
    wcds_version: Mapped[str] = mapped_column(String(20), nullable=False, default="0.1.0")

    deal: Mapped[Optional["Deal"]] = relationship(back_populates="loan_tapes")


class PoolFacility(Base, TimestampMixin):
    """Membership + eligibility outcome of a facility against a candidate SPV pool.
    One row per (spv_id, facility_id) — screened in/out with reasons, per SPV pipeline
    eligibility rules (app/spv/eligibility.py)."""

    __tablename__ = "pool_facility"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    spv_id: Mapped[str] = mapped_column(ForeignKey("spv.spv_id"), nullable=False)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facility.facility_id"), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exclusion_reasons: Mapped[Optional[str]] = mapped_column(Text)  # JSON array of strings
    pool_par_value: Mapped[Optional[float]] = mapped_column(MONEY)


class SPV(Base, TimestampMixin):
    __tablename__ = "spv"

    spv_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deal.deal_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    loan_tape_snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("loan_tape_snapshot.id"))

    eligible_pool_par: Mapped[Optional[float]] = mapped_column(MONEY)
    eligible_facility_count: Mapped[Optional[int]] = mapped_column(Integer)
    weighted_avg_rate: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    weighted_avg_life_months: Mapped[Optional[float]] = mapped_column(Numeric(9, 2))
    weighted_avg_dpd: Mapped[Optional[float]] = mapped_column(Numeric(9, 2))

    eligibility_criteria_json: Mapped[Optional[str]] = mapped_column(Text)
    sizing_assumptions_json: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")

    deal: Mapped["Deal"] = relationship(back_populates="spvs")
    tranches: Mapped[list["Tranche"]] = relationship(back_populates="spv")
    waterfall_runs: Mapped[list["WaterfallRun"]] = relationship(back_populates="spv")


class Tranche(Base, TimestampMixin):
    __tablename__ = "tranche"

    tranche_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    spv_id: Mapped[str] = mapped_column(ForeignKey("spv.spv_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # Senior / Mezzanine / Equity
    seniority_rank: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 = most senior
    initial_balance: Mapped[float] = mapped_column(MONEY, nullable=False)
    coupon_rate: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    advance_rate: Mapped[Optional[float]] = mapped_column(Numeric(7, 4))  # % of eligible pool

    spv: Mapped["SPV"] = relationship(back_populates="tranches")


class WaterfallRun(Base, TimestampMixin):
    """One deterministic simulation of pool cashflows through the tranche waterfall."""

    __tablename__ = "waterfall_run"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    spv_id: Mapped[str] = mapped_column(ForeignKey("spv.spv_id"), nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    assumptions_json: Mapped[Optional[str]] = mapped_column(Text)  # prepay/default/recovery/servicing assumptions
    horizon_months: Mapped[int] = mapped_column(Integer, nullable=False)
    senior_fully_repaid_month: Mapped[Optional[int]] = mapped_column(Integer)
    mezz_fully_repaid_month: Mapped[Optional[int]] = mapped_column(Integer)
    equity_irr: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    senior_dscr_min: Mapped[Optional[float]] = mapped_column(Numeric(9, 4))

    spv: Mapped["SPV"] = relationship(back_populates="waterfall_runs")
    periods: Mapped[list["WaterfallPeriod"]] = relationship(back_populates="run")


class WaterfallPeriod(Base, TimestampMixin):
    """One monthly period's collections + sequential-pay allocation for a waterfall run."""

    __tablename__ = "waterfall_period"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("waterfall_run.run_id"), nullable=False)
    period_index: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-based month
    period_date: Mapped[date] = mapped_column(Date, nullable=False)

    collections_principal: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)
    collections_interest: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)
    defaults: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)
    recoveries: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)
    servicing_fee: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)

    senior_interest_paid: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)
    senior_principal_paid: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)
    senior_balance_end: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)

    mezz_interest_paid: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)
    mezz_principal_paid: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)
    mezz_balance_end: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)

    equity_distribution: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)
    pool_balance_end: Mapped[float] = mapped_column(MONEY, nullable=False, default=0)

    run: Mapped["WaterfallRun"] = relationship(back_populates="periods")
