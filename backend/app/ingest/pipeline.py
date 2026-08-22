"""Orchestrates the full WCDS ingestion pipeline (spec §16, stages 1-9):
register source -> ingest raw -> map -> canonicalise -> validate -> reconcile ->
publish. Persists Facility/FacilitySnapshot/Party + the audit trail
(SourceRecord/ValidationResult/Reconciliation) to Postgres.

TransformationEvent rows are NOT persisted per-field by default (a 3,000-facility
tape times ~25 mapped fields is 75k rows of "we parsed a date" — real audit
value but real storage cost for an MVP). Lineage is instead reproducible
deterministically from (source file hash + mapping + canonicalize.py version),
which is what `full_lineage=True` trades storage for by persisting them anyway.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ingest.canonicalize import canonicalize_row
from app.ingest.loader import load_tape
from app.ingest.mapping import MappingResult, propose_mapping
from app.ingest.reconcile import ReconciliationResult, reconcile
from app.ingest.validator import ValidationReport, validate_dataset
from app.models.deal import LoanTapeSnapshot
from app.models.tier1 import Institution
from app.models.wcds import (
    Facility,
    FacilityParty,
    FacilitySnapshot,
    Organisation,
    Party,
    Person,
    Reconciliation,
    SourceRecord,
    TransformationEvent,
    ValidationResult,
)

WCDS_VERSION = "0.1.0"


@dataclass
class IngestResult:
    loan_tape_snapshot_id: str
    row_count: int
    mapping: MappingResult
    validation: ValidationReport
    reconciliation: list[ReconciliationResult]
    facilities_persisted: int
    status: str


def _ensure_institution(db: Session, institution_id: str) -> None:
    if db.get(Institution, institution_id) is None:
        db.add(Institution(institution_id=institution_id, legal_name=institution_id, status="stub_from_ingest"))
        db.flush()


def _ensure_party(db: Session, canonical: dict) -> str | None:
    party_id = canonical.get("party_id") or canonical.get("borrower_external_id")
    if not party_id:
        return None
    party_id = str(party_id)
    if db.get(Party, party_id) is None:
        party_type = canonical.get("party_type") or "PERSON"
        db.add(
            Party(
                party_id=party_id,
                party_type=party_type,
                customer_reference=canonical.get("borrower_external_id"),
            )
        )
        db.flush()
        if party_type == "PERSON":
            db.add(Person(party_id=party_id))
        else:
            db.add(Organisation(party_id=party_id))
    return party_id


def _upsert_facility(db: Session, canonical: dict, loan_tape_snapshot_id: str) -> Facility | None:
    facility_id = canonical.get("facility_id") or canonical.get("source_facility_id")
    if not facility_id:
        return None
    facility_id = str(facility_id)

    required = ("institution_id", "source_facility_id", "facility_type", "product_code", "currency",
                "amount_disbursed", "origination_date", "first_disbursement_date", "facility_status")
    if any(canonical.get(f) is None for f in required):
        return None

    facility = db.get(Facility, facility_id)
    if facility is None:
        facility = Facility(facility_id=facility_id)
        db.add(facility)

    facility.institution_id = canonical["institution_id"]
    facility.source_facility_id = str(canonical["source_facility_id"])
    facility.crms_credit_id = canonical.get("crms_credit_id")
    facility.facility_type = canonical["facility_type"]
    facility.product_code = str(canonical["product_code"])
    facility.currency = canonical["currency"]
    facility.approved_limit = canonical.get("approved_limit")
    facility.original_principal = canonical.get("original_principal")
    facility.amount_disbursed = canonical["amount_disbursed"]
    facility.approval_date = canonical.get("approval_date")
    facility.origination_date = canonical["origination_date"]
    facility.first_disbursement_date = canonical["first_disbursement_date"]
    facility.maturity_date = canonical.get("maturity_date")
    facility.original_tenor_days = canonical.get("original_tenor_days")
    facility.interest_rate = canonical.get("interest_rate")
    facility.rate_type = canonical.get("rate_type")
    facility.repayment_frequency = canonical.get("repayment_frequency")
    facility.scheduled_payment_amount = canonical.get("scheduled_payment_amount")
    facility.purpose_code = canonical.get("purpose_code")
    facility.sector_code = canonical.get("sector_code")
    facility.funding_source_code = canonical.get("funding_source_code")
    facility.secured_flag = bool(canonical.get("secured_flag") or False)
    facility.facility_status = canonical["facility_status"]
    facility.loan_tape_snapshot_id = loan_tape_snapshot_id
    return facility


def _upsert_snapshot(db: Session, canonical: dict, facility_id: str) -> None:
    snapshot_date = canonical.get("snapshot_date")
    if snapshot_date is None or canonical.get("outstanding_principal") is None:
        return
    existing = (
        db.query(FacilitySnapshot)
        .filter_by(facility_id=facility_id, snapshot_date=snapshot_date, snapshot_type="MONTHLY")
        .one_or_none()
    )
    snap = existing or FacilitySnapshot(facility_id=facility_id, snapshot_date=snapshot_date, snapshot_type="MONTHLY")
    snap.outstanding_principal = canonical.get("outstanding_principal") or 0
    snap.total_exposure = canonical.get("total_exposure") or canonical.get("outstanding_principal") or 0
    snap.days_past_due = int(canonical.get("days_past_due") or 0)
    snap.delinquency_bucket = canonical.get("delinquency_bucket") or "CURRENT"
    snap.default_flag = bool(canonical.get("default_flag") or False)
    snap.default_date = canonical.get("default_date")
    snap.restructure_flag = bool(canonical.get("restructure_flag") or False)
    snap.writeoff_flag = bool(canonical.get("writeoff_flag") or False)
    snap.writeoff_amount = canonical.get("writeoff_amount")
    snap.recovery_amount = canonical.get("recovery_amount")
    if existing is None:
        db.add(snap)


def ingest_loan_tape(
    db: Session,
    file_bytes: bytes,
    filename: str,
    default_institution_id: str | None = None,
    deal_id: str | None = None,
    column_overrides: dict[str, str] | None = None,
    declared_facility_count: int | None = None,
    declared_principal_total: float | None = None,
    full_lineage: bool = False,
    known_institution_ids: set[str] | None = None,
) -> IngestResult:
    rows_raw, columns, file_hash = load_tape(file_bytes, filename)
    mapping = propose_mapping(columns, column_overrides)

    if default_institution_id:
        _ensure_institution(db, default_institution_id)

    tape = LoanTapeSnapshot(
        deal_id=deal_id,
        institution_id=default_institution_id,
        original_filename=filename,
        file_hash=file_hash,
        row_count=len(rows_raw),
        received_date=datetime.now(timezone.utc),
        status="mapped",
        mapping_json=json.dumps(mapping.column_to_field),
        wcds_version=WCDS_VERSION,
    )
    db.add(tape)
    db.flush()

    canonical_rows: list[dict] = []
    for raw_row in rows_raw:
        canonical, events, errors = canonicalize_row(raw_row, mapping.column_to_field)
        if default_institution_id and not canonical.get("institution_id"):
            canonical["institution_id"] = default_institution_id
        canonical_rows.append(canonical)

        payload_json = json.dumps({k: str(v) for k, v in raw_row.items()}, default=str)
        source_record = SourceRecord(
            loan_tape_snapshot_id=tape.id,
            source_system=filename,
            source_primary_key=str(canonical.get("facility_id") or canonical.get("source_facility_id") or ""),
            ingested_at=datetime.now(timezone.utc),
            payload_hash=hashlib.sha256(payload_json.encode()).hexdigest(),
            payload_json=payload_json,
        )
        db.add(source_record)
        db.flush()

        if full_lineage:
            for ev in events:
                db.add(
                    TransformationEvent(
                        source_record_id=source_record.source_record_id,
                        target_entity="facility_or_snapshot",
                        target_field=ev.field_name,
                        original_value=str(ev.original_value),
                        normalised_value=str(ev.normalised_value),
                        rule_id=ev.rule_id,
                        rule_version=WCDS_VERSION,
                    )
                )
        for field_name, error_msg in errors.items():
            db.add(
                ValidationResult(
                    loan_tape_snapshot_id=tape.id,
                    rule_id="INGEST-PARSE",
                    entity_id=str(canonical.get("facility_id") or canonical.get("source_facility_id") or "<unknown>"),
                    field_name=field_name,
                    severity="ERROR",
                    status="FAIL",
                    message=error_msg,
                )
            )

    # R002 must check against institutions that already exist (or are explicitly
    # declared), not against the tape's own institution_id column — otherwise every
    # facility trivially "resolves" to itself and the rule never fires.
    known_ids = set(known_institution_ids or set())
    if default_institution_id:
        known_ids.add(default_institution_id)
    known_ids |= {row[0] for row in db.query(Institution.institution_id).all()}

    validation = validate_dataset(canonical_rows, known_institution_ids=known_ids)
    for vr in validation.results:
        db.add(
            ValidationResult(
                loan_tape_snapshot_id=tape.id,
                rule_id=vr.rule_id,
                entity_id=vr.entity_id,
                field_name=vr.field_name,
                severity=vr.severity,
                status=vr.status,
                message=vr.message,
            )
        )

    recon = reconcile(
        canonical_rows,
        declared_facility_count=declared_facility_count,
        declared_principal_total=declared_principal_total,
    )
    for rr in recon:
        db.add(
            Reconciliation(
                loan_tape_snapshot_id=tape.id,
                control_name=rr.control_name,
                source_control_total=rr.source_control_total,
                wcds_control_total=rr.wcds_control_total,
                variance=rr.variance,
                within_tolerance=rr.within_tolerance,
                notes=rr.notes,
            )
        )

    persisted = 0
    for canonical in canonical_rows:
        inst_id = canonical.get("institution_id")
        if inst_id:
            _ensure_institution(db, str(inst_id))
        party_id = _ensure_party(db, canonical)
        facility = _upsert_facility(db, canonical, tape.id)
        if facility is None:
            continue
        db.flush()
        if party_id and not (
            db.query(FacilityParty).filter_by(facility_id=facility.facility_id, party_id=party_id).first()
        ):
            db.add(FacilityParty(facility_id=facility.facility_id, party_id=party_id, role="BORROWER"))
        _upsert_snapshot(db, canonical, facility.facility_id)
        persisted += 1

    tape.status = "failed" if validation.fatal_count > 0 else "validated"
    db.commit()

    return IngestResult(
        loan_tape_snapshot_id=tape.id,
        row_count=len(rows_raw),
        mapping=mapping,
        validation=validation,
        reconciliation=recon,
        facilities_persisted=persisted,
        status=tape.status,
    )
