from app.ingest.mapping import propose_mapping


def test_auto_maps_wcds_native_headers():
    columns = ["facility_id", "institution_id", "outstanding_principal", "days_past_due"]
    result = propose_mapping(columns)
    assert result.column_to_field["outstanding_principal"] == "outstanding_principal"
    assert result.column_to_field["days_past_due"] == "days_past_due"
    assert not result.unmapped_columns


def test_auto_maps_common_lms_aliases():
    columns = ["Loan Account Number", "Principal Outstanding", "DPD", "Loan Status", "Currency"]
    result = propose_mapping(columns)
    assert result.column_to_field["Principal Outstanding"] == "outstanding_principal"
    assert result.column_to_field["DPD"] == "days_past_due"
    assert result.column_to_field["Loan Status"] == "facility_status"


def test_overrides_take_precedence_and_can_ignore_columns():
    columns = ["weird_col", "outstanding_principal"]
    result = propose_mapping(columns, overrides={"weird_col": "amount_disbursed", "outstanding_principal": ""})
    assert result.column_to_field["weird_col"] == "amount_disbursed"
    assert "outstanding_principal" in result.unmapped_columns


def test_reports_missing_required_fields():
    result = propose_mapping(["some_unrelated_column"])
    assert "amount_disbursed" in result.missing_required_fields
    assert "origination_date" in result.missing_required_fields
