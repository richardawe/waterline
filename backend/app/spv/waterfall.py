"""Cashflow projection + sequential-pay waterfall simulation.

Two stages:
1. Per-facility level-payment amortization schedules (from each facility's own
   outstanding_principal/interest_rate/remaining term) are summed into an
   aggregate monthly "baseline" schedule — this is the pool amortizing exactly
   as contracted, no attrition.
2. A pool-level default/prepayment overlay (CDR/CPR-style monthly rates) scales
   that baseline via a survival factor, splitting run-off into defaults (with a
   lagged recovery) and prepayments (extra principal). This is the standard
   simplification used in most securitization cashflow models — a single
   attrition curve applied to aggregate scheduled cashflows, not a loan-level
   Monte Carlo. Good enough to size tranches and stress a structure; not a
   substitute for a full loan-level cashflow engine before a real closing.

Modeled priority of payments (strict sequential, not the only real-world
convention — some deals pay mezz interest pari passu with senior interest while
senior principal is still amortizing): servicing fee -> senior interest ->
senior principal (until fully repaid) -> mezz interest -> mezz principal (until
fully repaid) -> equity residual.
"""

import math
from dataclasses import dataclass, field
from datetime import date

from dateutil.relativedelta import relativedelta

from app.spv.eligibility import FacilityCandidate
from app.spv.sizing import PoolSizingResult


@dataclass
class WaterfallAssumptions:
    horizon_months: int = 36
    annual_default_rate: float = 0.05  # CDR-style: annualized % of performing balance defaulting
    annual_prepayment_rate: float = 0.10  # CPR-style: annualized % of performing balance prepaying
    recovery_rate: float = 0.40  # % of a defaulted balance eventually recovered
    recovery_lag_months: int = 6
    servicing_fee_annual_rate: float = 0.02  # % of beginning performing balance, annualized


@dataclass
class PeriodResult:
    period_index: int
    period_date: date
    collections_principal: float
    collections_interest: float
    defaults: float
    recoveries: float
    servicing_fee: float
    senior_interest_paid: float
    senior_principal_paid: float
    senior_balance_end: float
    mezz_interest_paid: float
    mezz_principal_paid: float
    mezz_balance_end: float
    equity_distribution: float
    pool_balance_end: float


@dataclass
class WaterfallRunResult:
    periods: list[PeriodResult] = field(default_factory=list)
    senior_fully_repaid_month: int | None = None
    mezz_fully_repaid_month: int | None = None
    equity_irr_annual: float | None = None
    senior_interest_coverage_min: float | None = None


def _facility_amortization(c: FacilityCandidate, as_of_date: date, horizon: int) -> tuple[list[float], list[float]]:
    """Level-payment amortization for one facility from as_of_date. Returns
    (principal_due[1..horizon], interest_due[1..horizon]) — zero-padded past the
    facility's own remaining term or the horizon, whichever is shorter."""
    principal = [0.0] * (horizon + 1)
    interest = [0.0] * (horizon + 1)

    balance = c.outstanding_principal
    if balance <= 0:
        return principal, interest

    remaining_months = 1
    if c.maturity_date is not None:
        delta = relativedelta(c.maturity_date, as_of_date)
        remaining_months = max(delta.years * 12 + delta.months, 1)
    remaining_months = min(remaining_months, 360)  # sanity cap

    monthly_rate = (c.interest_rate or 0.0) / 12
    if monthly_rate == 0:
        payment = balance / remaining_months
    else:
        payment = balance * monthly_rate / (1 - (1 + monthly_rate) ** -remaining_months)

    for m in range(1, min(remaining_months, horizon) + 1):
        interest_due = balance * monthly_rate
        principal_due = min(payment - interest_due, balance)
        balance -= principal_due
        principal[m] = principal_due
        interest[m] = interest_due
        if balance <= 0.01:
            break

    return principal, interest


def _build_baseline(
    eligible: list[FacilityCandidate], as_of_date: date, horizon: int
) -> tuple[list[float], list[float], list[float]]:
    """Aggregate baseline (no attrition) schedule. Returns
    (principal_due[0..H], interest_due[0..H], balance_end[0..H])."""
    base_principal = [0.0] * (horizon + 1)
    base_interest = [0.0] * (horizon + 1)
    for c in eligible:
        p, i = _facility_amortization(c, as_of_date, horizon)
        for m in range(horizon + 1):
            base_principal[m] += p[m]
            base_interest[m] += i[m]

    balance_end = [0.0] * (horizon + 1)
    balance_end[0] = sum(c.outstanding_principal for c in eligible)
    for m in range(1, horizon + 1):
        balance_end[m] = max(balance_end[m - 1] - base_principal[m], 0.0)

    return base_principal, base_interest, balance_end


def _monthly_rate_from_annual(annual_rate: float) -> float:
    annual_rate = min(max(annual_rate, 0.0), 0.999)
    return 1 - (1 - annual_rate) ** (1 / 12)


def _irr_monthly(cashflows: list[float]) -> float | None:
    """Newton's method IRR solver on a monthly cashflow series (cashflows[0] is
    the initial outflow, negative). Returns the monthly rate, or None if it
    doesn't converge (e.g. no sign change — equity never recovers principal)."""
    if not cashflows or cashflows[0] >= 0 or all(cf <= 0 for cf in cashflows[1:]):
        return None

    rate = 0.02
    for _ in range(200):
        npv = sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))
        d_npv = sum(-t * cf / (1 + rate) ** (t + 1) for t, cf in enumerate(cashflows))
        if abs(d_npv) < 1e-9:
            break
        new_rate = rate - npv / d_npv
        if new_rate <= -0.99:
            new_rate = -0.5
        if abs(new_rate - rate) < 1e-9:
            rate = new_rate
            break
        rate = new_rate
    if not math.isfinite(rate):
        return None
    return rate


def run_waterfall(
    eligible: list[FacilityCandidate],
    sizing: PoolSizingResult,
    as_of_date: date,
    assumptions: WaterfallAssumptions,
) -> WaterfallRunResult:
    horizon = assumptions.horizon_months
    base_principal, base_interest, base_balance = _build_baseline(eligible, as_of_date, horizon)

    default_m = _monthly_rate_from_annual(assumptions.annual_default_rate)
    prepay_m = _monthly_rate_from_annual(assumptions.annual_prepayment_rate)
    servicing_m = assumptions.servicing_fee_annual_rate / 12

    survival = [1.0] * (horizon + 1)
    for m in range(1, horizon + 1):
        survival[m] = survival[m - 1] * max(1 - default_m - prepay_m, 0.0)

    default_amounts = [0.0] * (horizon + 1)

    senior_balance = next((t.initial_balance for t in sizing.tranches if t.name == "Senior"), 0.0)
    mezz_balance = next((t.initial_balance for t in sizing.tranches if t.name == "Mezzanine"), 0.0)
    senior_coupon = next((t.coupon_rate for t in sizing.tranches if t.name == "Senior"), 0.0) or 0.0
    mezz_coupon = next((t.coupon_rate for t in sizing.tranches if t.name == "Mezzanine"), 0.0) or 0.0

    result = WaterfallRunResult()
    equity_cashflows = [-next((t.initial_balance for t in sizing.tranches if t.name == "Equity"), 0.0)]
    interest_coverage_ratios: list[float] = []

    for m in range(1, horizon + 1):
        beginning_performing = survival[m - 1] * base_balance[m - 1]
        collections_principal = survival[m - 1] * base_principal[m]
        collections_interest = survival[m - 1] * base_interest[m]
        this_month_default = beginning_performing * default_m
        this_month_prepay = beginning_performing * prepay_m
        default_amounts[m] = this_month_default
        collections_principal += this_month_prepay

        recoveries = 0.0
        lag_month = m - assumptions.recovery_lag_months
        if lag_month >= 1:
            recoveries = default_amounts[lag_month] * assumptions.recovery_rate

        servicing_fee = beginning_performing * servicing_m

        cash = collections_principal + collections_interest + recoveries - servicing_fee
        cash = max(cash, 0.0)
        cash_before_senior_interest = cash

        senior_interest_due = senior_balance * senior_coupon / 12
        senior_interest_paid = min(cash, senior_interest_due)
        cash -= senior_interest_paid
        if senior_interest_due > 0:
            interest_coverage_ratios.append(cash_before_senior_interest / senior_interest_due)

        senior_principal_paid = min(cash, senior_balance)
        cash -= senior_principal_paid
        senior_balance -= senior_principal_paid
        if senior_balance <= 0.01 and result.senior_fully_repaid_month is None:
            result.senior_fully_repaid_month = m

        mezz_interest_due = mezz_balance * mezz_coupon / 12
        mezz_interest_paid = min(cash, mezz_interest_due)
        cash -= mezz_interest_paid

        mezz_principal_paid = min(cash, mezz_balance)
        cash -= mezz_principal_paid
        mezz_balance -= mezz_principal_paid
        if mezz_balance <= 0.01 and mezz_interest_due > 0 and result.mezz_fully_repaid_month is None:
            result.mezz_fully_repaid_month = m

        equity_distribution = cash
        pool_balance_end = survival[m] * base_balance[m]

        result.periods.append(
            PeriodResult(
                period_index=m,
                period_date=as_of_date + relativedelta(months=m),
                collections_principal=round(collections_principal, 2),
                collections_interest=round(collections_interest, 2),
                defaults=round(this_month_default, 2),
                recoveries=round(recoveries, 2),
                servicing_fee=round(servicing_fee, 2),
                senior_interest_paid=round(senior_interest_paid, 2),
                senior_principal_paid=round(senior_principal_paid, 2),
                senior_balance_end=round(senior_balance, 2),
                mezz_interest_paid=round(mezz_interest_paid, 2),
                mezz_principal_paid=round(mezz_principal_paid, 2),
                mezz_balance_end=round(mezz_balance, 2),
                equity_distribution=round(equity_distribution, 2),
                pool_balance_end=round(pool_balance_end, 2),
            )
        )
        equity_cashflows.append(equity_distribution)

    monthly_irr = _irr_monthly(equity_cashflows)
    if monthly_irr is not None:
        result.equity_irr_annual = round((1 + monthly_irr) ** 12 - 1, 6)
    result.senior_interest_coverage_min = round(min(interest_coverage_ratios), 4) if interest_coverage_ratios else None

    return result
