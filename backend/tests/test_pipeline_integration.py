"""Runs the full ingestion pipeline against the real WCDS sample loan tapes,
against a live Postgres instance, inside a rolled-back transaction. Confirms the
deliberate exceptions documented in standards/wcds/README.md are actually caught
end-to-end (mapping -> canonicalize -> persist -> validate -> reconcile), not
just by the pure validate_dataset() unit tests.
"""

from app.ingest.pipeline import ingest_loan_tape
from tests.conftest import SAMPLES_DIR, requires_db


@requires_db
def test_lender_a_catches_all_documented_exceptions(db_session):
    path = SAMPLES_DIR / "Lender_A_Consumer.csv"
    result = ingest_loan_tape(
        db_session,
        path.read_bytes(),
        path.name,
        default_institution_id="LENDER_A",
        known_institution_ids={"LENDER_A"},
    )
    assert result.facilities_persisted == 1200
    failed_rules = {r.rule_id for r in result.validation.results if r.status == "FAIL"}
    # negative outstanding_principal, negative DPD, default w/o default_date, CLOSED w/ nonzero exposure
    assert {"WCDS-R008", "WCDS-R014", "WCDS-R013", "WCDS-R012"} <= failed_rules


@requires_db
def test_lender_b_catches_all_documented_exceptions(db_session):
    path = SAMPLES_DIR / "Lender_B_SME.csv"
    result = ingest_loan_tape(
        db_session,
        path.read_bytes(),
        path.name,
        default_institution_id="LENDER_B",
        known_institution_ids={"LENDER_B"},
    )
    failed_rules = {r.rule_id for r in result.validation.results if r.status == "FAIL"}
    # invalid currency, rate as percentage not decimal, bucket/DPD mismatch, origination after maturity
    assert {"WCDS-R022", "WCDS-R021", "WCDS-R015", "WCDS-R010"} <= failed_rules


@requires_db
def test_lender_c_catches_all_documented_exceptions(db_session):
    path = SAMPLES_DIR / "Lender_C_MFB.csv"
    result = ingest_loan_tape(
        db_session,
        path.read_bytes(),
        path.name,
        default_institution_id="LENDER_C",
        known_institution_ids={"LENDER_C"},
    )
    failed_rules = {r.rule_id for r in result.validation.results if r.status == "FAIL"}
    # dates before origination, default_date before origination, duplicate crms_credit_id
    assert {"WCDS-R011", "WCDS-R013", "WCDS-R001"} <= failed_rules
    assert result.validation.fatal_count >= 1  # the duplicate crms_credit_id is FATAL
