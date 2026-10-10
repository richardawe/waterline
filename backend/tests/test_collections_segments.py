"""Segments (schools, estates live; lenders, service businesses on a waitlist),
ops switching, fee proposals, tiered fees and balance-update uploads."""

import json
from datetime import timedelta

from sqlalchemy import select

from app.collections import audit, outreach, service
from app.collections.models import AccountStatus, AuditLog, Client, DebtorAccount, DebtorPayment, FeeProposal
from app.collections.timeutil import lagos_date, utcnow
from tests.collections_factory import (
    a_weekday,
    add_sent,
    client_headers,
    make_account,
    make_client,
    make_ops,
    wat,
)
from tests.conftest import requires_db
from tests.test_collections_api import _signup_payload

pytestmark = requires_db
TODAY = lambda: lagos_date(utcnow())  # noqa: E731


# ---------------------------------------------------------------- public config + sign-up gating


def test_public_config_lists_live_and_waitlist_segments(cl_api):
    cfg = cl_api.get("/collections/public/config").json()
    status = {s["key"]: s["status"] for s in cfg["segments"]}
    assert status == {"school": "live", "estate": "live", "lender": "waitlist", "service": "waitlist"}
    lender = next(s for s in cfg["segments"] if s["key"] == "lender")
    assert "Microfinance bank" in lender["client_types"] and lender["terms"]["debtor"] == "borrower"


def test_estate_can_sign_up_with_its_own_types(cl_api, cl_db):
    r = cl_api.post("/collections/public/signup", json=_signup_payload(
        segment="estate", business_name="Palm Court Estate Test", business_type="Residents' association",
        email="chair@palmcourt.example"))
    assert r.status_code == 201, r.text
    client = cl_db.execute(select(Client).where(Client.business_name == "Palm Court Estate Test")).scalar_one()
    assert client.segment == "estate" and json.loads(client.fee_schedule) == {"type": "flat", "percent": 20}
    # a school type doesn't belong to the estate segment
    bad = cl_api.post("/collections/public/signup", json=_signup_payload(
        segment="estate", business_type="Secondary school", email="x@palmcourt.example"))
    assert bad.status_code == 422


def test_lender_sign_up_refused_while_on_waitlist_then_allowed_when_ops_switch_it_on(cl_api, cl_db):
    payload = _signup_payload(segment="lender", business_name="Kudi MFB Test", business_type="Microfinance bank",
                              email="ops@kudi.example", client_fields={"licence_type": "CBN MFB licence",
                                                                       "licence_number": "MFB/2021/123"})
    r = cl_api.post("/collections/public/signup", json=payload)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "waitlist"

    _, admin = make_ops(cl_db, role="Admin")
    _, ops = make_ops(cl_db, role="Ops")
    assert cl_api.put("/collections/ops/segments/lender", json={"status": "live"}, headers=ops).status_code == 403
    # lenders need an explicit conduct-policy review before going live
    r = cl_api.put("/collections/ops/segments/lender", json={"status": "live"}, headers=admin)
    assert r.status_code == 409 and "reviewed" in r.json()["detail"]
    r = cl_api.put("/collections/ops/segments/lender", json={"status": "live", "confirm_policy_review": True}, headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "live"
    assert cl_db.execute(select(AuditLog).where(AuditLog.action == "segment.updated", AuditLog.entity_id == "lender")).first()

    r = cl_api.post("/collections/public/signup", json=payload)
    assert r.status_code == 201, r.text
    client = cl_db.execute(select(Client).where(Client.business_name == "Kudi MFB Test")).scalar_one()
    assert client.segment == "lender"
    assert json.loads(client.fee_schedule)["type"] == "tiered_by_age"
    assert json.loads(client.profile_extra)["licence_number"] == "MFB/2021/123"
    # lender licence fields are required
    missing = cl_api.post("/collections/public/signup", json={**payload, "email": "b@kudi.example", "client_fields": {}})
    assert missing.status_code == 422 and "licence_type" in missing.json()["detail"]["fields"]


def test_switching_a_live_segment_to_waitlist_closes_sign_up_only(cl_api, cl_db):
    _, admin = make_ops(cl_db)
    existing, user = make_client(cl_db, segment="estate", name="Old Estate")
    assert cl_api.put("/collections/ops/segments/estate", json={"status": "waitlist"}, headers=admin).status_code == 200
    r = cl_api.post("/collections/public/signup", json=_signup_payload(segment="estate", business_type="Landlord",
                                                                       email="n@estate.example"))
    assert r.status_code == 409
    # existing estate clients keep working
    assert cl_api.get("/collections/client/dashboard", headers=client_headers(cl_db, user)).status_code == 200


def test_waitlist(cl_api, cl_db):
    r = cl_api.post("/collections/public/waitlist", json={
        "segment": "lender", "business_name": "Naira Finance", "business_type": "Digital lender",
        "contact_name": "Ife Ade", "email": "ife@naira.example", "phone": "08031234567", "note": "~2,000 loans/month"})
    assert r.status_code == 201
    _, ops = make_ops(cl_db, role="Ops")
    rows = cl_api.get("/collections/ops/waitlist?segment=lender", headers=ops).json()
    entry = next(w for w in rows if w["business_name"] == "Naira Finance")
    assert entry["email"] == "ife@naira.example" and entry["phone"] == "+2348031234567"
    assert cl_api.post(f"/collections/ops/waitlist/{entry['id']}/status", json={"status": "Contacted"}, headers=ops).status_code == 200
    seg_row = next(s for s in cl_api.get("/collections/ops/segments", headers=ops).json() if s["key"] == "lender")
    assert seg_row["status"] == "waitlist"


def test_segment_policy_override_tightens_contact_window(cl_api, cl_db):
    _, admin = make_ops(cl_db)
    r = cl_api.put("/collections/ops/segments/estate", json={"policy_overrides": {"contact_start": "07:00"}}, headers=admin)
    assert r.status_code == 422
    r = cl_api.put("/collections/ops/segments/estate", json={"policy_overrides": {"contact_start": "10:00"}}, headers=admin)
    assert r.status_code == 200 and r.json()["policy"]["contact_start"] == "10:00"

    estate, _ = make_client(cl_db, segment="estate", name="Late Estate")
    school, _ = make_client(cl_db, name="Early School")
    day = a_weekday()
    a_estate = make_account(cl_db, estate, phone="+2348031110001")
    a_school = make_account(cl_db, school, phone="+2348031110002")
    outreach.build_queue(cl_db, wat(day, 8))
    items = {i.account_id: i for i in cl_db.execute(select(outreach.OutreachItem)).scalars()}
    outreach.mark_sent(cl_db, items[a_school.id], "WhatsApp", audit.SYSTEM, now=wat(day, 9))
    try:
        outreach.mark_sent(cl_db, items[a_estate.id], "WhatsApp", audit.SYSTEM, now=wat(day, 9))
        raise AssertionError("estate send before 10:00 should be refused")
    except outreach.OutreachRefused as exc:
        assert "10:00" in str(exc)
    outreach.mark_sent(cl_db, items[a_estate.id], "WhatsApp", audit.SYSTEM, now=wat(day, 10, 5))


def test_each_segment_queues_its_own_template_wording(cl_db):
    lender, _ = make_client(cl_db, segment="lender", name="Kudi MFB Test")
    account = make_account(cl_db, lender, phone="+2348031110003")
    account.description = "Salary advance loan"
    outreach.build_queue(cl_db, wat(a_weekday(), 9))
    item = cl_db.execute(select(outreach.OutreachItem).where(outreach.OutreachItem.account_id == account.id)).scalar_one()
    assert "about your Salary advance loan" in item.rendered_text and "Kudi MFB Test via Waterline" in item.rendered_text


# ---------------------------------------------------------------- fee proposals


def test_client_proposes_fee_and_admin_approves(cl_api, cl_db):
    client, user = make_client(cl_db)
    h = client_headers(cl_db, user)
    _, admin = make_ops(cl_db, role="Admin")
    _, ops = make_ops(cl_db, role="Ops")
    r = cl_api.post("/collections/client/fee/proposals", json={"schedule": {"type": "flat", "percent": 15},
                                                                "note": "We have 900 accounts"}, headers=h)
    assert r.status_code == 201 and r.json()["text"] == "15% of confirmed recoveries"
    # a newer proposal replaces the pending one
    r2 = cl_api.post("/collections/client/fee/proposals", json={"schedule": {"type": "flat", "percent": 16}}, headers=h)
    assert cl_db.get(FeeProposal, r.json()["id"]).status == "Withdrawn"
    pending = cl_api.get("/collections/ops/fee-proposals", headers=ops).json()
    mine = [p for p in pending if p["client_id"] == client.id]
    assert len(mine) == 1 and mine[0]["current_text"] == "20% of confirmed recoveries"
    pid = r2.json()["id"]
    assert cl_api.post(f"/collections/ops/fee-proposals/{pid}/decide", json={"approve": True}, headers=ops).status_code == 403
    r = cl_api.post(f"/collections/ops/fee-proposals/{pid}/decide", json={"approve": True, "note": "Welcome"}, headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "Approved"
    cl_db.refresh(client)
    assert json.loads(client.fee_schedule) == {"type": "flat", "percent": 16}
    assert cl_api.get("/collections/client/fee", headers=h).json()["current_text"] == "16% of confirmed recoveries"
    actions = set(cl_db.execute(select(AuditLog.action).where(AuditLog.client_id == client.id)).scalars())
    assert {"fee.proposed", "fee.approved"} <= actions

    # the approved fee applies to the next confirmation
    account = make_account(cl_db, client)
    add_sent(cl_db, account, wat(TODAY() - timedelta(days=1), 10))
    p = service.record_client_payment(cl_db, account, amount=10_000_00, paid_on=TODAY(), note=None, actor=audit.SYSTEM, today=TODAY())
    assert p.fee_percent == 16 and p.fee_amount == 1_600_00


def test_rejected_and_invalid_proposals(cl_api, cl_db):
    client, user = make_client(cl_db)
    h = client_headers(cl_db, user)
    _, admin = make_ops(cl_db)
    assert cl_api.post("/collections/client/fee/proposals", json={"schedule": {"type": "flat", "percent": 0}}, headers=h).status_code == 422
    assert cl_api.post("/collections/client/fee/proposals", json={"schedule": {"type": "flat", "percent": 20}}, headers=h).status_code == 422
    pid = cl_api.post("/collections/client/fee/proposals", json={"schedule": {"type": "flat", "percent": 5}}, headers=h).json()["id"]
    r = cl_api.post(f"/collections/ops/fee-proposals/{pid}/decide", json={"approve": False, "note": "Too low"}, headers=admin)
    assert r.json()["status"] == "Rejected"
    cl_db.refresh(client)
    assert json.loads(client.fee_schedule)["percent"] == 20
    assert cl_api.post(f"/collections/ops/fee-proposals/{pid}/decide", json={"approve": True}, headers=admin).status_code == 409


def test_fee_can_be_proposed_at_sign_up(cl_api, cl_db):
    r = cl_api.post("/collections/public/signup", json=_signup_payload(
        business_name="Bright Minds Test", email="b@bright.example", proposed_fee_percent=12, fee_note="Small school"))
    assert r.status_code == 201
    client = cl_db.execute(select(Client).where(Client.business_name == "Bright Minds Test")).scalar_one()
    proposal = cl_db.execute(select(FeeProposal).where(FeeProposal.client_id == client.id)).scalar_one()
    assert proposal.status == "Pending" and json.loads(proposal.schedule) == {"type": "flat", "percent": 12}
    assert json.loads(client.fee_schedule)["percent"] == 20  # default until ops approve


def test_tiered_fee_uses_debt_age(cl_db):
    tiers = {"type": "tiered_by_age", "tiers": [{"max_days": 90, "percent": 15}, {"max_days": None, "percent": 25}]}
    client, _ = make_client(cl_db, segment="lender", name="Tiered MFB", fee_schedule=tiers)
    young = make_account(cl_db, client, phone="+2348031110004")
    young.due_date = TODAY() - timedelta(days=30)
    old = make_account(cl_db, client, phone="+2348031110005")
    old.extra = json.dumps({"days_past_due": 200})
    for a in (young, old):
        add_sent(cl_db, a, wat(TODAY() - timedelta(days=1), 10))
    p1 = service.record_client_payment(cl_db, young, amount=100_000_00, paid_on=TODAY(), note=None, actor=audit.SYSTEM, today=TODAY())
    p2 = service.record_client_payment(cl_db, old, amount=100_000_00, paid_on=TODAY(), note=None, actor=audit.SYSTEM, today=TODAY())
    assert (p1.fee_percent, p1.fee_amount) == (15, 15_000_00)
    assert (p2.fee_percent, p2.fee_amount) == (25, 25_000_00)


# ---------------------------------------------------------------- balance-update uploads


def _upload(cl_api, h, csv_text, mode):
    return cl_api.post("/collections/client/uploads", data={"mode": mode},
                       files={"file": ("b.csv", csv_text.encode(), "text/csv")}, headers=h)


def test_balance_update_upload(cl_api, cl_db):
    client, user = make_client(cl_db, segment="lender", name="Sync MFB")
    h = client_headers(cl_db, user)
    new = ("debtor_name,phone,description,loan_reference,amount_owed,days_past_due\n"
           "Tunde B,08031230001,Salary advance loan,LN-1,100000,30\n"
           "Grace E,08031230002,SME loan,LN-2,50000,10\n"
           "Musa K,08031230003,SME loan,LN-3,40000,5\n"
           "Ada O,08031230004,SME loan,LN-4,30000,5\n")
    r = _upload(cl_api, h, new, "new")
    assert r.status_code == 201 and r.json()["batch"]["valid_count"] == 4
    assert cl_api.post(f"/collections/client/uploads/{r.json()['batch']['id']}/import", json={"attest": True}, headers=h).status_code == 200
    accounts = {a.external_reference: a for a in cl_db.execute(select(DebtorAccount).where(DebtorAccount.client_id == client.id)).scalars()}
    for a in accounts.values():
        add_sent(cl_db, a, wat(TODAY() - timedelta(days=2), 10))

    # re-uploading the same loan references is a duplicate, not a second account
    again = _upload(cl_api, h, new, "new").json()
    assert again["batch"]["duplicate_count"] == 4

    update = ("loan_reference,amount_owed,due_date,reason\n"
              "LN-1,110000,,\n"           # interest accrued → balance up
              "LN-2,20000,,payment\n"     # paid lender directly → recorded payment, fee rules apply
              "LN-3,0,,correction\n"      # wasn't owed → closed, no fee
              "LN-4,30000,,\n"            # unchanged
              "LN-9,1000,,\n")            # unknown reference → error
    r = _upload(cl_api, h, update, "update")
    assert r.status_code == 201, r.text
    body = r.json()
    statuses = {row["raw"]["loan_reference"]: row["status"] for row in body["rows"]}
    assert statuses == {"LN-1": "valid", "LN-2": "valid", "LN-3": "valid", "LN-4": "unchanged", "LN-9": "error"}
    assert cl_api.post(f"/collections/client/uploads/{body['batch']['id']}/import", json={"attest": True}, headers=h).status_code == 200

    for a in accounts.values():
        cl_db.refresh(a)
    assert accounts["LN-1"].balance == 110_000_00 and accounts["LN-1"].original_amount == 110_000_00
    assert accounts["LN-2"].balance == 20_000_00
    pay = cl_db.execute(select(DebtorPayment).where(DebtorPayment.account_id == accounts["LN-2"].id)).scalar_one()
    assert pay.source == "Client recorded" and pay.amount_confirmed == 30_000_00 and pay.fee_eligible
    assert accounts["LN-3"].status == AccountStatus.WITHDRAWN
    assert cl_db.execute(select(DebtorPayment).where(DebtorPayment.account_id == accounts["LN-3"].id)).first() is None
    assert cl_db.execute(select(AuditLog).where(AuditLog.action == "account.balance_updated")).first()


def test_update_upload_rejects_disputed_and_extra_columns(cl_api, cl_db):
    client, user = make_client(cl_db, name="Sync School")
    h = client_headers(cl_db, user)
    r = _upload(cl_api, h, "debtor_name,phone,description,admission_number,amount_owed\n"
                           "Ada,08031230005,Fees,ADM-1,5000\n", "new")
    cl_api.post(f"/collections/client/uploads/{r.json()['batch']['id']}/import", json={"attest": True}, headers=h)
    account = cl_db.execute(select(DebtorAccount).where(DebtorAccount.client_id == client.id)).scalar_one()
    service.raise_dispute(cl_db, account, type="Wrong amount", details=None, evidence_bytes=None, raised_by="Debtor", actor=audit.SYSTEM)
    r = _upload(cl_api, h, "admission_number,amount_owed\nADM-1,1000\n", "update")
    assert r.json()["rows"][0]["status"] == "error" and "query" in r.json()["rows"][0]["errors"][0]
    r = _upload(cl_api, h, "admission_number,amount_owed,parent_employer\nADM-1,1000,Acme\n", "update")
    assert r.status_code == 422


def test_client_template_download_matches_segment(cl_api, cl_db):
    _, user = make_client(cl_db, segment="estate", name="Template Estate")
    h = client_headers(cl_db, user)
    csv_text = cl_api.get("/collections/client/upload-template.csv", headers=h).text
    assert csv_text.splitlines()[0] == "debtor_name,phone,email,description,unit,amount_owed,due_date"
    upd = cl_api.get("/collections/client/upload-template.csv?mode=update", headers=h).text
    assert upd.splitlines()[0] == "unit,amount_owed,due_date,reason"
    assert cl_api.get("/collections/public/upload-template.xlsx?segment=lender").status_code == 200


def test_ops_checklist_follows_segment(cl_api, cl_db):
    _, admin = make_ops(cl_db)
    lender, _ = make_client(cl_db, segment="lender", name="Check MFB", status="Pending")
    detail = cl_api.get(f"/collections/ops/clients/{lender.id}", headers=admin).json()
    keys = [c["key"] for c in detail["checklist_items"]]
    assert "licence_verified" in keys
    base = {k: True for k in keys if k != "licence_verified"}
    cl_api.put(f"/collections/ops/clients/{lender.id}/verification", json={"checklist": base}, headers=admin)
    r = cl_api.post(f"/collections/ops/clients/{lender.id}/status", json={"status": "Approved"}, headers=admin)
    assert r.status_code == 409 and "licence_verified" in r.json()["detail"]
