#!/usr/bin/env python3
"""Run the ingestion pipeline against a WCDS sample loan tape and print a report.

Usage: python scripts/validate_sample.py [Lender_A_Consumer|Lender_B_SME|Lender_C_MFB]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.ingest.pipeline import ingest_loan_tape  # noqa: E402

SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "standards" / "wcds" / "samples"


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "Lender_A_Consumer"
    path = SAMPLES_DIR / f"{name}.csv"
    if not path.exists():
        print(f"Sample not found: {path}")
        sys.exit(1)

    institution_id = {"Lender_A_Consumer": "LENDER_A", "Lender_B_SME": "LENDER_B", "Lender_C_MFB": "LENDER_C"}[name]

    db = SessionLocal()
    try:
        result = ingest_loan_tape(
            db, path.read_bytes(), path.name,
            default_institution_id=institution_id,
            known_institution_ids={institution_id},
        )
    finally:
        db.close()

    print(f"\n=== {name} ===")
    print(f"rows: {result.row_count}  facilities persisted: {result.facilities_persisted}  status: {result.status}")
    print(f"auto-mapped fields: {len(result.mapping.auto_matched)}  unmapped columns: {result.mapping.unmapped_columns}")
    print(f"missing required fields: {result.mapping.missing_required_fields}")

    print(f"\nvalidation — FATAL:{result.validation.fatal_count} ERROR:{result.validation.error_count} WARNING:{result.validation.warning_count}")
    for row in result.validation.results:
        if row.status == "FAIL":
            print(f"  [{row.severity:7}] {row.rule_id} {row.entity_id} {row.field_name or '':25} {row.message}")

    print("\nreconciliation:")
    for rr in result.reconciliation:
        print(f"  {rr.control_name}: wcds={rr.wcds_control_total} source={rr.source_control_total} within_tolerance={rr.within_tolerance} {rr.notes}")


if __name__ == "__main__":
    main()
