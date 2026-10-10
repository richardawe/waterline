from datetime import date

import pytest

from app.collections import segments, templates, totp, upload
from app.collections.money import format_naira, naira_to_kobo, percent_of
from app.collections.outreach import links
from app.collections.phone import normalise_phone
from app.collections.rules import (
    RulesInvalid,
    active_discount,
    plan_options,
    validate_rules,
)
from app.collections.timeutil import in_contact_window, working_days_between
from tests.collections_factory import wat


@pytest.mark.parametrize("raw", ["08031234567", "8031234567", "2348031234567", "+234 803 123 4567", "0803-123-4567",
                                 "+2340803 1234567", 8031234567.0])
def test_phone_normalises_nigerian_formats(raw):
    assert normalise_phone(raw) == "+2348031234567"


@pytest.mark.parametrize("raw", ["", "12345", "+447700900123", "0803123456", "abc", "06031234567"])
def test_phone_rejects_non_nigerian_mobiles(raw):
    with pytest.raises(ValueError):
        normalise_phone(raw)


def test_money_parse_format_and_fee_rounding():
    assert naira_to_kobo("₦1,234,567.5") == 123456750
    assert naira_to_kobo(150000) == 15000000
    assert format_naira(123456700) == "₦1,234,567.00"
    assert format_naira(5) == "₦0.05"
    assert percent_of(33_33, 20) == 667  # 666.6 kobo → nearest kobo
    assert percent_of(25, 20) == 5
    with pytest.raises(ValueError):
        naira_to_kobo("12.345")
    with pytest.raises(ValueError):
        naira_to_kobo("ten")


def test_every_segment_definition_is_consistent():
    # complete template sets that pass the conduct check, no third-party columns,
    # required core fields, valid default fees
    segments._check_definitions()


@pytest.mark.parametrize("bad", [
    "Pay now or we call the police. {{client_name}} via Waterline {{link}} Reply STOP to stop messages.",
    "Your child may miss exams. {{client_name}} via Waterline {{link}} Reply STOP to stop messages.",
    "We will blacklist you. {{client_name}} via Waterline {{link}} Reply STOP to stop messages.",
    "We will tell your neighbours. {{client_name}} via Waterline {{link}} Reply STOP to stop messages.",
    "Hello {{client_name}} via Waterline {{link}}.",  # no STOP line
    "Hello {{client_name}} {{link}} Reply STOP to stop messages.",  # no via Waterline
    "Hello {{client_name}} via Waterline {{link}} {{child_surname}} Reply STOP to stop messages.",
])
def test_template_conduct_rules_reject(bad):
    with pytest.raises(templates.TemplateRejected):
        templates.check_template(bad)


def test_render_fills_and_collapses_empty_discount_line():
    text = templates.render(segments.get("school").templates["REMINDER"], {
        "debtor_first_name": "Ada", "client_name": "Greenfield", "balance": "₦1.00", "description": "fees",
        "discount_line": "", "link": "https://x/p/t",
    })
    assert "  " not in text and "Reply STOP to stop messages." in text and "via Waterline" in text


def test_whatsapp_and_sms_links_carry_prefilled_text():
    out = links("+2348031234567", "Hello Ada & co")
    assert out["whatsapp"] == "https://wa.me/2348031234567?text=Hello%20Ada%20%26%20co"
    assert out["sms"].startswith("sms:+2348031234567?&body=Hello%20Ada")


def test_contact_window_and_sundays():
    monday = date(2026, 11, 2)
    assert in_contact_window(wat(monday, 8, 0), set())
    assert not in_contact_window(wat(monday, 7, 59), set())
    assert not in_contact_window(wat(monday, 19, 0), set())
    assert in_contact_window(wat(date(2026, 11, 7), 12), set())  # Saturday
    assert not in_contact_window(wat(date(2026, 11, 8), 12), set())  # Sunday
    assert not in_contact_window(wat(monday, 12), {monday})  # holiday


def test_working_days_skip_weekends_and_holidays():
    friday = date(2026, 11, 6)
    assert working_days_between(friday, date(2026, 11, 13), set()) == 5
    assert working_days_between(friday, date(2026, 11, 13), {date(2026, 11, 9)}) == 4


def test_rules_validation_and_plan_options():
    with pytest.raises(RulesInvalid):
        validate_rules({"max_instalments": 7})
    with pytest.raises(RulesInvalid):
        validate_rules({"discount_percent": 10})  # no deadline
    rules = validate_rules({"min_instalment": 20_000_00, "max_instalments": 4})
    options = plan_options(rules, 100_000_00, date(2026, 11, 2))
    assert [o["instalments"] for o in options] == [2, 3, 4]
    three = options[1]
    assert sum(s["amount"] for s in three["schedule"]) == 100_000_00
    assert three["instalment_amount"] == 33_334_00
    assert plan_options(validate_rules({"min_instalment": 60_000_00, "max_instalments": 6}), 100_000_00, date(2026, 11, 2)) == []


def test_discount_only_before_deadline():
    rules = validate_rules({"discount_percent": 10, "discount_deadline": "2026-11-30"})
    assert active_discount(rules, 100_000_00, date(2026, 11, 30))["pay_amount"] == 90_000_00
    assert active_discount(rules, 100_000_00, date(2026, 12, 1)) is None


def test_totp_roundtrip():
    secret = totp.new_secret()
    code = totp.code_at(secret, 1_700_000_000)
    assert totp.verify(secret, code, now=1_700_000_000)
    assert not totp.verify(secret, code, now=1_700_000_000 + 600)


SCHOOL = segments.get("school")


def _csv(seg, rows):
    return ("\n".join([",".join(seg.column_names)] + [",".join(r) for r in rows]) + "\n").encode()


@pytest.mark.parametrize("key", list(segments.SEGMENTS))
def test_upload_rejects_extra_columns_in_every_segment(key):
    seg = segments.get(key)
    header = ",".join(seg.column_names + ["guarantor_name"])
    with pytest.raises(upload.UploadRejected, match="guarantor_name"):
        upload.read_for(seg, "new", "x.csv", (header + "\n").encode())


@pytest.mark.parametrize("key", list(segments.SEGMENTS))
def test_upload_xlsx_template_roundtrip_per_segment(key):
    seg = segments.get(key)
    rows = upload.read_for(seg, "new", "t.xlsx", upload.template_xlsx(seg))
    preview = upload.build_preview(rows, seg, set())
    assert {p["status"] for p in preview} == {"valid"}, preview
    assert preview[0]["clean"]["phone"] == "+2348031234567"


def test_lender_upload_requires_loan_reference_and_keeps_extra_fields():
    seg = segments.get("lender")
    rows = upload.read_for(seg, "new", "t.csv", upload.template_csv(seg).encode())
    [first, _] = upload.build_preview(rows, seg, set())
    assert first["clean"]["external_reference"] == "LN-2025-00981"
    assert first["clean"]["extra"] == {"disbursed_on": "2025-09-01", "days_past_due": 34}
    bad = upload.build_preview([{**rows[0], "loan_reference": ""}], seg, set())
    assert bad[0]["status"] == "error"


def test_school_only_accepts_child_first_name():
    rows = upload.read_for(SCHOOL, "new", "x.csv", _csv(SCHOOL, [["A B", "08031234567", "", "fees", "", "Tolu Okafor", "100", ""]]))
    assert upload.build_preview(rows, SCHOOL, set())[0]["status"] == "error"
    estate = segments.get("estate")
    with pytest.raises(upload.UploadRejected, match="dependant_first_name"):
        upload.read_for(estate, "new", "x.csv", b"debtor_name,phone,description,amount_owed,dependant_first_name\n")


def test_segment_templates_reject_segment_specific_wording():
    body = ("Hello {{debtor_first_name}}, pay or we will evict you. {{client_name}} via Waterline {{link}} "
            "Reply STOP to stop messages.")
    templates.check_template(body.replace("evict you", "be glad"), segments.get("estate"))
    with pytest.raises(templates.TemplateRejected):
        templates.check_template(body, segments.get("estate"))


def test_policy_overrides_can_only_tighten():
    seg = segments.get("school")
    assert segments.validate_policy_override(seg, {"contact_start": "09:00", "max_per_7_days": 1}) == {
        "contact_start": "09:00", "max_per_7_days": 1}
    for loosen in ({"contact_start": "07:00"}, {"contact_end": "20:00"}, {"max_per_7_days": 3},
                   {"contact_weekdays": [0, 1, 2, 3, 4, 5, 6]}, {"sequence_days": [0, 1, 7]},
                   {"repeat_every_days": 3}, {"max_unanswered": 9}, {"surprise": 1}):
        with pytest.raises(segments.PolicyInvalid):
            segments.validate_policy_override(seg, loosen)


def test_fee_schedules_validate_and_tier_by_age():
    tiered = segments.validate_fee_schedule({"type": "tiered_by_age", "tiers": [
        {"max_days": 90, "percent": 15}, {"max_days": 180, "percent": 20}, {"max_days": None, "percent": 25}]})
    assert [segments.fee_percent_for(tiered, d) for d in (0, 90, 91, 180, 181, 5000)] == [15, 15, 20, 20, 25, 25]
    assert segments.describe_fee(tiered) == "15% (0–90 days overdue), 20% (91–180 days overdue), 25% (over 180 days overdue)"
    assert segments.validate_fee_schedule({"type": "flat", "percent": "12.5"}) == {"type": "flat", "percent": 12.5}
    for bad in ({"type": "flat", "percent": 0}, {"type": "flat", "percent": 51}, {"type": "weird"},
                {"type": "tiered_by_age", "tiers": [{"max_days": 90, "percent": 10}]},
                {"type": "tiered_by_age", "tiers": [{"max_days": 90, "percent": 10}, {"max_days": 30, "percent": 10},
                                                    {"max_days": None, "percent": 10}]},
                {"type": "tiered_by_age", "tiers": [{"max_days": 90, "percent": 10}, {"max_days": 120, "percent": 10}]}):
        with pytest.raises(segments.FeeInvalid):
            segments.validate_fee_schedule(bad)
