"""Pool sizing and tranche structuring.

Takes the eligible pool from eligibility.screen_pool() and computes portfolio
stats plus a senior/mezzanine/equity split from configurable advance rates.
Equity is always the residual — it absorbs whatever the senior+mezz advance
rates don't cover, which is the point of a first-loss tranche.
"""

from dataclasses import dataclass
from datetime import date

from app.spv.eligibility import FacilityCandidate


@dataclass
class SizingAssumptions:
    senior_advance_rate: float = 0.80  # % of eligible pool par
    mezz_advance_rate: float = 0.10  # % of eligible pool par; 0.0 for a two-tranche (senior/equity) structure
    senior_coupon_rate: float = 0.19
    mezz_coupon_rate: float = 0.24


@dataclass
class TrancheSizing:
    name: str
    seniority_rank: int
    initial_balance: float
    coupon_rate: float | None
    advance_rate: float | None


@dataclass
class PoolSizingResult:
    eligible_facility_count: int
    eligible_pool_par: float
    weighted_avg_rate: float | None
    weighted_avg_life_months: float | None
    weighted_avg_dpd: float | None
    tranches: list[TrancheSizing]
    overcollateralization_ratio: float | None  # pool_par / senior_balance


def _months_between(d1: date, d2: date) -> float:
    return max((d2 - d1).days, 0) / 30.4375


def size_pool(
    eligible: list[FacilityCandidate], as_of_date: date, assumptions: SizingAssumptions
) -> PoolSizingResult:
    pool_par = sum(c.outstanding_principal for c in eligible)
    n = len(eligible)

    if pool_par > 0:
        wa_rate = sum((c.interest_rate or 0) * c.outstanding_principal for c in eligible) / pool_par
        wa_life = sum(
            _months_between(as_of_date, c.maturity_date) * c.outstanding_principal
            for c in eligible
            if c.maturity_date is not None
        ) / pool_par
        wa_dpd = sum(c.days_past_due * c.outstanding_principal for c in eligible) / pool_par
    else:
        wa_rate = wa_life = wa_dpd = None

    senior_balance = round(pool_par * assumptions.senior_advance_rate, 2)
    mezz_balance = round(pool_par * assumptions.mezz_advance_rate, 2)
    equity_balance = round(pool_par - senior_balance - mezz_balance, 2)

    tranches = [
        TrancheSizing("Senior", 1, senior_balance, assumptions.senior_coupon_rate, assumptions.senior_advance_rate),
    ]
    if mezz_balance > 0:
        tranches.append(
            TrancheSizing("Mezzanine", 2, mezz_balance, assumptions.mezz_coupon_rate, assumptions.mezz_advance_rate)
        )
    tranches.append(
        TrancheSizing(
            "Equity",
            len(tranches) + 1,
            equity_balance,
            None,
            round(equity_balance / pool_par, 4) if pool_par else None,
        )
    )

    return PoolSizingResult(
        eligible_facility_count=n,
        eligible_pool_par=pool_par,
        weighted_avg_rate=wa_rate,
        weighted_avg_life_months=wa_life,
        weighted_avg_dpd=wa_dpd,
        tranches=tranches,
        overcollateralization_ratio=(pool_par / senior_balance) if senior_balance else None,
    )
