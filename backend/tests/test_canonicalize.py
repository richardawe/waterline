from datetime import date

from app.ingest.canonicalize import canonicalize_row


def test_canonicalizes_dates_money_and_booleans():
    raw_row = {
        "amt": "1,250,000.50",
        "dt": "2026-01-15",
        "sec": "True",
        "stat": "active",
        "dpd": "12",
    }
    mapping = {
        "amt": "amount_disbursed",
        "dt": "origination_date",
        "sec": "secured_flag",
        "stat": "facility_status",
        "dpd": "days_past_due",
    }
    canonical, events, errors = canonicalize_row(raw_row, mapping)
    assert not errors
    assert canonical["amount_disbursed"] == 1250000.50
    assert canonical["origination_date"] == date(2026, 1, 15)
    assert canonical["secured_flag"] is True
    assert canonical["facility_status"] == "ACTIVE"
    assert canonical["days_past_due"] == 12
    assert len(events) == 5


def test_does_not_silently_fix_out_of_range_rate():
    """26 instead of 0.26 must survive canonicalization unmodified — it's a
    validation failure (WCDS-R021), not something the pipeline auto-corrects."""
    raw_row = {"rate": "26.0"}
    canonical, _, errors = canonicalize_row(raw_row, {"rate": "interest_rate"})
    assert not errors
    assert canonical["interest_rate"] == 26.0


def test_blank_values_become_none_without_error():
    raw_row = {"dd": "", "amt": None}
    canonical, _, errors = canonicalize_row(raw_row, {"dd": "default_date", "amt": "amount_disbursed"})
    assert canonical["default_date"] is None
    assert canonical["amount_disbursed"] is None
    assert not errors


def test_unparseable_value_is_reported_as_field_error():
    raw_row = {"dt": "not-a-date"}
    canonical, _, errors = canonicalize_row(raw_row, {"dt": "origination_date"})
    assert canonical["origination_date"] is None
    assert "origination_date" in errors
