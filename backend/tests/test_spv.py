from datetime import date

from app.spv.eligibility import EligibilityCriteria, FacilityCandidate, screen_pool
from app.spv.sizing import SizingAssumptions, size_pool
from app.spv.waterfall import WaterfallAssumptions, run_waterfall

AS_OF = date(2026, 1, 1)


def _candidate(**overrides) -> FacilityCandidate:
    defaults = dict(
        facility_id="F1",
        party_id="P1",
        sector_code="RETAIL",
        currency="NGN",
        facility_status="ACTIVE",
        outstanding_principal=100_000.0,
        days_past_due=0,
        default_flag=False,
        writeoff_flag=False,
        secured_flag=False,
        maturity_date=date(2026, 12, 1),
        interest_rate=0.24,
    )
    defaults.update(overrides)
    return FacilityCandidate(**defaults)


def test_eligibility_excludes_defaulted_and_delinquent_facilities():
    candidates = [
        _candidate(facility_id="F1", party_id="P1"),
        _candidate(facility_id="F2", party_id="P2", default_flag=True),
        _candidate(facility_id="F3", party_id="P3", days_past_due=90),
        _candidate(facility_id="F4", party_id="P4", facility_status="CLOSED"),
    ]
    # concentration caps irrelevant here — isolate the hard-screen (status/dpd/default) behavior
    criteria = EligibilityCriteria(max_single_obligor_pct=1.0, max_sector_concentration_pct=1.0)
    outcomes = screen_pool(candidates, criteria, AS_OF)
    eligible = {o.facility_id for o in outcomes if o.eligible}
    assert eligible == {"F1"}


def test_eligibility_applies_single_obligor_concentration_cap():
    candidates = [
        _candidate(facility_id="BIG", party_id="WHALE", outstanding_principal=900_000.0),
        *[_candidate(facility_id=f"F{i}", party_id=f"P{i}", outstanding_principal=10_000.0) for i in range(10)],
    ]
    criteria = EligibilityCriteria(max_single_obligor_pct=0.10)
    outcomes = screen_pool(candidates, criteria, AS_OF)
    big_outcome = next(o for o in outcomes if o.facility_id == "BIG")
    assert not big_outcome.eligible
    assert "single-obligor" in big_outcome.reasons[0]


def test_sizing_produces_senior_mezz_equity_split_summing_to_pool_par():
    candidates = [_candidate(facility_id=f"F{i}") for i in range(5)]
    sizing = size_pool(candidates, AS_OF, SizingAssumptions(senior_advance_rate=0.8, mezz_advance_rate=0.1))
    total = sum(t.initial_balance for t in sizing.tranches)
    assert abs(total - sizing.eligible_pool_par) < 1.0
    assert sizing.tranches[0].name == "Senior"
    assert sizing.tranches[-1].name == "Equity"


def test_waterfall_pays_senior_before_mezz_and_equity_gets_residual():
    candidates = [_candidate(facility_id=f"F{i}", outstanding_principal=1_000_000.0) for i in range(20)]
    sizing = size_pool(candidates, AS_OF, SizingAssumptions())
    result = run_waterfall(candidates, sizing, AS_OF, WaterfallAssumptions(horizon_months=24))

    assert result.periods
    for p in result.periods:
        assert p.senior_balance_end >= 0
        assert p.mezz_balance_end >= 0
        if p.mezz_principal_paid > 0:
            # mezz principal should only flow once senior is fully repaid (strict sequential pay)
            assert p.senior_balance_end == 0

    if result.senior_fully_repaid_month and result.mezz_fully_repaid_month:
        assert result.mezz_fully_repaid_month >= result.senior_fully_repaid_month


def test_waterfall_equity_gets_nothing_until_debt_tranches_are_serviced():
    candidates = [_candidate(facility_id="F1", outstanding_principal=100_000.0)]
    sizing = size_pool(candidates, AS_OF, SizingAssumptions())
    result = run_waterfall(candidates, sizing, AS_OF, WaterfallAssumptions(horizon_months=12))
    first_period = result.periods[0]
    if first_period.senior_balance_end > 0:
        assert first_period.equity_distribution == 0
