"""SPV pool eligibility screening.

Applies a standard securitization-style eligibility criteria set to a lender's
WCDS-canonicalized facility book (latest FacilitySnapshot per Facility) and
returns which facilities go into the candidate pool, with a documented reason
for every exclusion — pool eligibility decisions need to survive an investor's
"why was this loan left out" question just as much as the loan-tape data itself.

Concentration limits (single-obligor / sector) are applied as a second pass over
the hard-screened pool: facilities are trimmed largest-balance-first within any
group that breaches its cap. The cap denominator is fixed at the hard-screened
pool's par value (not recomputed as facilities are trimmed) — a deliberate
simplification; recomputing iteratively converges to a marginally tighter pool
but rarely changes which facilities end up excluded at realistic concentration
levels.
"""

import json
from dataclasses import dataclass, field
from datetime import date


@dataclass
class EligibilityCriteria:
    allowed_currencies: tuple[str, ...] = ("NGN",)
    allowed_statuses: tuple[str, ...] = ("ACTIVE",)
    max_days_past_due: int = 30
    exclude_default_flag: bool = True
    exclude_writeoff_flag: bool = True
    min_outstanding_principal: float = 0.0
    max_outstanding_principal: float | None = None
    min_remaining_tenor_days: int = 30
    require_secured: bool = False
    max_single_obligor_pct: float = 0.02  # 2% of pool par per borrower
    max_sector_concentration_pct: float = 0.35  # 35% of pool par per sector_code


@dataclass
class FacilityCandidate:
    facility_id: str
    party_id: str | None
    sector_code: str | None
    currency: str
    facility_status: str
    outstanding_principal: float
    days_past_due: int
    default_flag: bool
    writeoff_flag: bool
    secured_flag: bool
    maturity_date: date | None
    interest_rate: float | None


@dataclass
class EligibilityOutcome:
    facility_id: str
    eligible: bool
    reasons: list[str] = field(default_factory=list)

    def reasons_json(self) -> str:
        return json.dumps(self.reasons)


def _hard_screen(c: FacilityCandidate, criteria: EligibilityCriteria, as_of_date: date) -> list[str]:
    reasons: list[str] = []
    if c.currency not in criteria.allowed_currencies:
        reasons.append(f"currency {c.currency} not in allowed set {criteria.allowed_currencies}")
    if c.facility_status not in criteria.allowed_statuses:
        reasons.append(f"facility_status {c.facility_status} not in allowed set {criteria.allowed_statuses}")
    if c.days_past_due > criteria.max_days_past_due:
        reasons.append(f"days_past_due {c.days_past_due} exceeds max {criteria.max_days_past_due}")
    if criteria.exclude_default_flag and c.default_flag:
        reasons.append("default_flag is true")
    if criteria.exclude_writeoff_flag and c.writeoff_flag:
        reasons.append("writeoff_flag is true")
    if c.outstanding_principal < criteria.min_outstanding_principal:
        reasons.append(f"outstanding_principal {c.outstanding_principal} below minimum {criteria.min_outstanding_principal}")
    if criteria.max_outstanding_principal is not None and c.outstanding_principal > criteria.max_outstanding_principal:
        reasons.append(f"outstanding_principal {c.outstanding_principal} exceeds maximum {criteria.max_outstanding_principal}")
    if criteria.require_secured and not c.secured_flag:
        reasons.append("secured_flag is false but pool requires secured facilities")
    if c.maturity_date is not None:
        remaining_days = (c.maturity_date - as_of_date).days
        if remaining_days < criteria.min_remaining_tenor_days:
            reasons.append(f"remaining tenor {remaining_days}d below minimum {criteria.min_remaining_tenor_days}d")
    return reasons


def _apply_concentration_limits(
    passed: list[FacilityCandidate], criteria: EligibilityCriteria
) -> dict[str, list[str]]:
    exclusions: dict[str, list[str]] = {}
    pool_par = sum(c.outstanding_principal for c in passed)
    if pool_par <= 0:
        return exclusions

    def _trim(group_key_fn, cap_pct: float, label: str) -> None:
        groups: dict[str, list[FacilityCandidate]] = {}
        for c in passed:
            if c.facility_id in exclusions:
                continue
            key = group_key_fn(c)
            if key is None:
                continue
            groups.setdefault(key, []).append(c)
        cap = pool_par * cap_pct
        for key, members in groups.items():
            members.sort(key=lambda c: c.outstanding_principal, reverse=True)
            total = sum(m.outstanding_principal for m in members)
            i = 0
            while total > cap and i < len(members):
                m = members[i]
                exclusions.setdefault(m.facility_id, []).append(
                    f"{label} {key!r} concentration {total:,.2f} exceeds cap {cap:,.2f} ({cap_pct:.1%} of pool)"
                )
                total -= m.outstanding_principal
                i += 1

    _trim(lambda c: c.party_id, criteria.max_single_obligor_pct, "single-obligor")
    _trim(lambda c: c.sector_code, criteria.max_sector_concentration_pct, "sector")
    return exclusions


def screen_pool(
    candidates: list[FacilityCandidate], criteria: EligibilityCriteria, as_of_date: date
) -> list[EligibilityOutcome]:
    hard_results: dict[str, list[str]] = {}
    passed: list[FacilityCandidate] = []
    for c in candidates:
        reasons = _hard_screen(c, criteria, as_of_date)
        if reasons:
            hard_results[c.facility_id] = reasons
        else:
            passed.append(c)

    concentration_exclusions = _apply_concentration_limits(passed, criteria)

    outcomes: list[EligibilityOutcome] = []
    for c in candidates:
        if c.facility_id in hard_results:
            outcomes.append(EligibilityOutcome(c.facility_id, False, hard_results[c.facility_id]))
        elif c.facility_id in concentration_exclusions:
            outcomes.append(EligibilityOutcome(c.facility_id, False, concentration_exclusions[c.facility_id]))
        else:
            outcomes.append(EligibilityOutcome(c.facility_id, True, []))
    return outcomes
