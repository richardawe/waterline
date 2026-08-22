"""WCDS v0.1 canonical field registry for loan-tape ingestion.

This is the flattened "one row per facility" view of WCDS used for tape intake —
Facility + its latest FacilitySnapshot + the primary borrower Party, joined —
matching the shape of standards/wcds/samples/*.csv and what a lender's own loan
tape naturally looks like (one row per loan). Deeper entities (Collateral,
Restructure, Payment, Schedule, CreditRisk) are separate optional intake paths,
not covered by this flat mapper.

Each field carries `aliases`: normalized (lowercase, alnum-only) source-column
names the auto-mapper will match against, built from common LMS/core-banking
export conventions — not just the WCDS canonical name itself.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FieldSpec:
    name: str
    entity: str  # facility / facility_snapshot / party
    dtype: str  # string / decimal / integer / date / datetime / boolean / enum
    required: bool
    aliases: tuple[str, ...] = field(default_factory=tuple)
    enum_values: tuple[str, ...] = field(default_factory=tuple)


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


FACILITY_STATUS = ("PENDING", "ACTIVE", "SUSPENDED", "CLOSED", "CANCELLED", "DEFAULTED", "WRITTEN_OFF")
FACILITY_TYPE = (
    "TERM_LOAN",
    "REVOLVING_CREDIT",
    "OVERDRAFT",
    "CREDIT_CARD",
    "LEASE",
    "HIRE_PURCHASE",
    "MORTGAGE",
    "TRADE_FINANCE",
    "GUARANTEE_FACILITY",
    "OTHER",
)
REPAYMENT_FREQUENCY = (
    "DAILY",
    "WEEKLY",
    "FORTNIGHTLY",
    "MONTHLY",
    "QUARTERLY",
    "SEMI_ANNUAL",
    "ANNUAL",
    "BULLET",
    "IRREGULAR",
)
DELINQUENCY_BUCKET = ("CURRENT", "DPD_1_30", "DPD_31_60", "DPD_61_90", "DPD_90_PLUS")
RATE_TYPE = ("FIXED", "FLOATING", "PROFIT_RATE", "ZERO_RATE", "OTHER")

FIELDS: list[FieldSpec] = [
    FieldSpec("facility_id", "facility", "string", False, ("facilityid", "loanid", "loannumber", "loanno")),
    FieldSpec(
        "institution_id",
        "facility",
        "string",
        True,
        ("institutionid", "lenderid", "lendercode", "originatorid"),
    ),
    FieldSpec(
        "source_facility_id",
        "facility",
        "string",
        True,
        ("sourcefacilityid", "loanid", "loannumber", "loanaccountnumber", "accountno", "loanref", "loanreference"),
    ),
    FieldSpec("crms_credit_id", "facility", "string", False, ("crmscreditid", "crmsid", "crmsreference")),
    FieldSpec(
        "facility_type",
        "facility",
        "enum",
        True,
        ("facilitytype", "loantype", "producttype"),
        FACILITY_TYPE,
    ),
    FieldSpec("product_code", "facility", "string", True, ("productcode", "product", "productname")),
    FieldSpec("currency", "facility", "string", True, ("currency", "ccy", "currencycode")),
    FieldSpec(
        "approved_limit",
        "facility",
        "decimal",
        False,
        ("approvedlimit", "creditlimit", "approvedamount"),
    ),
    FieldSpec(
        "original_principal",
        "facility",
        "decimal",
        False,
        ("originalprincipal", "loanamount", "principalamount", "faceamount"),
    ),
    FieldSpec(
        "amount_disbursed",
        "facility",
        "decimal",
        True,
        ("amountdisbursed", "disbursedamount", "disbursementamount"),
    ),
    FieldSpec("approval_date", "facility", "date", False, ("approvaldate",)),
    FieldSpec(
        "origination_date",
        "facility",
        "date",
        True,
        ("originationdate", "loanstartdate", "startdate", "contractdate", "disbursementdate"),
    ),
    FieldSpec(
        "first_disbursement_date",
        "facility",
        "date",
        True,
        ("firstdisbursementdate", "disbursementdate", "drawdowndate", "valuedate"),
    ),
    FieldSpec(
        "maturity_date",
        "facility",
        "date",
        False,
        ("maturitydate", "loanenddate", "enddate", "expirydate"),
    ),
    FieldSpec("original_tenor_days", "facility", "integer", False, ("originaltenordays", "tenordays", "tenor")),
    FieldSpec(
        "interest_rate",
        "facility",
        "decimal",
        False,
        ("interestrate", "rate", "annualrate", "nominalrate", "interestratepct", "interestrateannum"),
    ),
    FieldSpec("rate_type", "facility", "enum", False, ("ratetype",), RATE_TYPE),
    FieldSpec(
        "repayment_frequency",
        "facility",
        "enum",
        False,
        ("repaymentfrequency", "paymentfrequency", "frequency"),
        REPAYMENT_FREQUENCY,
    ),
    FieldSpec(
        "scheduled_payment_amount",
        "facility",
        "decimal",
        False,
        ("scheduledpaymentamount", "instalmentamount", "installmentamount", "emi"),
    ),
    FieldSpec("purpose_code", "facility", "string", False, ("purposecode", "loanpurpose", "purpose")),
    FieldSpec("sector_code", "facility", "string", False, ("sectorcode", "sector", "industrysector")),
    FieldSpec("funding_source_code", "facility", "string", False, ("fundingsourcecode", "fundingsource")),
    FieldSpec(
        "secured_flag",
        "facility",
        "boolean",
        True,
        ("securedflag", "secured", "issecured", "collateralised", "collateralized"),
    ),
    FieldSpec(
        "facility_status",
        "facility",
        "enum",
        True,
        ("facilitystatus", "loanstatus", "status", "accountstatus"),
        FACILITY_STATUS,
    ),
    FieldSpec(
        "snapshot_date",
        "facility_snapshot",
        "date",
        True,
        ("snapshotdate", "asofdate", "reportingdate", "extractdate"),
    ),
    FieldSpec(
        "outstanding_principal",
        "facility_snapshot",
        "decimal",
        True,
        ("outstandingprincipal", "principaloutstanding", "outstandingbalance", "balance", "currentbalance"),
    ),
    FieldSpec(
        "total_exposure",
        "facility_snapshot",
        "decimal",
        True,
        ("totalexposure", "totaloutstanding", "exposure"),
    ),
    FieldSpec(
        "days_past_due",
        "facility_snapshot",
        "integer",
        True,
        ("dayspastdue", "dpd", "daysoverdue", "arrearsdays"),
    ),
    FieldSpec(
        "delinquency_bucket",
        "facility_snapshot",
        "enum",
        True,
        ("delinquencybucket", "dpdbucket", "arrearsbucket", "bucket"),
        DELINQUENCY_BUCKET,
    ),
    FieldSpec("default_flag", "facility_snapshot", "boolean", True, ("defaultflag", "isdefault", "indefault")),
    FieldSpec("default_date", "facility_snapshot", "date", False, ("defaultdate",)),
    FieldSpec(
        "restructure_flag", "facility_snapshot", "boolean", True, ("restructureflag", "isrestructured", "restructured")
    ),
    FieldSpec("writeoff_flag", "facility_snapshot", "boolean", True, ("writeoffflag", "iswrittenoff", "writtenoff")),
    FieldSpec("writeoff_amount", "facility_snapshot", "decimal", False, ("writeoffamount",)),
    FieldSpec("recovery_amount", "facility_snapshot", "decimal", False, ("recoveryamount", "recovered")),
    FieldSpec("party_id", "party", "string", False, ("partyid", "customerid", "borrowerid")),
    FieldSpec(
        "borrower_external_id",
        "party",
        "string",
        False,
        ("borrowerexternalid", "customerreference", "cif", "borrowerref"),
    ),
    FieldSpec("party_type", "party", "enum", True, ("partytype", "customertype", "borrowertype"), ("PERSON", "ORGANISATION")),
]

FIELDS_BY_NAME: dict[str, FieldSpec] = {f.name: f for f in FIELDS}

_ALIAS_INDEX: dict[str, str] = {}
for _f in FIELDS:
    _ALIAS_INDEX[_norm(_f.name)] = _f.name
    for _a in _f.aliases:
        _ALIAS_INDEX.setdefault(_norm(_a), _f.name)


def suggest_field(source_column: str) -> str | None:
    """Best-effort match of a raw source column name to a WCDS field name.
    Exact normalized match first, then substring containment as a fallback."""
    key = _norm(source_column)
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    for alias_key, field_name in _ALIAS_INDEX.items():
        if len(alias_key) >= 5 and (alias_key in key or key in alias_key):
            return field_name
    return None
