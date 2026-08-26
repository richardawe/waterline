import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingest.canonicalize import canonicalize_row
from app.ingest.loader import load_tape
from app.ingest.mapping import propose_mapping
from app.ingest.pipeline import ingest_loan_tape
from app.ingest.reconcile import reconcile
from app.ingest.validator import validate_dataset
from app.models.deal import Deal, LoanTapeSnapshot
from app.models.tier1 import Institution
from app.models.wcds import Reconciliation, ValidationResult
from app.security import require_admin

router = APIRouter(
    tags=["loan-tapes"],
    dependencies=[Depends(require_admin)],
)
private_router = APIRouter(
    tags=["loan-tapes"],
    dependencies=[Depends(require_admin)],
)


@router.post("/validate")
async def validate_loan_tape(file: UploadFile, institution_id: str | None = None, db: Session = Depends(get_db)):
    """Stateless dry run of the WCDS mapping + validation stages (map ->
    canonicalize -> validate -> reconcile) — no Deal required, nothing
    persisted. Powers the admin workspace assessment: real engine, real
    rulebook, just no database write at the end."""
    file_bytes = await file.read()
    rows_raw, columns, _file_hash = load_tape(file_bytes, file.filename or "upload.csv")
    mapping = propose_mapping(columns)

    canonical_rows: list[dict] = []
    for raw_row in rows_raw:
        canonical, _events, _errors = canonicalize_row(raw_row, mapping.column_to_field)
        if institution_id and not canonical.get("institution_id"):
            canonical["institution_id"] = institution_id
        canonical_rows.append(canonical)

    known_ids = {row[0] for row in db.execute(select(Institution.institution_id)).all()}
    if institution_id:
        known_ids.add(institution_id)

    validation = validate_dataset(canonical_rows, known_institution_ids=known_ids)
    recon = reconcile(canonical_rows)

    return {
        "row_count": len(rows_raw),
        "mapping": {
            "auto_matched": mapping.auto_matched,
            "unmapped_columns": mapping.unmapped_columns,
            "missing_required_fields": mapping.missing_required_fields,
        },
        "validation_summary": {
            "fatal": validation.fatal_count,
            "error": validation.error_count,
            "warning": validation.warning_count,
            "rules": {
                rid: {"passed": s.passed, "failed": s.failed, "skipped": s.skipped, "severity": s.severity}
                for rid, s in validation.summary.items()
            },
        },
        "exceptions": [
            {
                "rule_id": r.rule_id,
                "entity_id": r.entity_id,
                "field_name": r.field_name,
                "severity": r.severity,
                "message": r.message,
            }
            for r in validation.results
            if r.status == "FAIL"
        ],
        "reconciliation": [
            {
                "control_name": r.control_name,
                "source_control_total": r.source_control_total,
                "wcds_control_total": r.wcds_control_total,
                "within_tolerance": r.within_tolerance,
                "notes": r.notes,
            }
            for r in recon
        ],
    }


def _tape_to_dict(t: LoanTapeSnapshot) -> dict:
    return {
        "id": t.id,
        "deal_id": t.deal_id,
        "institution_id": t.institution_id,
        "original_filename": t.original_filename,
        "file_hash": t.file_hash,
        "row_count": t.row_count,
        "status": t.status,
        "wcds_version": t.wcds_version,
        "mapping": json.loads(t.mapping_json) if t.mapping_json else None,
        "received_date": t.received_date.isoformat() if t.received_date else None,
    }


@private_router.post("/deals/{deal_id}/loan-tapes", status_code=201)
async def upload_loan_tape(
    deal_id: str,
    file: UploadFile,
    institution_id: str | None = None,
    declared_facility_count: int | None = None,
    declared_principal_total: float | None = None,
    db: Session = Depends(get_db),
):
    deal = db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "deal not found")

    file_bytes = await file.read()
    result = ingest_loan_tape(
        db,
        file_bytes,
        file.filename or "upload.csv",
        default_institution_id=institution_id or deal.originator_institution_id,
        deal_id=deal_id,
        declared_facility_count=declared_facility_count,
        declared_principal_total=declared_principal_total,
    )

    return {
        "loan_tape_snapshot_id": result.loan_tape_snapshot_id,
        "row_count": result.row_count,
        "facilities_persisted": result.facilities_persisted,
        "status": result.status,
        "mapping": {
            "auto_matched": result.mapping.auto_matched,
            "unmapped_columns": result.mapping.unmapped_columns,
            "missing_required_fields": result.mapping.missing_required_fields,
        },
        "validation_summary": {
            "fatal": result.validation.fatal_count,
            "error": result.validation.error_count,
            "warning": result.validation.warning_count,
            "rules": {
                rid: {"passed": s.passed, "failed": s.failed, "skipped": s.skipped, "severity": s.severity}
                for rid, s in result.validation.summary.items()
            },
        },
        "reconciliation": [
            {
                "control_name": r.control_name,
                "source_control_total": r.source_control_total,
                "wcds_control_total": r.wcds_control_total,
                "within_tolerance": r.within_tolerance,
                "notes": r.notes,
            }
            for r in result.reconciliation
        ],
    }


@private_router.get("/deals/{deal_id}/loan-tapes")
def list_loan_tapes(deal_id: str, db: Session = Depends(get_db)):
    deal = db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "deal not found")
    tapes = (
        db.query(LoanTapeSnapshot)
        .filter(LoanTapeSnapshot.deal_id == deal_id)
        .order_by(LoanTapeSnapshot.received_date.desc())
        .all()
    )
    return [_tape_to_dict(t) for t in tapes]


@private_router.get("/loan-tapes/{tape_id}")
def get_loan_tape(tape_id: str, db: Session = Depends(get_db)):
    tape = db.get(LoanTapeSnapshot, tape_id)
    if tape is None:
        raise HTTPException(404, "loan tape not found")
    return _tape_to_dict(tape)


@private_router.get("/loan-tapes/{tape_id}/validation-results")
def get_validation_results(tape_id: str, status: str | None = "FAIL", db: Session = Depends(get_db)):
    q = db.query(ValidationResult).filter(ValidationResult.loan_tape_snapshot_id == tape_id)
    if status:
        q = q.filter(ValidationResult.status == status)
    rows = q.order_by(ValidationResult.rule_id).limit(2000).all()
    return [
        {
            "rule_id": r.rule_id,
            "entity_id": r.entity_id,
            "field_name": r.field_name,
            "severity": r.severity,
            "status": r.status,
            "message": r.message,
        }
        for r in rows
    ]


@private_router.get("/loan-tapes/{tape_id}/reconciliation")
def get_reconciliation(tape_id: str, db: Session = Depends(get_db)):
    rows = db.query(Reconciliation).filter(Reconciliation.loan_tape_snapshot_id == tape_id).all()
    return [
        {
            "control_name": r.control_name,
            "source_control_total": float(r.source_control_total) if r.source_control_total is not None else None,
            "wcds_control_total": float(r.wcds_control_total) if r.wcds_control_total is not None else None,
            "variance": float(r.variance) if r.variance is not None else None,
            "within_tolerance": r.within_tolerance,
            "notes": r.notes,
        }
        for r in rows
    ]
