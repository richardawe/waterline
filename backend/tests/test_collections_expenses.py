"""Team expenses: a team member adds a claim (with a receipt) and their bank
details; it shows up for the Admin, who approves/rejects and marks it paid.
People only see their own claims; only Admins decide."""

from datetime import timedelta

from sqlalchemy import select, text

from app.collections.models import EmailMessage, Expense
from app.collections.timeutil import lagos_date, utcnow
from tests.collections_factory import make_ops
from tests.conftest import requires_db

pytestmark = requires_db

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def _add(api, h, amount="12,500", **kw):
    data = {"spent_on": str(lagos_date(utcnow())), "amount": amount, "category": "Transport",
            "description": "Uber to Greenfield Academy for onboarding"} | kw
    return api.post("/collections/ops/expenses", headers=h, data=data,
                    files={"receipt": ("receipt.png", PNG, "image/png")})


def test_claim_flows_from_team_member_to_admin_and_gets_paid(cl_api, cl_db):
    tosin, h = make_ops(cl_db, role="Ops")
    admin, admin_h = make_ops(cl_db, role="Admin")

    r = cl_api.put("/collections/ops/me/payout", headers=h,
                   json={"bank_name": "GTBank", "account_number": "0123456789", "account_name": "Tosin Bashua"})
    assert r.status_code == 200, r.text
    raw = cl_db.execute(text("SELECT payout_details FROM cl_ops_user WHERE id = :i"), {"i": tosin.id}).scalar_one()
    assert "0123456789" not in raw  # encrypted at rest

    r = _add(cl_api, h)
    assert r.status_code == 200, r.text
    claim = r.json()
    assert claim["amount"] == 12_500_00 and claim["status"] == "Submitted" and claim["receipt_url"]
    assert "payout" not in claim
    # Admins were emailed
    sent = cl_db.execute(select(EmailMessage).where(EmailMessage.kind == "expense_submitted")).scalars().all()
    assert admin.email in [m.to_address for m in sent] and tosin.email not in [m.to_address for m in sent]

    # a team member only sees their own, and can't see everyone's
    assert len(cl_api.get("/collections/ops/expenses", headers=h).json()["expenses"]) == 1
    assert cl_api.get("/collections/ops/expenses?scope=all", headers=h).status_code == 403
    assert cl_api.post(f"/collections/ops/expenses/{claim['id']}/approve", headers=h).status_code == 403

    # the Admin sees it with the bank details, and what's owed
    rows = cl_api.get("/collections/ops/expenses?scope=all", headers=admin_h).json()["expenses"]
    mine = next(x for x in rows if x["id"] == claim["id"])
    assert mine["name"] == tosin.name and mine["payout"]["account_number"] == "0123456789"
    summary = cl_api.get("/collections/ops/expenses/summary", headers=admin_h).json()
    person = next(p for p in summary["people"] if p["ops_user_id"] == tosin.id)
    assert person["owed"] == 12_500_00 and person["payout"]["bank_name"] == "GTBank"

    assert cl_api.post(f"/collections/ops/expenses/{claim['id']}/approve", headers=admin_h).json()["status"] == "Approved"
    # once approved, the team member can't delete it
    assert cl_api.delete(f"/collections/ops/expenses/{claim['id']}", headers=h).status_code == 409
    r = cl_api.post(f"/collections/ops/expenses/{claim['id']}/paid", headers=admin_h, json={"reference": "GTB-TRF-8812"})
    assert r.status_code == 200 and r.json()["status"] == "Paid" and r.json()["paid_reference"] == "GTB-TRF-8812"
    assert cl_api.post(f"/collections/ops/expenses/{claim['id']}/paid", headers=admin_h, json={}).status_code == 409
    person = next(p for p in cl_api.get("/collections/ops/expenses/summary", headers=admin_h).json()["people"]
                  if p["ops_user_id"] == tosin.id)
    assert person["owed"] == 0 and person["paid_this_month"] == 12_500_00
    assert cl_db.execute(select(EmailMessage).where(EmailMessage.kind == "expense_paid")).first()
    actions = set(cl_db.execute(text("SELECT action FROM cl_audit_log WHERE entity_id = :i"), {"i": claim["id"]}).scalars())
    assert {"expense.submitted", "expense.approved", "expense.paid"} <= actions


def test_validation_reject_and_delete(cl_api, cl_db):
    tosin, h = make_ops(cl_db, role="Ops")
    other, other_h = make_ops(cl_db, role="Ops")
    _, admin_h = make_ops(cl_db, role="Admin")
    assert _add(cl_api, h, amount="abc").status_code == 422
    assert _add(cl_api, h, amount="0").status_code == 422
    assert _add(cl_api, h, spent_on=str(lagos_date(utcnow()) + timedelta(days=1))).status_code == 422
    assert _add(cl_api, h, category="Holiday").status_code == 422
    bad = cl_api.post("/collections/ops/expenses", headers=h, files={"receipt": ("x.png", b"not an image", "image/png")},
                      data={"spent_on": str(lagos_date(utcnow())), "amount": "500", "category": "Meals", "description": "Lunch"})
    assert bad.status_code == 422
    # no receipt is fine
    ok = cl_api.post("/collections/ops/expenses", headers=h,
                     data={"spent_on": str(lagos_date(utcnow())), "amount": "1500.50", "category": "Airtime & data",
                           "description": "Data for WhatsApp follow-ups"})
    assert ok.status_code == 200 and ok.json()["amount"] == 1500_50 and ok.json()["receipt_url"] is None
    cid = ok.json()["id"]
    # someone else can't delete it
    assert cl_api.delete(f"/collections/ops/expenses/{cid}", headers=other_h).status_code == 404
    r = cl_api.post(f"/collections/ops/expenses/{cid}/reject", headers=admin_h, json={"reason": "Personal data plan"})
    assert r.json()["status"] == "Rejected"
    assert cl_api.post(f"/collections/ops/expenses/{cid}/paid", headers=admin_h, json={}).status_code == 409

    second = _add(cl_api, h).json()
    assert cl_api.delete(f"/collections/ops/expenses/{second['id']}", headers=h).status_code == 200
    assert cl_db.get(Expense, second["id"]) is None
    # payout account number must be 10 digits (NUBAN)
    assert cl_api.put("/collections/ops/me/payout", headers=h,
                      json={"bank_name": "GTB", "account_number": "123", "account_name": "T"}).status_code == 422
