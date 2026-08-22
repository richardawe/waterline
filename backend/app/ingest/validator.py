"""L3 — Deterministic validation rulebook (WCDS spec §8, WCDS-R001..R030).

Operates on the flattened Facility+FacilitySnapshot+Party row shape produced by
canonicalize.canonicalize_row() — one dict per source loan-tape row, matching
standards/wcds/samples/*.csv. Entities WCDS defines but the flat-tape mapper
doesn't populate (Collateral, Restructure, Payment, Schedule, CreditRisk, BVN/TIN
identity) are reported SKIPPED rather than silently omitted — the rule fired,
there was just nothing in scope to check it against.

Only FAIL/SKIPPED outcomes are materialized as ValidationResultRow objects (a
30k-facility tape run through ~20 rules is 600k rows of mostly "PASS", which is
storage noise, not audit value) — `RuleSummary` carries the pass/fail/skip
counts per rule for the reconciliation-style rollup investors and analysts
actually want to see.
"""

from dataclasses import dataclass, field
from datetime import date

ISO_4217_COMMON = {
    "NGN", "USD", "EUR", "GBP", "ZAR", "GHS", "KES", "XOF", "XAF", "EGP",
    "JPY", "CNY", "CHF", "CAD", "AUD", "AED", "INR", "BRL", "MXN", "SEK",
    "NOK", "DKK", "SGD", "HKD", "NZD", "TRY", "SAR", "QAR", "MAD", "TZS",
    "UGX", "RWF", "ETB", "ZMW",
}

DPD_BUCKETS = [
    (0, 0, "CURRENT"),
    (1, 30, "DPD_1_30"),
    (31, 60, "DPD_31_60"),
    (61, 90, "DPD_61_90"),
]


def expected_bucket(dpd: int) -> str:
    for lo, hi, name in DPD_BUCKETS:
        if lo <= dpd <= hi:
            return name
    return "DPD_90_PLUS"


@dataclass
class ValidationResultRow:
    rule_id: str
    entity_id: str
    field_name: str | None
    severity: str  # INFO/WARNING/ERROR/FATAL
    status: str  # FAIL/SKIPPED (PASS rows are not materialized, see module docstring)
    message: str


@dataclass
class RuleSummary:
    rule_id: str
    severity: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass
class ValidationReport:
    results: list[ValidationResultRow] = field(default_factory=list)
    summary: dict[str, RuleSummary] = field(default_factory=dict)

    @property
    def fatal_count(self) -> int:
        return sum(1 for r in self.results if r.status == "FAIL" and r.severity == "FATAL")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.status == "FAIL" and r.severity == "ERROR")

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if r.status == "FAIL" and r.severity == "WARNING")


class _Recorder:
    def __init__(self, report: ValidationReport):
        self.report = report

    def _bump(self, rule_id: str, severity: str, key: str) -> None:
        s = self.report.summary.setdefault(rule_id, RuleSummary(rule_id, severity))
        setattr(s, key, getattr(s, key) + 1)

    def fail(self, rule_id: str, severity: str, entity_id: str, field_name: str | None, message: str) -> None:
        self.report.results.append(ValidationResultRow(rule_id, entity_id, field_name, severity, "FAIL", message))
        self._bump(rule_id, severity, "failed")

    def skip(self, rule_id: str, severity: str, entity_id: str, field_name: str | None, message: str) -> None:
        self.report.results.append(ValidationResultRow(rule_id, entity_id, field_name, severity, "SKIPPED", message))
        self._bump(rule_id, severity, "skipped")

    def ok(self, rule_id: str, severity: str) -> None:
        self._bump(rule_id, severity, "passed")


def _entity_id(row: dict) -> str:
    return str(row.get("facility_id") or row.get("source_facility_id") or "<unknown>")


def _validate_row(row: dict, r: _Recorder, known_institution_ids: set[str] | None) -> None:
    eid = _entity_id(row)

    # R001 — primary key present
    if not row.get("facility_id") and not row.get("source_facility_id"):
        r.fail("WCDS-R001", "FATAL", eid, "facility_id", "Facility has no facility_id or source_facility_id.")
    else:
        r.ok("WCDS-R001", "FATAL")

    # R002 — facility originator resolves to Institution
    inst = row.get("institution_id")
    if not inst:
        r.fail("WCDS-R002", "FATAL", eid, "institution_id", "institution_id is missing.")
    elif known_institution_ids is not None and inst not in known_institution_ids:
        r.fail("WCDS-R002", "FATAL", eid, "institution_id", f"institution_id {inst!r} does not resolve to a known Institution.")
    else:
        r.ok("WCDS-R002", "FATAL")

    # R003 — borrower link (flat tape: one party per row, assumed BORROWER)
    if not row.get("party_id") and not row.get("borrower_external_id"):
        r.fail("WCDS-R003", "FATAL", eid, "party_id", "Facility has no linked borrower party.")
    else:
        r.ok("WCDS-R003", "FATAL")

    # R004/R005/R006 — identity fields not carried by the flat tape mapper
    r.skip("WCDS-R004", "ERROR", eid, "bvn", "BVN not present in flat loan-tape mapping; use Party/Person intake to check.")
    r.skip("WCDS-R005", "ERROR", eid, "tin", "TIN not present in flat loan-tape mapping; use Party/Organisation intake to check.")
    r.skip("WCDS-R006", "ERROR", eid, None, "RelatedParty/director records not in scope of flat loan-tape mapping.")

    # R007 — amount_disbursed >= 0
    disbursed = row.get("amount_disbursed")
    if disbursed is not None:
        if disbursed < 0:
            r.fail("WCDS-R007", "ERROR", eid, "amount_disbursed", f"amount_disbursed is negative: {disbursed}.")
        else:
            r.ok("WCDS-R007", "ERROR")

    # R008 — outstanding_principal >= 0
    outstanding = row.get("outstanding_principal")
    if outstanding is not None:
        if outstanding < 0:
            r.fail("WCDS-R008", "ERROR", eid, "outstanding_principal", f"outstanding_principal is negative: {outstanding}.")
        else:
            r.ok("WCDS-R008", "ERROR")

    # R009 — approval_date <= first_disbursement_date
    approval, first_disb = row.get("approval_date"), row.get("first_disbursement_date")
    if approval and first_disb:
        if approval > first_disb:
            r.fail("WCDS-R009", "ERROR", eid, "approval_date", f"approval_date {approval} is after first_disbursement_date {first_disb}.")
        else:
            r.ok("WCDS-R009", "ERROR")

    # R010 — origination_date <= maturity_date
    origination, maturity = row.get("origination_date"), row.get("maturity_date")
    if origination and maturity:
        if origination > maturity:
            r.fail("WCDS-R010", "ERROR", eid, "origination_date", f"origination_date {origination} is after maturity_date {maturity}.")
        else:
            r.ok("WCDS-R010", "ERROR")

    # R011 — snapshot/disbursement timing must not precede origination
    snapshot_date = row.get("snapshot_date")
    if origination and snapshot_date:
        if snapshot_date < origination:
            r.fail("WCDS-R011", "ERROR", eid, "snapshot_date", f"snapshot_date {snapshot_date} precedes origination_date {origination}.")
        else:
            r.ok("WCDS-R011", "ERROR")
    if origination and first_disb:
        if first_disb < origination:
            r.fail("WCDS-R011", "ERROR", eid, "first_disbursement_date", f"first_disbursement_date {first_disb} precedes origination_date {origination}.")
        else:
            r.ok("WCDS-R011", "ERROR")

    # R012 — CLOSED facility should have zero exposure
    status = row.get("facility_status")
    if status == "CLOSED" and outstanding is not None:
        if outstanding != 0:
            r.fail("WCDS-R012", "ERROR", eid, "outstanding_principal", f"facility_status=CLOSED but outstanding_principal={outstanding} (expected 0).")
        else:
            r.ok("WCDS-R012", "ERROR")

    # R013 — default_flag requires default_date, and default_date must not precede origination
    default_flag, default_date = row.get("default_flag"), row.get("default_date")
    if default_flag is True:
        if not default_date:
            r.fail("WCDS-R013", "ERROR", eid, "default_date", "default_flag=true but default_date is missing.")
        elif origination and default_date < origination:
            r.fail("WCDS-R013", "ERROR", eid, "default_date", f"default_date {default_date} precedes origination_date {origination}.")
        else:
            r.ok("WCDS-R013", "ERROR")
    elif default_flag is False:
        r.ok("WCDS-R013", "ERROR")

    # R014 — DPD domain: integer >= 0
    dpd = row.get("days_past_due")
    if dpd is not None:
        if dpd < 0:
            r.fail("WCDS-R014", "ERROR", eid, "days_past_due", f"days_past_due is negative: {dpd}.")
        else:
            r.ok("WCDS-R014", "ERROR")

    # R015 — delinquency_bucket must agree with days_past_due
    bucket = row.get("delinquency_bucket")
    if dpd is not None and dpd >= 0 and bucket:
        exp = expected_bucket(dpd)
        if bucket != exp:
            r.fail("WCDS-R015", "ERROR", eid, "delinquency_bucket", f"delinquency_bucket={bucket} inconsistent with days_past_due={dpd} (expected {exp}).")
        else:
            r.ok("WCDS-R015", "ERROR")

    # R016 — restructure integrity (Restructure entity out of flat-tape scope)
    if row.get("restructure_flag") is True:
        r.skip("WCDS-R016", "ERROR", eid, "restructure_flag", "restructure_flag=true; no Restructure records available in flat loan-tape mapping to confirm.")

    # R017 — write-off integrity
    if row.get("writeoff_flag") is True:
        woa = row.get("writeoff_amount")
        if woa is None:
            r.fail("WCDS-R017", "ERROR", eid, "writeoff_amount", "writeoff_flag=true but writeoff_amount is missing.")
        elif woa == 0:
            r.fail("WCDS-R017", "WARNING", eid, "writeoff_amount", "writeoff_flag=true with writeoff_amount=0 — confirm zero-value write-off policy.")
        else:
            r.ok("WCDS-R017", "ERROR")

    # R018 — collateral integrity (Collateral entity out of flat-tape scope)
    if row.get("secured_flag") is True:
        r.skip("WCDS-R018", "ERROR", eid, "secured_flag", "secured_flag=true; no Collateral records available in flat loan-tape mapping to confirm.")

    # R019/R020 — payment decomposition / schedule balance (Payment/Schedule out of scope)
    r.skip("WCDS-R019", "ERROR", eid, None, "Payment records not in scope of flat loan-tape mapping.")
    r.skip("WCDS-R020", "ERROR", eid, None, "Schedule records not in scope of flat loan-tape mapping.")

    # R021 — interest rate must be canonical decimal form (0..10 per spec §5.6)
    rate = row.get("interest_rate")
    if rate is not None:
        if not (0 <= rate <= 10):
            r.fail("WCDS-R021", "ERROR", eid, "interest_rate", f"interest_rate={rate} is outside canonical decimal range 0..10 (24% must be 0.24, not 24).")
        else:
            r.ok("WCDS-R021", "ERROR")

    # R022 — currency must be valid ISO-4217
    currency = row.get("currency")
    if currency is not None:
        if currency not in ISO_4217_COMMON:
            r.fail("WCDS-R022", "ERROR", eid, "currency", f"currency={currency!r} is not a recognised ISO-4217 code.")
        else:
            r.ok("WCDS-R022", "ERROR")

    # R028/R029 — IFRS9/PD-LGD out of flat-tape scope
    r.skip("WCDS-R028", "ERROR", eid, None, "CreditRisk/IFRS9 stage not in scope of flat loan-tape mapping.")
    r.skip("WCDS-R029", "ERROR", eid, None, "PD/LGD not in scope of flat loan-tape mapping.")


def _validate_portfolio(rows: list[dict], r: _Recorder) -> None:
    seen_source_facility: dict[tuple[str, str], list[str]] = {}
    seen_crms: dict[str, list[str]] = {}
    seen_snapshot_key: dict[tuple, list[str]] = {}

    for row in rows:
        eid = _entity_id(row)
        key = (str(row.get("institution_id")), str(row.get("source_facility_id")))
        if row.get("source_facility_id"):
            seen_source_facility.setdefault(key, []).append(eid)
        crms = row.get("crms_credit_id")
        if crms:
            seen_crms.setdefault(crms, []).append(eid)
        snap_key = (row.get("facility_id") or row.get("source_facility_id"), row.get("snapshot_date"), "MONTHLY")
        seen_snapshot_key.setdefault(snap_key, []).append(eid)

    for key, ids in seen_source_facility.items():
        if len(ids) > 1:
            r.fail("WCDS-R026", "WARNING", ids[0], "source_facility_id", f"Duplicate source_facility_id {key[1]!r} within institution {key[0]!r} across facilities {ids}.")
        else:
            r.ok("WCDS-R026", "WARNING")

    for crms_id, ids in seen_crms.items():
        if len(ids) > 1:
            r.fail("WCDS-R001", "FATAL", ids[0], "crms_credit_id", f"Duplicate crms_credit_id {crms_id!r} shared by facilities {ids} (must be unique when present).")

    for snap_key, ids in seen_snapshot_key.items():
        if len(ids) > 1:
            r.fail("WCDS-R027", "ERROR", ids[0], "snapshot_date", f"Duplicate snapshot (facility, date, type) shared by facilities {ids}.")
        else:
            r.ok("WCDS-R027", "ERROR")


def validate_dataset(rows: list[dict], known_institution_ids: set[str] | None = None) -> ValidationReport:
    report = ValidationReport()
    r = _Recorder(report)
    for row in rows:
        _validate_row(row, r, known_institution_ids)
    _validate_portfolio(rows, r)
    return report
