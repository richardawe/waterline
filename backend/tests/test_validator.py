from datetime import date

from app.ingest.validator import validate_dataset

BASE_ROW = {
    "facility_id": "FAC-1",
    "institution_id": "LENDER_X",
    "source_facility_id": "SRC-1",
    "crms_credit_id": None,
    "amount_disbursed": 100000.0,
    "outstanding_principal": 50000.0,
    "total_exposure": 50000.0,
    "approval_date": date(2026, 1, 1),
    "origination_date": date(2026, 1, 1),
    "first_disbursement_date": date(2026, 1, 1),
    "maturity_date": date(2026, 6, 1),
    "snapshot_date": date(2026, 3, 1),
    "days_past_due": 0,
    "delinquency_bucket": "CURRENT",
    "default_flag": False,
    "default_date": None,
    "restructure_flag": False,
    "writeoff_flag": False,
    "writeoff_amount": None,
    "interest_rate": 0.24,
    "currency": "NGN",
    "facility_status": "ACTIVE",
    "party_id": "PTY-1",
    "borrower_external_id": "EXT-1",
    "party_type": "PERSON",
}


def _failed_rules(report):
    return {r.rule_id for r in report.results if r.status == "FAIL"}


def test_clean_row_has_no_failures():
    report = validate_dataset([dict(BASE_ROW)], known_institution_ids={"LENDER_X"})
    assert report.fatal_count == 0
    assert report.error_count == 0


def test_catches_negative_outstanding_principal():
    row = dict(BASE_ROW, outstanding_principal=-100.0)
    report = validate_dataset([row], known_institution_ids={"LENDER_X"})
    assert "WCDS-R008" in _failed_rules(report)


def test_catches_negative_dpd():
    row = dict(BASE_ROW, days_past_due=-5)
    report = validate_dataset([row], known_institution_ids={"LENDER_X"})
    assert "WCDS-R014" in _failed_rules(report)


def test_catches_default_flag_without_default_date():
    row = dict(BASE_ROW, default_flag=True, default_date=None)
    report = validate_dataset([row], known_institution_ids={"LENDER_X"})
    assert "WCDS-R013" in _failed_rules(report)


def test_catches_closed_facility_with_nonzero_exposure():
    row = dict(BASE_ROW, facility_status="CLOSED", outstanding_principal=500.0)
    report = validate_dataset([row], known_institution_ids={"LENDER_X"})
    assert "WCDS-R012" in _failed_rules(report)


def test_catches_origination_after_maturity():
    row = dict(BASE_ROW, origination_date=date(2026, 6, 1), maturity_date=date(2026, 1, 1))
    report = validate_dataset([row], known_institution_ids={"LENDER_X"})
    assert "WCDS-R010" in _failed_rules(report)


def test_catches_bucket_dpd_mismatch():
    row = dict(BASE_ROW, days_past_due=0, delinquency_bucket="DPD_31_60")
    report = validate_dataset([row], known_institution_ids={"LENDER_X"})
    assert "WCDS-R015" in _failed_rules(report)


def test_catches_rate_entered_as_percentage_not_decimal():
    row = dict(BASE_ROW, interest_rate=26.0)
    report = validate_dataset([row], known_institution_ids={"LENDER_X"})
    assert "WCDS-R021" in _failed_rules(report)


def test_catches_invalid_currency():
    row = dict(BASE_ROW, currency="NRA")
    report = validate_dataset([row], known_institution_ids={"LENDER_X"})
    assert "WCDS-R022" in _failed_rules(report)


def test_catches_duplicate_crms_credit_id():
    row_a = dict(BASE_ROW, facility_id="FAC-1", crms_credit_id="CRMS-DUP")
    row_b = dict(BASE_ROW, facility_id="FAC-2", source_facility_id="SRC-2", crms_credit_id="CRMS-DUP")
    report = validate_dataset([row_a, row_b], known_institution_ids={"LENDER_X"})
    assert "WCDS-R001" in _failed_rules(report)


def test_catches_unresolved_institution():
    row = dict(BASE_ROW)
    report = validate_dataset([row], known_institution_ids={"SOME_OTHER_LENDER"})
    assert "WCDS-R002" in _failed_rules(report)
