"""Client segments (verticals) for Waterline Collections.

A segment is everything that differs between, say, a school and a lender:
the client types it covers, the upload columns, the words used on screen
and in messages, the conduct policy, the verification checklist, the default
fee and the message templates. The engine (queue, confirmations, disputes,
invoices, audit) is the same for every segment.

Definitions live here in code because they carry structure (column specs,
templates that must pass the conduct check). What ops control at runtime is
stored in `cl_segment`: whether a segment is hidden, open for a waitlist, or
live; and stricter-only overrides of its conduct policy and its default fee.
Adding a new segment = add a definition below, then switch it on in ops.

Guarantors, references, relatives and employers are blocked for every
segment: no definition may declare a column for them (enforced at import
time by `_check_definitions`).
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

STATUS_HIDDEN, STATUS_WAITLIST, STATUS_LIVE = "hidden", "waitlist", "live"
STATUSES = (STATUS_HIDDEN, STATUS_WAITLIST, STATUS_LIVE)

# Words that may never appear in a column name: no third-party data, ever.
THIRD_PARTY_WORDS = ("guarantor", "referee", "reference_name", "next_of_kin", "kin", "relative", "spouse",
                     "employer", "emergency", "contact_person", "witness", "friend")


@dataclass(frozen=True)
class Column:
    name: str
    kind: str  # text / phone / email / amount / date / int / first_name
    required: bool = False
    label: str = ""
    help: str = ""
    maps_to: str = "extra"  # debtor_name / phone / email / description / amount / due_date / dependant / external_reference / extra
    max_len: int = 300


# The baseline conduct policy is the phase-1 spec. A segment may be stricter;
# ops overrides may only tighten it further (see `validate_policy_override`).
BASE_POLICY = {
    "contact_start": "08:00",
    "contact_end": "19:00",
    "contact_weekdays": [0, 1, 2, 3, 4, 5],  # Monday=0 … Saturday=5; never Sunday
    "max_per_7_days": 2,
    "sequence_days": [0, 3, 7],  # intro, reminder, plan offer
    "repeat_every_days": 7,
    "max_unanswered": 6,
    "plan_reminder_days_before": 2,
    "plan_broken_after_days": 14,
}


@dataclass(frozen=True)
class Segment:
    key: str
    name: str  # shown to clients and on the landing page
    tagline: str
    client_types: tuple[str, ...]
    default_status: str
    terms: dict  # wording used across the portal, debtor page and emails
    columns: tuple[Column, ...]
    example_rows: tuple[tuple[str, ...], ...]
    templates: dict  # template key -> body
    default_fee_schedule: dict
    checklist: tuple[tuple[str, str], ...]  # (key, label)
    client_fields: tuple[Column, ...] = ()  # extra sign-up fields (e.g. licence number)
    extra_forbidden: tuple[str, ...] = ()  # regexes on top of the global conduct check
    policy: dict = field(default_factory=lambda: dict(BASE_POLICY))
    requires_policy_review: bool = False  # must be confirmed by an Admin (after counsel) before going live

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def reference_column(self) -> Column | None:
        return next((c for c in self.columns if c.maps_to == "external_reference"), None)


BASE_CHECKLIST = (
    ("phone_call_made", "Phone call made"),
    ("business_exists", "Business confirmed to exist"),
    ("bank_name_matches", "Bank account name matches business"),
    ("contact_confirmed", "Contact person confirmed"),
)


def _core_columns(description_help: str, reference: Column | None = None) -> tuple[Column, ...]:
    cols = [
        Column("debtor_name", "text", True, "Name", "Full name of the person who owes", "debtor_name", 160),
        Column("phone", "phone", True, "Phone", "Nigerian mobile number", "phone"),
        Column("email", "email", False, "Email", "Optional", "email", 254),
        Column("description", "text", True, "What it's for", description_help, "description", 300),
    ]
    if reference:
        cols.append(reference)
    return tuple(cols)


# ---------------------------------------------------------------- generic templates (fee/charge style)

_GENERIC_TEMPLATES = {
    "INTRO": (
        "Hello {{debtor_first_name}}, this is a message from {{client_name}} via Waterline. Our records show an "
        "outstanding balance of {{balance}} for {{description}}. You can view the details, pay, or choose a payment "
        "plan here: {{link}}. If you've already paid or think this is wrong, let us know on the same link. "
        "Reply STOP to stop messages."
    ),
    "REMINDER": (
        "Hello {{debtor_first_name}}, a quick reminder from {{client_name}} via Waterline about the balance of "
        "{{balance}} for {{description}}. {{discount_line}} Details and payment options: {{link}}. "
        "Reply STOP to stop messages."
    ),
    "PLAN_OFFER": (
        "Hello {{debtor_first_name}}, this is {{client_name}} via Waterline. We understand things can be tight. "
        "You can clear the {{balance}} balance in smaller instalments. Choose a plan that works for you: {{link}}. "
        "Reply STOP to stop messages."
    ),
    "INSTALMENT_DUE": (
        "Hello {{debtor_first_name}}, your next instalment of {{instalment_amount}} to {{client_name}} via Waterline "
        "is due on {{due_date}}. Pay and upload your receipt here: {{link}}. Reply STOP to stop messages."
    ),
    "DISPUTE_RESOLVED": (
        "Hello {{debtor_first_name}}, {{client_name}} has reviewed your query about {{description}} via Waterline. "
        "See the outcome and your updated balance here: {{link}}. Reply STOP to stop messages."
    ),
}

_LENDER_TEMPLATES = {
    "INTRO": (
        "Hello {{debtor_first_name}}, this is a message from {{client_name}} via Waterline about your {{description}}. "
        "Our records show {{balance}} outstanding. You can view the details, repay, or choose a repayment plan here: "
        "{{link}}. If you've already paid or think this is wrong, let us know on the same link. Reply STOP to stop messages."
    ),
    "REMINDER": (
        "Hello {{debtor_first_name}}, a reminder from {{client_name}} via Waterline that {{balance}} is outstanding on "
        "your {{description}}. {{discount_line}} Details and repayment options: {{link}}. Reply STOP to stop messages."
    ),
    "PLAN_OFFER": (
        "Hello {{debtor_first_name}}, this is {{client_name}} via Waterline. If paying {{balance}} at once is hard, you "
        "can repay in smaller instalments. Choose a plan that works for you: {{link}}. Reply STOP to stop messages."
    ),
    "INSTALMENT_DUE": _GENERIC_TEMPLATES["INSTALMENT_DUE"],
    "DISPUTE_RESOLVED": _GENERIC_TEMPLATES["DISPUTE_RESOLVED"],
}

TEMPLATE_KEYS = tuple(_GENERIC_TEMPLATES)


SEGMENTS: dict[str, Segment] = {}


def _register(seg: Segment) -> None:
    SEGMENTS[seg.key] = seg


_register(Segment(
    key="school",
    name="Schools",
    tagline="Unpaid school fees, recovered politely.",
    client_types=("Nursery/primary school", "Secondary school", "School group", "Tutorial centre", "Other school"),
    default_status=STATUS_LIVE,
    terms={"debtor": "parent", "debtors": "parents", "debtor_title": "Parent or guardian", "balance": "fees",
           "item_example": "School fees – 2nd term 2025/26", "reference": "Admission number"},
    columns=_core_columns(
        'e.g. "School fees – 2nd term 2025/26"',
        Column("admission_number", "text", False, "Admission number", "Optional — your own reference", "external_reference", 60),
    ) + (
        Column("dependant_first_name", "first_name", False, "Child's first name",
               "Optional. First name only — never a surname", "dependant", 40),
        Column("amount_owed", "amount", True, "Amount owed (₦)", "", "amount"),
        Column("due_date", "date", False, "Due date", "DD/MM/YYYY", "due_date"),
    ),
    example_rows=(
        ("Adaeze Okafor", "08031234567", "adaeze@example.com", "School fees – 2nd term 2025/26", "GFS/2019/044", "Tolu", "150000", "15/01/2026"),
        ("Musa Bello", "+2348051234567", "", "School fees – 2nd term 2025/26", "", "Amina", "95000.50", ""),
    ),
    templates=_GENERIC_TEMPLATES,
    default_fee_schedule={"type": "flat", "percent": 20},
    checklist=BASE_CHECKLIST,
    extra_forbidden=(r"\bwithhold", r"\bsend (him|her|them|your child|your ward) home\b", r"\bnot be allowed\b"),
))

_register(Segment(
    key="estate",
    name="Estates",
    tagline="Service charges and levies, collected without the awkward knocks.",
    client_types=("Estate management", "Residents' association", "Facility manager", "Landlord"),
    default_status=STATUS_LIVE,
    terms={"debtor": "resident", "debtors": "residents", "debtor_title": "Resident", "balance": "service charge",
           "item_example": "Service charge – Q1 2026", "reference": "Unit / house number"},
    columns=_core_columns(
        'e.g. "Service charge – Q1 2026"',
        Column("unit", "text", False, "Unit / house number", "Optional, e.g. Block C, Flat 4", "external_reference", 60),
    ) + (
        Column("amount_owed", "amount", True, "Amount owed (₦)", "", "amount"),
        Column("due_date", "date", False, "Due date", "DD/MM/YYYY", "due_date"),
    ),
    example_rows=(
        ("Chinedu Obi", "08031234567", "chinedu@example.com", "Service charge – Q1 2026", "Block C, Flat 4", "120000", "31/01/2026"),
        ("Funke Adeyemi", "+2348051234567", "", "Security levy – January 2026", "House 12", "15000", ""),
    ),
    templates=_GENERIC_TEMPLATES,
    default_fee_schedule={"type": "flat", "percent": 20},
    checklist=BASE_CHECKLIST,
    extra_forbidden=(r"\bevict", r"\block(ed)? out\b", r"\bdisconnect", r"\bcut (off|your)\b", r"\bgate pass\b"),
))

_register(Segment(
    key="lender",
    name="Lenders",
    tagline="Microfinance banks, digital lenders, finance companies and cooperatives.",
    client_types=("Microfinance bank", "Digital lender", "Finance company", "Cooperative society", "Commercial bank"),
    default_status=STATUS_WAITLIST,
    terms={"debtor": "borrower", "debtors": "borrowers", "debtor_title": "Borrower", "balance": "loan balance",
           "item_example": "Salary advance loan", "reference": "Loan reference"},
    columns=_core_columns(
        'The loan product, e.g. "Salary advance loan"',
        Column("loan_reference", "text", True, "Loan reference", "Your loan or account ID", "external_reference", 60),
    ) + (
        Column("amount_owed", "amount", True, "Amount outstanding (₦)", "Total currently due", "amount"),
        Column("disbursed_on", "date", False, "Disbursement date", "DD/MM/YYYY", "extra"),
        Column("due_date", "date", False, "Due / maturity date", "DD/MM/YYYY", "due_date"),
        Column("days_past_due", "int", False, "Days past due", "As at the upload date", "extra"),
    ),
    example_rows=(
        ("Tunde Bakare", "08031234567", "tunde@example.com", "Salary advance loan", "LN-2025-00981", "85000", "01/09/2025", "01/10/2025", "34"),
        ("Grace Eze", "+2348051234567", "", "SME working-capital loan", "LN-2025-01022", "420000.75", "", "", "120"),
    ),
    templates=_LENDER_TEMPLATES,
    default_fee_schedule={"type": "tiered_by_age", "tiers": [
        {"max_days": 90, "percent": 15}, {"max_days": 180, "percent": 20}, {"max_days": None, "percent": 25}]},
    checklist=BASE_CHECKLIST + (
        ("licence_verified", "Licence/registration verified with the regulator (CBN or FCCPC)"),
        ("lending_terms_reviewed", "Loan terms and borrower consent reviewed"),
    ),
    client_fields=(
        Column("licence_type", "text", True, "Licence type", "e.g. CBN MFB licence, FCCPC digital lending registration", "extra", 120),
        Column("licence_number", "text", True, "Licence / registration number", "", "extra", 60),
    ),
    extra_forbidden=(r"\bdefaulter", r"\bfraud", r"\bthief", r"\bcriminal", r"\byour contacts\b", r"\bphonebook\b"),
    requires_policy_review=True,
))

_register(Segment(
    key="service",
    name="Gyms, clubs & subscriptions",
    tagline="Membership, subscription and service balances.",
    client_types=("Gym or club", "Internet provider", "Subscription service", "Other service business"),
    default_status=STATUS_WAITLIST,
    terms={"debtor": "customer", "debtors": "customers", "debtor_title": "Customer", "balance": "balance",
           "item_example": "Membership – January 2026", "reference": "Customer ID"},
    columns=_core_columns(
        'e.g. "Membership – January 2026"',
        Column("customer_id", "text", False, "Customer ID", "Optional — your own reference", "external_reference", 60),
    ) + (
        Column("amount_owed", "amount", True, "Amount owed (₦)", "", "amount"),
        Column("due_date", "date", False, "Due date", "DD/MM/YYYY", "due_date"),
    ),
    example_rows=(
        ("Kemi Johnson", "08031234567", "", "Membership – January 2026", "M-1042", "25000", "31/01/2026"),
    ),
    templates=_GENERIC_TEMPLATES,
    default_fee_schedule={"type": "flat", "percent": 20},
    checklist=BASE_CHECKLIST,
))


def _check_definitions() -> None:
    from app.collections import templates as tpl_mod

    for seg in SEGMENTS.values():
        names = seg.column_names
        assert len(names) == len(set(names)), f"{seg.key}: duplicate columns"
        for name in names + [c.name for c in seg.client_fields]:
            assert not any(w in name for w in THIRD_PARTY_WORDS), f"{seg.key}: third-party column {name}"
        for required in ("debtor_name", "phone", "description", "amount"):
            assert any(c.maps_to == required and c.required for c in seg.columns), f"{seg.key}: {required} must be required"
        assert set(seg.templates) == set(TEMPLATE_KEYS), f"{seg.key}: template set incomplete"
        for body in seg.templates.values():
            tpl_mod.check_template(body, seg)
        validate_fee_schedule(seg.default_fee_schedule)


# ---------------------------------------------------------------- runtime state (cl_segment)


def get(key: str) -> Segment:
    try:
        return SEGMENTS[key]
    except KeyError as exc:
        raise KeyError(f"unknown segment {key}") from exc


def setting(db: Session, key: str):
    from app.collections.models import SegmentSetting

    return db.get(SegmentSetting, key)


def status(db: Session, key: str) -> str:
    row = setting(db, key)
    return row.status if row else get(key).default_status


def policy(db: Session, key: str) -> dict:
    import json

    seg = get(key)
    out = dict(seg.policy)
    row = setting(db, key)
    if row and row.policy_overrides:
        out.update(json.loads(row.policy_overrides))
    return out


def default_fee_schedule(db: Session, key: str) -> dict:
    import json

    row = setting(db, key)
    if row and row.default_fee_schedule:
        return json.loads(row.default_fee_schedule)
    return get(key).default_fee_schedule


def segment_for_type(client_type: str) -> Segment | None:
    return next((s for s in SEGMENTS.values() if client_type in s.client_types), None)


# ---------------------------------------------------------------- validation


class PolicyInvalid(ValueError):
    pass


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def validate_policy_override(seg: Segment, override: dict) -> dict:
    """Ops may only make a segment's conduct policy stricter than its
    definition — never looser. Returns the cleaned override."""
    base = seg.policy
    clean = {}
    for key, value in (override or {}).items():
        if key not in BASE_POLICY:
            raise PolicyInvalid(f"Unknown policy setting {key}.")
        try:
            if key in ("contact_start", "contact_end"):
                minutes = _minutes(str(value))
                if not 0 <= minutes <= 24 * 60:
                    raise ValueError
                if key == "contact_start" and minutes < _minutes(base[key]):
                    raise PolicyInvalid(f"Contact can't start before {base[key]}.")
                if key == "contact_end" and minutes > _minutes(base[key]):
                    raise PolicyInvalid(f"Contact can't end after {base[key]}.")
                clean[key] = f"{minutes // 60:02d}:{minutes % 60:02d}"
            elif key == "contact_weekdays":
                days = sorted({int(d) for d in value})
                if not days or not set(days) <= set(base[key]):
                    raise PolicyInvalid(f"Contact days must be a non-empty subset of {base[key]} (Monday=0).")
                clean[key] = days
            elif key == "sequence_days":
                days = [int(d) for d in value]
                if len(days) != len(base[key]) or days != sorted(days) or days[0] != 0:
                    raise PolicyInvalid("Sequence must start at day 0 and have the same number of steps.")
                if any(d < b for d, b in zip(days, base[key])):
                    raise PolicyInvalid(f"Sequence steps can't come sooner than {base[key]}.")
                clean[key] = days
            elif key in ("repeat_every_days", "plan_reminder_days_before"):
                v = int(value)
                if key == "repeat_every_days" and v < base[key]:
                    raise PolicyInvalid(f"Repeat messages can't be more often than every {base[key]} days.")
                if key == "plan_reminder_days_before" and not 1 <= v <= base[key]:
                    raise PolicyInvalid(f"Plan reminders go 1–{base[key]} days before a due date.")
                clean[key] = v
            elif key in ("max_per_7_days", "max_unanswered"):
                v = int(value)
                if not 1 <= v <= base[key]:
                    raise PolicyInvalid(f"{key} must be between 1 and {base[key]}.")
                clean[key] = v
            elif key == "plan_broken_after_days":
                v = int(value)
                if not 1 <= v <= 60:
                    raise PolicyInvalid("plan_broken_after_days must be between 1 and 60.")
                clean[key] = v
        except (TypeError, ValueError) as exc:
            if isinstance(exc, PolicyInvalid):
                raise
            raise PolicyInvalid(f"Invalid value for {key}.") from exc
    merged = {**base, **clean}
    if _minutes(merged["contact_start"]) >= _minutes(merged["contact_end"]):
        raise PolicyInvalid("Contact must start before it ends.")
    return clean


class FeeInvalid(ValueError):
    pass


MAX_FEE_PERCENT = 50


def validate_fee_schedule(schedule: dict) -> dict:
    """{"type": "flat", "percent": 20} or
    {"type": "tiered_by_age", "tiers": [{"max_days": 90, "percent": 15}, …, {"max_days": null, "percent": 25}]}
    — tiers ascending by debt age, the last open-ended."""
    if not isinstance(schedule, dict):
        raise FeeInvalid("Fee must be a schedule.")

    def pct(v) -> float:
        try:
            p = round(float(v), 2)
        except (TypeError, ValueError) as exc:
            raise FeeInvalid("Fee percentage must be a number.") from exc
        if not 0 < p <= MAX_FEE_PERCENT:
            raise FeeInvalid(f"Fee percentage must be more than 0 and at most {MAX_FEE_PERCENT}%.")
        return p

    kind = schedule.get("type")
    if kind == "flat":
        return {"type": "flat", "percent": pct(schedule.get("percent"))}
    if kind == "tiered_by_age":
        tiers = schedule.get("tiers") or []
        if not 2 <= len(tiers) <= 6:
            raise FeeInvalid("Use 2 to 6 tiers.")
        clean, last = [], 0
        for i, t in enumerate(tiers):
            max_days = t.get("max_days")
            is_last = i == len(tiers) - 1
            if is_last:
                if max_days is not None:
                    raise FeeInvalid("The last tier must have no upper limit.")
            else:
                try:
                    max_days = int(max_days)
                except (TypeError, ValueError) as exc:
                    raise FeeInvalid("Each tier except the last needs a day limit.") from exc
                if max_days <= last:
                    raise FeeInvalid("Tier day limits must increase.")
                last = max_days
            clean.append({"max_days": max_days, "percent": pct(t.get("percent"))})
        return {"type": "tiered_by_age", "tiers": clean}
    raise FeeInvalid("Fee type must be flat or tiered_by_age.")


def fee_percent_for(schedule: dict, age_days: int) -> float:
    if schedule["type"] == "flat":
        return schedule["percent"]
    for tier in schedule["tiers"]:
        if tier["max_days"] is None or age_days <= tier["max_days"]:
            return tier["percent"]
    return schedule["tiers"][-1]["percent"]


def describe_fee(schedule: dict) -> str:
    if schedule["type"] == "flat":
        return f"{schedule['percent']:g}% of confirmed recoveries"
    parts, prev = [], None
    for t in schedule["tiers"]:
        low = 0 if prev is None else prev + 1
        span = f"{low}–{t['max_days']} days overdue" if t["max_days"] is not None else f"over {prev} days overdue"
        parts.append(f"{t['percent']:g}% ({span})")
        prev = t["max_days"]
    return ", ".join(parts)


def public_view(db: Session, seg: Segment) -> dict:
    fee = default_fee_schedule(db, seg.key)
    return {
        "key": seg.key, "name": seg.name, "tagline": seg.tagline, "status": status(db, seg.key),
        "client_types": list(seg.client_types), "terms": seg.terms,
        "client_fields": [{"name": c.name, "label": c.label, "help": c.help, "required": c.required} for c in seg.client_fields],
        "default_fee": fee, "default_fee_text": describe_fee(fee),
    }
