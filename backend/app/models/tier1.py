"""Tier 1 — Market Intelligence models (public-source, institution/portfolio grain).

Mirrors the JSON schema documented in data/market-intelligence/README.md and
docs/loan-database-plan.md §1.1. `Institution` doubles as the WCDS `Institution`
entity referenced by Facility.institution_id (see models/wcds.py) — one lender,
one row, whether we're tracking its public financials or ingesting its loan tape.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid


class Institution(Base, TimestampMixin):
    """One row per lender. Shared identity for Tier 1 market-intel facts and
    Tier 2 WCDS facility ownership. institution_id is a stable slug (e.g.
    'access-bank'), not a UUID — WCDS §4.1 only requires org-wide uniqueness,
    UUIDv7 is a recommendation we deviate from for readability/seed continuity."""

    __tablename__ = "institution"

    institution_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Tier 1 / market-intel fields
    group_holdco_id: Mapped[Optional[str]] = mapped_column(ForeignKey("institution.institution_id"))
    type: Mapped[Optional[str]] = mapped_column(String(50))  # commercial_bank/mfb/fintech/leasing_company/...
    sector_tag: Mapped[Optional[str]] = mapped_column(String(50))
    cbn_authorization: Mapped[Optional[str]] = mapped_column(String(50))
    ngx_ticker: Mapped[Optional[str]] = mapped_column(String(20))
    ngx_listed: Mapped[Optional[bool]] = mapped_column(Boolean)
    status: Mapped[Optional[str]] = mapped_column(String(30))
    headquarters: Mapped[Optional[str]] = mapped_column(String(255))
    website: Mapped[Optional[str]] = mapped_column(String(255))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # WCDS §5.1 fields
    institution_code: Mapped[Optional[str]] = mapped_column(String(50), unique=True)
    institution_type: Mapped[Optional[str]] = mapped_column(String(50))
    country_code: Mapped[Optional[str]] = mapped_column(String(2), default="NG")

    source_name: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_date: Mapped[Optional[date]] = mapped_column(Date)

    filings: Mapped[list["Filing"]] = relationship(back_populates="institution")


class Filing(Base, TimestampMixin):
    __tablename__ = "filing"

    filing_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institution.institution_id"), nullable=False)
    source_type: Mapped[Optional[str]] = mapped_column(String(50))
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    filing_date: Mapped[Optional[date]] = mapped_column(Date)
    url: Mapped[Optional[str]] = mapped_column(Text)
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    extraction_status: Mapped[Optional[str]] = mapped_column(String(30))

    institution: Mapped["Institution"] = relationship(back_populates="filings")


class Provenance(Base, TimestampMixin):
    __tablename__ = "provenance"

    provenance_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_document_id: Mapped[Optional[str]] = mapped_column(ForeignKey("filing.filing_id"))
    source_name: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_date: Mapped[Optional[date]] = mapped_column(Date)
    extraction_method: Mapped[Optional[str]] = mapped_column(String(30))  # manual/ocr/llm/api
    extracted_by: Mapped[Optional[str]] = mapped_column(String(150))
    confidence_score: Mapped[Optional[str]] = mapped_column(String(10))  # high/medium/low
    self_reported: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[Optional[str]] = mapped_column(String(150))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PortfolioSnapshot(Base, TimestampMixin):
    __tablename__ = "portfolio_snapshot"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institution.institution_id"), nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_type: Mapped[Optional[str]] = mapped_column(String(30))  # FY/H1/Q1.../interim
    unit: Mapped[Optional[str]] = mapped_column(String(20))  # NGN_billion/NGN_trillion/NGN — scale of the raw figures below
    gross_loans: Mapped[Optional[float]] = mapped_column(Numeric(24, 4))
    net_loans: Mapped[Optional[float]] = mapped_column(Numeric(24, 4))
    stage_1_balance: Mapped[Optional[float]] = mapped_column(Numeric(24, 4))
    stage_2_balance: Mapped[Optional[float]] = mapped_column(Numeric(24, 4))
    stage_3_balance: Mapped[Optional[float]] = mapped_column(Numeric(24, 4))
    npl_ratio: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    npl_coverage: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    avg_yield_on_loans: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    cost_of_funds: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    provisioning_charge: Mapped[Optional[float]] = mapped_column(Numeric(24, 4))
    confidence: Mapped[Optional[str]] = mapped_column(String(10))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    source_name: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_date: Mapped[Optional[date]] = mapped_column(Date)


class PortfolioMix(Base, TimestampMixin):
    """One row per (institution, period, dimension_value) — the source JSON's
    per-period sector_mix dict is expanded into one row per sector at seed time."""

    __tablename__ = "portfolio_mix"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institution.institution_id"), nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    dimension_type: Mapped[str] = mapped_column(String(30), nullable=False)  # sector/geography/product/...
    dimension_value: Mapped[str] = mapped_column(String(150), nullable=False)
    amount: Mapped[Optional[float]] = mapped_column(Numeric(24, 4))
    pct_of_book: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    confidence: Mapped[Optional[str]] = mapped_column(String(10))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    source_name: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_date: Mapped[Optional[date]] = mapped_column(Date)


class RatingAction(Base, TimestampMixin):
    __tablename__ = "rating_action"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institution.institution_id"), nullable=False)
    action_date: Mapped[Optional[date]] = mapped_column(Date)
    action_date_is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    agency: Mapped[Optional[str]] = mapped_column(String(50))
    rating: Mapped[Optional[str]] = mapped_column(String(80))
    rating_type: Mapped[Optional[str]] = mapped_column(String(30))  # issuer/instrument/...
    outlook: Mapped[Optional[str]] = mapped_column(String(20))
    prior_rating: Mapped[Optional[str]] = mapped_column(String(80))
    rating_expiry: Mapped[Optional[date]] = mapped_column(Date)
    report_url: Mapped[Optional[str]] = mapped_column(Text)
    key_commentary_tags: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    source_name: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_date: Mapped[Optional[date]] = mapped_column(Date)


class CapitalMarketInstrument(Base, TimestampMixin):
    __tablename__ = "capital_market_instrument"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institution.institution_id"), nullable=False)
    issuer_name: Mapped[Optional[str]] = mapped_column(String(255))
    instrument_type: Mapped[Optional[str]] = mapped_column(String(30))
    series: Mapped[Optional[str]] = mapped_column(String(150))
    issue_size: Mapped[Optional[float]] = mapped_column(Numeric(24, 4))
    unit: Mapped[Optional[str]] = mapped_column(String(20))
    tenor: Mapped[Optional[str]] = mapped_column(String(30))
    coupon: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    issue_date: Mapped[Optional[date]] = mapped_column(Date)
    use_of_proceeds: Mapped[Optional[str]] = mapped_column(Text)
    collateral_description: Mapped[Optional[str]] = mapped_column(Text)
    sec_rin: Mapped[Optional[str]] = mapped_column(String(50))
    trustee: Mapped[Optional[str]] = mapped_column(String(150))
    issuing_house: Mapped[Optional[str]] = mapped_column(String(150))
    confidence: Mapped[Optional[str]] = mapped_column(String(10))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    source_name: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_date: Mapped[Optional[date]] = mapped_column(Date)


class DisbursementMetric(Base, TimestampMixin):
    __tablename__ = "disbursement_metric"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institution.institution_id"), nullable=False)
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    period_type: Mapped[Optional[str]] = mapped_column(String(30))
    disbursement_volume: Mapped[Optional[float]] = mapped_column(Numeric(24, 4))
    unit: Mapped[Optional[str]] = mapped_column(String(20))
    active_customers: Mapped[Optional[int]] = mapped_column()
    avg_ticket_size: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    portfolio_at_risk: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    metric_description: Mapped[Optional[str]] = mapped_column(Text)
    self_reported: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    source_name: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_date: Mapped[Optional[date]] = mapped_column(Date)


class IndustryAggregate(Base, TimestampMixin):
    """CBN-sourced industry-wide denominators — one row per (segment, metric, period).
    E.g. segment='deposit_money_banks', metric='total_gross_credit', value=700.31, unit='NGN_trillion'."""

    __tablename__ = "industry_aggregate"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    segment: Mapped[str] = mapped_column(String(100), nullable=False)
    metric: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Optional[float]] = mapped_column(Numeric(24, 4))
    unit: Mapped[Optional[str]] = mapped_column(String(20))
    confidence: Mapped[Optional[str]] = mapped_column(String(10))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    source_name: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_date: Mapped[Optional[date]] = mapped_column(Date)


class ProductSnapshot(Base, TimestampMixin):
    __tablename__ = "product_snapshot"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institution.institution_id"), nullable=False)
    product_name: Mapped[Optional[str]] = mapped_column(String(150))
    min_ticket: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    max_ticket: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ticket_currency: Mapped[Optional[str]] = mapped_column(String(3))
    min_tenor_days: Mapped[Optional[int]] = mapped_column()
    max_tenor_days: Mapped[Optional[int]] = mapped_column()
    interest_rate_description: Mapped[Optional[str]] = mapped_column(Text)
    eligibility_notes: Mapped[Optional[str]] = mapped_column(Text)
    collateral_required: Mapped[Optional[str]] = mapped_column(String(50))  # free text: "none"/"required"/...
    confidence: Mapped[Optional[str]] = mapped_column(String(10))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    scrape_date: Mapped[Optional[date]] = mapped_column(Date)
    source_name: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_date: Mapped[Optional[date]] = mapped_column(Date)


class FccpcDigitalLender(Base, TimestampMixin):
    """FCCPC digital-lender registry (source #9) — the loan-app universe list."""

    __tablename__ = "fccpc_digital_lender"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    app_or_entity_name: Mapped[str] = mapped_column(String(255), nullable=False)
    approval_status: Mapped[Optional[str]] = mapped_column(String(30))  # full / conditional
    institution_id: Mapped[Optional[str]] = mapped_column(ForeignKey("institution.institution_id"))
    retrieved_date: Mapped[Optional[date]] = mapped_column(Date)
    raw: Mapped[Optional[str]] = mapped_column(Text)  # JSON-encoded original record
