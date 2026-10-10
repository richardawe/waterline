from datetime import date

import pytest

from app.collections import templates, totp, upload
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


def test_default_templates_pass_conduct_rules():
    for body in templates.DEFAULT_TEMPLATES.values():
        templates.check_template(body)


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
    text = templates.render(templates.DEFAULT_TEMPLATES["REMINDER"], {
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


def _csv(rows):
    return ("\n".join([",".join(upload.COLUMNS)] + [",".join(r) for r in rows]) + "\n").encode()


def test_upload_rejects_extra_columns():
    data = b"debtor_name,phone,description,amount_owed,guarantor_name\nA,08031234567,fees,100,B\n"
    with pytest.raises(upload.UploadRejected, match="guarantor_name"):
        upload.read_rows("x.csv", data)


def test_upload_xlsx_roundtrip():
    rows = upload.read_rows("t.xlsx", upload.template_xlsx())
    preview = upload.build_preview(rows, set())
    assert [p["status"] for p in preview] == ["valid", "valid"]
    assert preview[0]["clean"]["phone"] == "+2348031234567"


def test_upload_rejects_child_surname_in_dependant_field():
    rows = upload.read_rows("x.csv", _csv([["A B", "08031234567", "", "fees", "Tolu Okafor", "100", ""]]))
    assert upload.build_preview(rows, set())[0]["status"] == "error"
