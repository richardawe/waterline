"""Tier 2 — Waterline Credit Data Standard (WCDS) v0.1 canonical model.

Field-for-field from standards/wcds/WCDS_v0.1_Full_Specification.docx §4-§5.
`Institution` lives in models/tier1.py (shared identity). Primary keys are kept
as strings rather than enforced UUIDv7 — the spec (§4.1) only requires
organisation-wide uniqueness, and a real source `facility_id`/`source_facility_id`
is often more useful to carry through untouched than a synthetic replacement.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid

MONEY = Numeric(20, 2)
RATE = Numeric(12, 8)


class Party(Base, TimestampMixin):
    __tablename__ = "party"

    party_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    party_type: Mapped[str] = mapped_column(String(20), nullable=False)  # PERSON / ORGANISATION
    customer_reference: Mapped[Optional[str]] = mapped_column(String(100))
    relationship_start_date: Mapped[Optional[date]] = mapped_column(Date)
    address_line_1: Mapped[Optional[str]] = mapped_column(String(255))
    address_line_2: Mapped[Optional[str]] = mapped_column(String(255))
    city: Mapped[Optional[str]] = mapped_column(String(150))
    state_code: Mapped[Optional[str]] = mapped_column(String(20))
    lga_code: Mapped[Optional[str]] = mapped_column(String(20))
    country_code: Mapped[Optional[str]] = mapped_column(String(2), default="NG")

    person: Mapped[Optional["Person"]] = relationship(back_populates="party", uselist=False)
    organisation: Mapped[Optional["Organisation"]] = relationship(back_populates="party", uselist=False)


class Person(Base, TimestampMixin):
    __tablename__ = "person"

    party_id: Mapped[str] = mapped_column(ForeignKey("party.party_id"), primary_key=True)
    bvn: Mapped[Optional[str]] = mapped_column(String(11))
    nin: Mapped[Optional[str]] = mapped_column(String(11))
    surname: Mapped[Optional[str]] = mapped_column(String(150))
    first_name: Mapped[Optional[str]] = mapped_column(String(150))
    middle_name: Mapped[Optional[str]] = mapped_column(String(150))
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)
    gender: Mapped[Optional[str]] = mapped_column(String(10))
    nationality_code: Mapped[Optional[str]] = mapped_column(String(2))
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    email: Mapped[Optional[str]] = mapped_column(String(254))
    occupation_code: Mapped[Optional[str]] = mapped_column(String(30))
    employer_name: Mapped[Optional[str]] = mapped_column(String(255))

    party: Mapped["Party"] = relationship(back_populates="person")


class Organisation(Base, TimestampMixin):
    __tablename__ = "organisation"

    party_id: Mapped[str] = mapped_column(ForeignKey("party.party_id"), primary_key=True)
    tin: Mapped[Optional[str]] = mapped_column(String(50))
    legal_name: Mapped[Optional[str]] = mapped_column(String(255))
    trading_name: Mapped[Optional[str]] = mapped_column(String(255))
    registration_number: Mapped[Optional[str]] = mapped_column(String(100))
    incorporation_date: Mapped[Optional[date]] = mapped_column(Date)
    business_type_code: Mapped[Optional[str]] = mapped_column(String(30))
    sector_code: Mapped[Optional[str]] = mapped_column(String(50))
    industry_code: Mapped[Optional[str]] = mapped_column(String(50))

    party: Mapped["Party"] = relationship(back_populates="organisation")


class RelatedParty(Base, TimestampMixin):
    __tablename__ = "related_party"

    related_party_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    organisation_party_id: Mapped[str] = mapped_column(ForeignKey("organisation.party_id"), nullable=False)
    person_party_id: Mapped[str] = mapped_column(ForeignKey("person.party_id"), nullable=False)
    relationship_role: Mapped[str] = mapped_column(String(30), nullable=False)
    ownership_percentage: Mapped[Optional[float]] = mapped_column(Numeric(7, 4))
    appointment_date: Mapped[Optional[date]] = mapped_column(Date)


class Facility(Base, TimestampMixin):
    __tablename__ = "facility"
    __table_args__ = (UniqueConstraint("institution_id", "source_facility_id", name="uq_facility_source_id"),)

    facility_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institution.institution_id"), nullable=False)
    source_facility_id: Mapped[str] = mapped_column(String(150), nullable=False)
    crms_credit_id: Mapped[Optional[str]] = mapped_column(String(150))
    account_reference: Mapped[Optional[str]] = mapped_column(String(150))

    facility_type: Mapped[str] = mapped_column(String(30), nullable=False)
    product_code: Mapped[str] = mapped_column(String(100), nullable=False)
    product_name: Mapped[Optional[str]] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    approved_limit: Mapped[Optional[float]] = mapped_column(MONEY)
    original_principal: Mapped[Optional[float]] = mapped_column(MONEY)
    amount_disbursed: Mapped[float] = mapped_column(MONEY, nullable=False)

    approval_date: Mapped[Optional[date]] = mapped_column(Date)
    origination_date: Mapped[date] = mapped_column(Date, nullable=False)
    first_disbursement_date: Mapped[date] = mapped_column(Date, nullable=False)
    maturity_date: Mapped[Optional[date]] = mapped_column(Date)
    original_tenor_days: Mapped[Optional[int]] = mapped_column(Integer)

    interest_rate: Mapped[Optional[float]] = mapped_column(RATE)
    rate_type: Mapped[Optional[str]] = mapped_column(String(20))
    repayment_frequency: Mapped[Optional[str]] = mapped_column(String(20))
    scheduled_payment_amount: Mapped[Optional[float]] = mapped_column(MONEY)

    purpose_code: Mapped[Optional[str]] = mapped_column(String(50))
    sector_code: Mapped[Optional[str]] = mapped_column(String(50))
    funding_source_code: Mapped[Optional[str]] = mapped_column(String(50))
    secured_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    facility_status: Mapped[str] = mapped_column(String(20), nullable=False)

    loan_tape_snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("loan_tape_snapshot.id"))

    parties: Mapped[list["FacilityParty"]] = relationship(back_populates="facility")
    collateral: Mapped[list["Collateral"]] = relationship(back_populates="facility")
    snapshots: Mapped[list["FacilitySnapshot"]] = relationship(back_populates="facility")
    payments: Mapped[list["Payment"]] = relationship(back_populates="facility")


class FacilityParty(Base, TimestampMixin):
    __tablename__ = "facility_party"

    facility_party_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facility.facility_id"), nullable=False)
    party_id: Mapped[str] = mapped_column(ForeignKey("party.party_id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # BORROWER/CO_BORROWER/GUARANTOR/...
    liability_percentage: Mapped[Optional[float]] = mapped_column(Numeric(7, 4))

    facility: Mapped["Facility"] = relationship(back_populates="parties")


class Collateral(Base, TimestampMixin):
    __tablename__ = "collateral"

    collateral_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facility.facility_id"), nullable=False)
    collateral_type_code: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500))
    original_value: Mapped[Optional[float]] = mapped_column(MONEY)
    current_value: Mapped[Optional[float]] = mapped_column(MONEY)
    valuation_date: Mapped[Optional[date]] = mapped_column(Date)
    perfection_status: Mapped[Optional[str]] = mapped_column(String(20))
    security_rank: Mapped[Optional[int]] = mapped_column(Integer)

    facility: Mapped["Facility"] = relationship(back_populates="collateral")


class Guarantee(Base, TimestampMixin):
    __tablename__ = "guarantee"

    guarantee_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facility.facility_id"), nullable=False)
    guarantor_party_id: Mapped[str] = mapped_column(ForeignKey("party.party_id"), nullable=False)
    guarantee_type: Mapped[str] = mapped_column(String(20), nullable=False)
    guarantee_amount: Mapped[Optional[float]] = mapped_column(MONEY)


class Restructure(Base, TimestampMixin):
    __tablename__ = "restructure"

    restructure_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facility.facility_id"), nullable=False)
    restructure_date: Mapped[date] = mapped_column(Date, nullable=False)
    restructure_type: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_code: Mapped[Optional[str]] = mapped_column(String(50))
    previous_principal_balance: Mapped[Optional[float]] = mapped_column(MONEY)
    revised_principal_balance: Mapped[Optional[float]] = mapped_column(MONEY)
    previous_interest_rate: Mapped[Optional[float]] = mapped_column(RATE)
    revised_interest_rate: Mapped[Optional[float]] = mapped_column(RATE)
    previous_maturity_date: Mapped[Optional[date]] = mapped_column(Date)
    revised_maturity_date: Mapped[Optional[date]] = mapped_column(Date)
    capitalised_interest: Mapped[Optional[float]] = mapped_column(MONEY)


class Schedule(Base, TimestampMixin):
    __tablename__ = "schedule"

    schedule_item_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facility.facility_id"), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    principal_due: Mapped[float] = mapped_column(MONEY, nullable=False)
    interest_due: Mapped[float] = mapped_column(MONEY, nullable=False)
    fees_due: Mapped[Optional[float]] = mapped_column(MONEY)


class Payment(Base, TimestampMixin):
    __tablename__ = "payment"

    payment_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facility.facility_id"), nullable=False)
    source_transaction_id: Mapped[Optional[str]] = mapped_column(String(150))
    payment_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    payment_amount: Mapped[float] = mapped_column(MONEY, nullable=False)
    principal_component: Mapped[Optional[float]] = mapped_column(MONEY)
    interest_component: Mapped[Optional[float]] = mapped_column(MONEY)
    fee_component: Mapped[Optional[float]] = mapped_column(MONEY)
    recovery_component: Mapped[Optional[float]] = mapped_column(MONEY)
    reversal_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    facility: Mapped["Facility"] = relationship(back_populates="payments")


class FacilitySnapshot(Base, TimestampMixin):
    __tablename__ = "facility_snapshot"
    __table_args__ = (
        UniqueConstraint("facility_id", "snapshot_date", "snapshot_type", name="uq_snapshot_facility_date_type"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facility.facility_id"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    snapshot_type: Mapped[str] = mapped_column(String(20), nullable=False, default="MONTHLY")

    opening_principal_balance: Mapped[Optional[float]] = mapped_column(MONEY)
    outstanding_principal: Mapped[float] = mapped_column(MONEY, nullable=False)
    accrued_interest: Mapped[Optional[float]] = mapped_column(MONEY)
    fees_outstanding: Mapped[Optional[float]] = mapped_column(MONEY)
    total_exposure: Mapped[float] = mapped_column(MONEY, nullable=False)
    past_due_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    days_past_due: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_payment_date: Mapped[Optional[date]] = mapped_column(Date)
    next_payment_date: Mapped[Optional[date]] = mapped_column(Date)
    delinquency_bucket: Mapped[str] = mapped_column(String(20), nullable=False)
    default_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_date: Mapped[Optional[date]] = mapped_column(Date)
    restructure_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    writeoff_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    writeoff_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    recovery_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    month_end_balance: Mapped[Optional[float]] = mapped_column(MONEY)

    facility: Mapped["Facility"] = relationship(back_populates="snapshots")


class CreditRisk(Base, TimestampMixin):
    __tablename__ = "credit_risk"
    __table_args__ = (UniqueConstraint("facility_id", "assessment_date", name="uq_credit_risk_facility_date"),)

    credit_risk_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facility.facility_id"), nullable=False)
    assessment_date: Mapped[date] = mapped_column(Date, nullable=False)
    ifrs9_stage: Mapped[Optional[str]] = mapped_column(String(10))
    sicr_flag: Mapped[Optional[bool]] = mapped_column(Boolean)
    pd_12m: Mapped[Optional[float]] = mapped_column(RATE)
    pd_lifetime: Mapped[Optional[float]] = mapped_column(RATE)
    lgd: Mapped[Optional[float]] = mapped_column(RATE)
    ead: Mapped[Optional[float]] = mapped_column(MONEY)
    ecl_amount: Mapped[Optional[float]] = mapped_column(MONEY)
    model_version: Mapped[Optional[str]] = mapped_column(String(100))


class SourceRecord(Base, TimestampMixin):
    __tablename__ = "source_record"

    source_record_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    loan_tape_snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("loan_tape_snapshot.id"))
    source_system: Mapped[str] = mapped_column(String(150), nullable=False)
    source_table: Mapped[Optional[str]] = mapped_column(String(150))
    source_primary_key: Mapped[str] = mapped_column(String(500), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[Optional[str]] = mapped_column(Text)


class TransformationEvent(Base, TimestampMixin):
    __tablename__ = "transformation_event"

    transformation_event_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    source_record_id: Mapped[str] = mapped_column(ForeignKey("source_record.source_record_id"), nullable=False)
    target_entity: Mapped[str] = mapped_column(String(100), nullable=False)
    target_field: Mapped[str] = mapped_column(String(100), nullable=False)
    original_value: Mapped[Optional[str]] = mapped_column(Text)
    normalised_value: Mapped[Optional[str]] = mapped_column(Text)
    rule_id: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(20), nullable=False, default="0.1.0")


class ValidationResult(Base, TimestampMixin):
    __tablename__ = "validation_result"

    validation_result_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    loan_tape_snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("loan_tape_snapshot.id"))
    rule_id: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(150), nullable=False)
    field_name: Mapped[Optional[str]] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # INFO/WARNING/ERROR/FATAL
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # PASS/FAIL/SKIPPED
    message: Mapped[str] = mapped_column(String(1000), nullable=False)


class ReportingSubmission(Base, TimestampMixin):
    __tablename__ = "reporting_submission"

    submission_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    destination: Mapped[str] = mapped_column(String(30), nullable=False)
    destination_schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    reporting_date: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    control_total: Mapped[Optional[float]] = mapped_column(Numeric(24, 2))
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Reconciliation(Base, TimestampMixin):
    __tablename__ = "reconciliation"

    reconciliation_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    loan_tape_snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("loan_tape_snapshot.id"))
    control_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_control_total: Mapped[Optional[float]] = mapped_column(Numeric(24, 2))
    wcds_control_total: Mapped[Optional[float]] = mapped_column(Numeric(24, 2))
    variance: Mapped[Optional[float]] = mapped_column(Numeric(24, 2))
    within_tolerance: Mapped[Optional[bool]] = mapped_column(Boolean)
    notes: Mapped[Optional[str]] = mapped_column(Text)
