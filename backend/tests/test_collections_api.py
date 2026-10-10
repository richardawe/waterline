"""End-to-end Collections flows over HTTP, plus tenant-isolation and audit
coverage. Uses `cl_api`, whose handler commits become savepoints inside a
rolled-back transaction."""

import csv
import io
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select, text

from app.collections import audit, billing, outreach, service, totp
from app.collections.models import (
    AccountStatus,
    AuditLog,
    Client,
    ClientStatus,
    ClientUser,
    DebtorAccount,
    DebtorPayment,
    EmailMessage,
    Invoice,
    OutreachItem,
)
from app.collections.timeutil import lagos_date, utcnow
from tests.collections_factory import (
    PASSWORD,
    a_weekday,
    add_sent,
    client_headers,
    make_account,
    make_client,
    make_ops,
    wat,
)
from tests.conftest import requires_db

pytestmark = requires_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TODAY = lambda: lagos_date(utcnow())  # noqa: E731


def _signup_payload(**overrides):
    body = {
        "business_name": "Sunrise Academy", "business_type": "School", "contact_name": "Ngozi Eze", "role": "Bursar",
        "email": "ngozi@sunrise.example", "phone": "0803 123 4567", "state": "Lagos", "city": "Yaba",
        "bank_name": "Access Bank", "account_number": "0123456789", "account_name": "Sunrise Academy Ltd",
        "password": "school-fees-2026", "accept_terms": True, "accept_dpa": True, "accept_privacy": True,
    }
    body.update(overrides)
    return body


def _last_link(db, kind: str) -> str:
    row = db.execute(select(EmailMessage).where(EmailMessage.kind == kind).order_by(EmailMessage.created_at.desc())).scalars().first()
    return re.search(r"token=([\w-]+)", row.body).group(1)


# ---------------------------------------------------------------- sign-up → pending dashboard


def test_signup_verify_login_lands_on_pending_dashboard_and_nothing_queues(cl_api, cl_db):
    r = cl_api.post("/collections/public/signup", json=_signup_payload())
    assert r.status_code == 201, r.text
    client = cl_db.execute(select(Client).where(Client.business_name == "Sunrise Academy")).scalar_one()
    assert client.status == ClientStatus.PENDING
    assert client.terms_accepted_at and client.dpa_accepted_at and client.privacy_accepted_at
    # encrypted at rest
    raw = cl_db.execute(text("SELECT account_number FROM cl_client WHERE id = :id"), {"id": client.id}).scalar_one()
    assert "0123456789" not in raw
    raw_phone = cl_db.execute(text("SELECT phone, email FROM cl_client_user WHERE client_id = :id"), {"id": client.id}).one()
    assert "8031234567" not in raw_phone[0] and "sunrise" not in raw_phone[1]

    assert cl_api.post("/collections/public/login", json={"email": "ngozi@sunrise.example", "password": "school-fees-2026"}).status_code == 403
    token = _last_link(cl_db, "client_verify_email")
    session = cl_api.post("/collections/public/verify-email", json={"token": token}).json()["token"]
    assert cl_api.post("/collections/public/verify-email", json={"token": token}).status_code == 400  # one-time

    r = cl_api.post("/collections/public/login", json={"email": "NGOZI@sunrise.example", "password": "school-fees-2026"})
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    me = cl_api.get("/collections/client/me", headers=headers).json()
    assert me["client"]["status"] == "Pending"
    assert cl_api.get("/collections/client/dashboard", headers={"Authorization": f"Bearer {session}"}).status_code == 200

    # pending clients can prepare uploads but nothing is ever queued
    account = make_account(cl_db, client, status=AccountStatus.NEW)
    outreach.build_queue(cl_db, wat(a_weekday(), 9))
    assert cl_db.execute(select(OutreachItem).where(OutreachItem.account_id == account.id)).first() is None


def test_signup_requires_all_three_acceptances_and_valid_bank(cl_api):
    r = cl_api.post("/collections/public/signup", json=_signup_payload(accept_dpa=False, account_number="123"))
    assert r.status_code == 422
    fields = r.json()["detail"]["fields"]
    assert "accept" in fields and "account_number" in fields


def test_wrong_password_gives_generic_error(cl_api, cl_db):
    _, user = make_client(cl_db, email="x@school.example")
    r = cl_api.post("/collections/public/login", json={"email": "x@school.example", "password": "nope-nope-1"})
    assert r.status_code == 401 and r.json()["detail"] == "Email or password is incorrect."
    assert cl_api.post("/collections/public/login", json={"email": "x@school.example", "password": PASSWORD}).status_code == 200


def test_password_reset_flow(cl_api, cl_db):
    make_client(cl_db, email="reset@school.example")
    assert cl_api.post("/collections/public/forgot-password", json={"email": "reset@school.example"}).status_code == 200
    token = _last_link(cl_db, "client_reset_password")
    r = cl_api.post("/collections/public/reset-password", json={"token": token, "password": "brand-new-pass-1"})
    assert r.status_code == 200
    assert cl_api.post("/collections/public/login", json={"email": "reset@school.example", "password": "brand-new-pass-1"}).status_code == 200


def test_login_is_rate_limited(cl_api):
    codes = [cl_api.post("/collections/public/login", json={"email": "a@b.example", "password": "x"}).status_code for _ in range(11)]
    assert codes[-1] == 429


# ---------------------------------------------------------------- ops approvals


def test_ops_login_requires_totp(cl_api, cl_db):
    ops, _ = make_ops(cl_db)
    body = {"email": ops.email, "password": PASSWORD, "code": "000000"}
    assert cl_api.post("/collections/public/ops-login", json=body).status_code == 401
    body["code"] = totp.code_at(ops.totp_secret, __import__("time").time())
    assert cl_api.post("/collections/public/ops-login", json=body).status_code == 200


def test_ops_approve_reject_suspend(cl_api, cl_db):
    _, ops_h = make_ops(cl_db, role="Ops")
    client, _ = make_client(cl_db, status=ClientStatus.PENDING)
    url = f"/collections/ops/clients/{client.id}"
    assert cl_api.post(url + "/status", json={"status": "Approved"}, headers=ops_h).status_code == 409  # checklist first
    checks = {k: True for k in ["phone_call_made", "business_exists", "bank_name_matches", "contact_confirmed"]}
    assert cl_api.put(url + "/verification", json={"notes": "Called bursar", "checklist": checks}, headers=ops_h).status_code == 200
    r = cl_api.post(url + "/status", json={"status": "Approved"}, headers=ops_h)
    assert r.status_code == 200 and r.json()["status"] == "Approved"
    assert cl_db.execute(select(EmailMessage).where(EmailMessage.kind == "client_approved")).first()

    account = make_account(cl_db, client)
    day = a_weekday()
    outreach.build_queue(cl_db, wat(day, 8))
    item = cl_db.execute(select(OutreachItem).where(OutreachItem.account_id == account.id)).scalar_one()
    assert cl_api.post(url + "/status", json={"status": "Suspended"}, headers=ops_h).status_code == 422  # reason needed
    r = cl_api.post(url + "/status", json={"status": "Suspended", "reason": "Complaint under review"}, headers=ops_h)
    assert r.status_code == 200
    cl_db.refresh(item)
    assert item.status == "Skipped"
    outreach.build_queue(cl_db, wat(day + timedelta(days=1), 8))
    assert cl_db.execute(
        select(OutreachItem).where(OutreachItem.account_id == account.id, OutreachItem.status == "Queued")
    ).first() is None

    other, _ = make_client(cl_db, name="Other", status=ClientStatus.PENDING)
    r = cl_api.post(f"/collections/ops/clients/{other.id}/status", json={"status": "Rejected", "reason": "Could not verify"}, headers=ops_h)
    assert r.status_code == 200 and r.json()["status"] == "Rejected"


def test_ops_queue_api_marks_sent_and_returns_wa_link(cl_api, cl_db, monkeypatch):
    _, ops_h = make_ops(cl_db)
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    day = a_weekday()
    now = wat(day, 10)
    monkeypatch.setattr(outreach, "utcnow", lambda: now)
    import app.collections.api.ops as ops_api

    monkeypatch.setattr(ops_api, "utcnow", lambda: now)
    assert cl_api.post("/collections/ops/queue/build", headers=ops_h).json()["created"] == 1
    [item] = cl_api.get("/collections/ops/queue", headers=ops_h).json()["items"]
    assert item["links"]["whatsapp"].startswith("https://wa.me/2348031234567?text=Hello%20Adaeze")
    assert "%20via%20Waterline" in item["links"]["whatsapp"]
    r = cl_api.post(f"/collections/ops/queue/{item['id']}/sent", json={"channel": "WhatsApp"}, headers=ops_h)
    assert r.status_code == 200 and r.json()["status"] == "Sent"
    again = cl_api.post(f"/collections/ops/queue/{item['id']}/sent", json={"channel": "WhatsApp"}, headers=ops_h)
    assert again.status_code == 409
    r = cl_api.post(f"/collections/ops/queue/{item['id']}/reply", json={"category": "Promised to pay"}, headers=ops_h)
    assert r.status_code == 200 and r.json()["reply_category"] == "Promised to pay"
    cl_db.refresh(account)
    assert account.last_response_at is not None


# ---------------------------------------------------------------- upload


def _fifty_row_csv() -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["debtor_name", "phone", "email", "description", "dependant_first_name", "amount_owed", "due_date"])
    for i in range(50):
        w.writerow([f"Parent {i}", f"0803{1000000 + i}", "", "School fees – 1st term", "", f"{50000 + i}", "15/01/2026"])
    rows = out.getvalue().splitlines()
    rows[3] = "Parent X,12345,,School fees,,50000,"             # bad phone
    rows[8] = ",08039990001,,School fees,,50000,"              # no name
    rows[15] = "Parent Y,08039990002,,School fees,,-5,"        # amount <= 0
    rows[22] = "Parent Z,08039990003,not-an-email,School fees,,1000,"  # bad email
    rows[30] = "Parent W,08039990004,,School fees,,1000,31/31/2026"    # bad date
    return ("\n".join(rows) + "\n").encode()


def test_fifty_row_upload_with_five_bad_rows(cl_api, cl_db):
    client, user = make_client(cl_db)
    h = client_headers(cl_db, user)
    r = cl_api.post("/collections/client/uploads", files={"file": ("balances.csv", _fifty_row_csv(), "text/csv")}, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["batch"]["row_count"] == 50
    assert body["batch"]["valid_count"] == 45 and body["batch"]["error_count"] == 5
    errors = [row for row in body["rows"] if row["status"] == "error"]
    assert sorted(e["row"] for e in errors) == [4, 9, 16, 23, 31]
    assert all(e["errors"] for e in errors)
    batch_id = body["batch"]["id"]

    assert cl_api.post(f"/collections/client/uploads/{batch_id}/import", json={"attest": False}, headers=h).status_code == 422
    assert cl_db.execute(select(DebtorAccount).where(DebtorAccount.batch_id == batch_id)).first() is None

    # fix one error row in the preview
    fixed = cl_api.post(f"/collections/client/uploads/{batch_id}/revalidate",
                        json={"fixes": {"4": {"phone": "08035550000"}}}, headers=h).json()
    assert fixed["batch"]["valid_count"] == 46

    r = cl_api.post(f"/collections/client/uploads/{batch_id}/import", json={"attest": True, "skip_rows": [2]}, headers=h)
    assert r.status_code == 200, r.text
    accounts = cl_db.execute(select(DebtorAccount).where(DebtorAccount.batch_id == batch_id)).scalars().all()
    assert len(accounts) == 45  # 46 valid, one skipped by the client
    assert all(a.status == AccountStatus.NEW and len(a.access_token) >= 32 for a in accounts)
    assert all(a.phone.startswith("+234") for a in accounts)
    batch_log = cl_db.execute(select(AuditLog).where(AuditLog.entity_id == batch_id)).scalars().all()
    assert {"upload.previewed", "upload.attested", "upload.imported"} <= {e.action for e in batch_log}

    # the same file again: every imported row is now a duplicate of an open
    # account; only the row the client skipped (row 2) is new
    again = cl_api.post("/collections/client/uploads", files={"file": ("balances.csv", _fifty_row_csv(), "text/csv")}, headers=h).json()
    assert again["batch"]["duplicate_count"] == 44 and again["batch"]["valid_count"] == 1


def test_upload_row_cap_and_extra_columns(cl_api, cl_db):
    client, user = make_client(cl_db, upload_row_cap=10)
    h = client_headers(cl_db, user)
    r = cl_api.post("/collections/client/uploads", files={"file": ("b.csv", _fifty_row_csv(), "text/csv")}, headers=h)
    assert r.status_code == 422 and "up to 10 rows" in r.json()["detail"]
    bad = b"debtor_name,phone,description,amount_owed,employer\nA,08031234567,fees,100,Acme\n"
    r = cl_api.post("/collections/client/uploads", files={"file": ("b.csv", bad, "text/csv")}, headers=h)
    assert r.status_code == 422 and "employer" in r.json()["detail"]


# ---------------------------------------------------------------- debtor page


def test_debtor_page_plan_proof_dispute_and_opt_out(cl_api, cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client, balance=90_000_00)
    other = make_account(cl_db, client, phone="+2348030001111", name="Someone Else")
    url = f"/collections/d/{account.access_token}"

    page = cl_api.get(url).json()
    assert page["client"]["account_number"] == "0123456789"
    assert page["balance"] == 90_000_00 and page["reference_code"] == account.reference_code
    assert "Someone Else" not in str(page) and other.reference_code not in str(page)
    assert page["plan_options"]

    r = cl_api.post(url + "/plan", json={"instalments": 3})
    assert r.status_code == 200 and r.json()["plan"]["instalment_count"] == 3
    assert account.status == AccountStatus.ON_PLAN

    r = cl_api.post(url + "/proof", data={"amount": "30,000", "paid_on": TODAY().isoformat(), "sender_name": "Ada O"},
                    files={"file": ("receipt.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    assert account.status == AccountStatus.PROOF_SUBMITTED
    payment = cl_db.execute(select(DebtorPayment).where(DebtorPayment.account_id == account.id)).scalar_one()
    assert payment.status == "Proof submitted" and payment.amount_claimed == 30_000_00

    # same receipt again is flagged as a likely duplicate
    cl_api.post(url + "/proof", data={"amount": "30000", "paid_on": TODAY().isoformat()}, files={"file": ("r.png", PNG, "image/png")})
    dupes = cl_db.execute(select(DebtorPayment).where(DebtorPayment.account_id == account.id, DebtorPayment.possible_duplicate.is_(True))).scalars().all()
    assert len(dupes) == 1

    # wrong file type and oversize are refused
    assert cl_api.post(url + "/proof", data={"amount": "1", "paid_on": TODAY().isoformat()},
                       files={"file": ("x.png", b"GIF89a....", "image/png")}).status_code == 422
    assert cl_api.post(url + "/proof", data={"amount": "1", "paid_on": TODAY().isoformat()},
                       files={"file": ("x.pdf", b"%PDF-" + b"0" * (5 * 1024 * 1024), "application/pdf")}).status_code == 413

    r = cl_api.post(url + "/dispute", data={"type": "Wrong amount", "details": "I paid part at the bursary"})
    assert r.status_code == 200 and r.json()["open_dispute"]["type"] == "Wrong amount"
    assert account.status == AccountStatus.DISPUTED

    assert cl_api.post(url + "/opt-out").json()["opted_out"] is True
    assert cl_api.get(url).status_code == 200  # still reachable to pay


@pytest.mark.parametrize("token", ["x" * 43, "short", "../../etc/passwd" + "a" * 30])
def test_invalid_token_is_generic_404(cl_api, token):
    r = cl_api.get(f"/collections/d/{token}")
    assert r.status_code == 404 and r.json()["detail"].lower().startswith("not found")


def test_token_expires_30_days_after_close_and_reissue_kills_old_link(cl_api, cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    old = account.access_token
    service.reissue_token(cl_db, account, audit.SYSTEM)
    assert cl_api.get(f"/collections/d/{old}").status_code == 404
    assert cl_api.get(f"/collections/d/{account.access_token}").status_code == 200
    account.status, account.closed_at = AccountStatus.PAID, utcnow() - timedelta(days=31)
    cl_db.flush()
    assert cl_api.get(f"/collections/d/{account.access_token}").status_code == 404


# ---------------------------------------------------------------- confirmation + fees


def _proof(cl_db, account, amount, paid_on):
    return service.submit_proof(cl_db, account, file_bytes=PNG + amount.to_bytes(8, "big"), amount=amount,
                                paid_on=paid_on, sender_name=None, actor=audit.SYSTEM, today=paid_on)


def test_confirm_updates_balance_marks_paid_and_fee_only_when_rules_met(cl_api, cl_db, tmp_path):
    client, user = make_client(cl_db)
    h = client_headers(cl_db, user)
    day = TODAY()
    contacted = make_account(cl_db, client, balance=100_000_00, phone="+2348031111111")
    add_sent(cl_db, contacted, wat(day - timedelta(days=5), 10))
    never = make_account(cl_db, client, balance=50_000_00, phone="+2348032222222")
    stale = make_account(cl_db, client, balance=50_000_00, phone="+2348033333333")
    add_sent(cl_db, stale, wat(day - timedelta(days=90), 10))

    p1 = _proof(cl_db, contacted, 40_000_00, day)
    r = cl_api.post(f"/collections/client/payments/{p1.id}/confirm", json={"amount_received": "39,500"}, headers=h)
    assert r.status_code == 200, r.text
    cl_db.refresh(contacted)
    assert contacted.balance == 100_000_00 - 39_500_00 and contacted.status == AccountStatus.ACTIVE
    assert p1.fee_eligible and p1.fee_amount == 7_900_00

    p2 = _proof(cl_db, contacted, 60_500_00, day)
    cl_api.post(f"/collections/client/payments/{p2.id}/confirm", json={}, headers=h)
    cl_db.refresh(contacted)
    assert contacted.balance == 0 and contacted.status == AccountStatus.PAID and contacted.closed_at

    p3 = _proof(cl_db, never, 50_000_00, day)
    cl_api.post(f"/collections/client/payments/{p3.id}/confirm", json={}, headers=h)
    assert not p3.fee_eligible and p3.fee_amount == 0  # no outreach before payment

    p4 = _proof(cl_db, stale, 10_000_00, day)
    cl_api.post(f"/collections/client/payments/{p4.id}/confirm", json={}, headers=h)
    assert not p4.fee_eligible  # last message outside the 60-day window

    events = cl_db.execute(select(AuditLog.action).where(AuditLog.entity_id == p1.id)).scalars().all()
    assert "payment.confirmed" in events


def test_message_after_payment_does_not_count(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    day = TODAY()
    add_sent(cl_db, account, wat(day, 10))
    p = _proof(cl_db, account, 1_000_00, day - timedelta(days=1))
    service.confirm_payment(cl_db, p, None, audit.SYSTEM)
    assert not p.fee_eligible


def test_reject_returns_account_to_active_and_notifies_ops(cl_api, cl_db, monkeypatch):
    monkeypatch.setenv("COLLECTIONS_OPS_ALERT_EMAIL", "ops@waterline.example")
    from app.config import get_settings

    get_settings.cache_clear()
    client, user = make_client(cl_db)
    h = client_headers(cl_db, user)
    account = make_account(cl_db, client)
    p = _proof(cl_db, account, 5_000_00, TODAY())
    assert account.status == AccountStatus.PROOF_SUBMITTED
    r = cl_api.post(f"/collections/client/payments/{p.id}/reject", json={"reason": "Not received"}, headers=h)
    assert r.status_code == 200
    cl_db.refresh(account)
    assert account.status == AccountStatus.ACTIVE and account.balance == 150_000_00
    alert = cl_db.execute(select(EmailMessage).where(EmailMessage.kind == "ops_proof_rejected")).scalar_one()
    assert alert.to_address == "ops@waterline.example"
    assert cl_db.execute(select(AuditLog).where(AuditLog.action == "payment.rejected", AuditLog.entity_id == p.id)).first()


def test_client_recorded_cash_payment_counts_within_attribution(cl_api, cl_db):
    client, user = make_client(cl_db)
    h = client_headers(cl_db, user)
    account = make_account(cl_db, client, balance=20_000_00)
    add_sent(cl_db, account, wat(TODAY() - timedelta(days=2), 10))
    r = cl_api.post(f"/collections/client/accounts/{account.id}/payments",
                    json={"amount": "20000", "paid_on": TODAY().isoformat(), "note": "cash at bursary"}, headers=h)
    assert r.status_code == 201 and r.json()["fee_eligible"] is True and r.json()["status"] == "Confirmed"
    cl_db.refresh(account)
    assert account.status == AccountStatus.PAID


def test_withdrawn_dispute_closes_account_and_removes_fee(cl_api, cl_db):
    client, user = make_client(cl_db)
    h = client_headers(cl_db, user)
    account = make_account(cl_db, client)
    add_sent(cl_db, account, wat(TODAY() - timedelta(days=1), 10))
    p = _proof(cl_db, account, 1_000_00, TODAY())
    service.confirm_payment(cl_db, p, None, audit.SYSTEM)
    assert p.fee_eligible
    d = service.raise_dispute(cl_db, account, type="Not my debt", details=None, evidence_bytes=None, raised_by="Debtor", actor=audit.SYSTEM)
    r = cl_api.post(f"/collections/client/disputes/{d.id}/resolve", json={"outcome": "Withdraw"}, headers=h)
    assert r.status_code == 200
    cl_db.refresh(account)
    cl_db.refresh(p)
    assert account.status == AccountStatus.WITHDRAWN and not p.fee_eligible and p.fee_amount == 0


def test_discount_written_off_when_paid_in_full_by_deadline(cl_db):
    from tests.collections_factory import make_batch

    client, _ = make_client(cl_db)
    deadline = TODAY() + timedelta(days=10)
    batch = make_batch(cl_db, client, rules={"discount_percent": 10, "discount_deadline": deadline.isoformat()})
    account = make_account(cl_db, client, batch, balance=100_000_00)
    p = _proof(cl_db, account, 90_000_00, TODAY())
    service.confirm_payment(cl_db, p, None, audit.SYSTEM)
    assert account.balance == 0 and account.status == AccountStatus.PAID


# ---------------------------------------------------------------- invoices


def test_monthly_invoice_draft_lines_and_totals(cl_api, cl_db):
    _, ops_h = make_ops(cl_db)
    client, user = make_client(cl_db, fee_percent=20)
    other_client, _ = make_client(cl_db, name="Other School", fee_percent=15)
    month = date(2026, 9, 1)
    rows = []
    for i, (amount, eligible) in enumerate([(100_000_00, True), (33_333_33, True), (50_000_00, False)]):
        account = make_account(cl_db, client, phone=f"+234803100000{i}")
        p = DebtorPayment(account_id=account.id, client_id=client.id, amount_claimed=amount, amount_confirmed=amount,
                    paid_on=date(2026, 9, 10 + i), source="Debtor proof", status="Confirmed",
                    confirmed_at=wat(date(2026, 9, 12 + i), 12), fee_eligible=eligible,
                    fee_amount=round(amount * 0.2) if eligible else 0)
        cl_db.add(p)
        rows.append(p)
    # confirmed in October: belongs to next month's invoice
    late_acc = make_account(cl_db, client, phone="+2348031000009")
    cl_db.add(DebtorPayment(account_id=late_acc.id, client_id=client.id, amount_claimed=1_000_00, amount_confirmed=1_000_00,
                      paid_on=date(2026, 9, 30), source="Debtor proof", status="Confirmed",
                      confirmed_at=wat(date(2026, 10, 1), 0, 30), fee_eligible=True, fee_amount=200_00))
    oacc = make_account(cl_db, other_client, phone="+2348031000010")
    cl_db.add(DebtorPayment(account_id=oacc.id, client_id=other_client.id, amount_claimed=10_000_00, amount_confirmed=10_000_00,
                      paid_on=date(2026, 9, 5), source="Debtor proof", status="Confirmed",
                      confirmed_at=wat(date(2026, 9, 6), 12), fee_eligible=True, fee_amount=1_500_00))
    cl_db.flush()

    drafted = billing.draft_invoices(cl_db, month)
    inv = next(i for i in drafted if i.client_id == client.id)
    assert inv.period_start == date(2026, 9, 1) and inv.period_end == date(2026, 9, 30)
    assert len(inv.lines) == 2
    assert inv.total_confirmed == 133_333_33
    assert inv.fee_amount == 20_000_00 + 6_666_67
    assert {l.reference_code for l in inv.lines} == {rows[0].account_id and cl_db.get(DebtorAccount, rows[0].account_id).reference_code,
                                                     cl_db.get(DebtorAccount, rows[1].account_id).reference_code}
    assert billing.draft_invoices(cl_db, month) == []  # idempotent: nothing new to add

    # client sees nothing until issued; then sees it with lines
    h = client_headers(cl_db, user)
    assert cl_api.get("/collections/client/invoices", headers=h).json()["invoices"] == []
    assert cl_api.post(f"/collections/ops/invoices/{inv.id}/issue", headers=ops_h).status_code == 200
    detail = cl_api.get(f"/collections/client/invoices/{inv.id}", headers=h).json()["invoice"]
    assert detail["status"] == "Issued" and len(detail["lines"]) == 2 and detail["fee_amount"] == 26_666_67
    assert cl_api.post(f"/collections/ops/invoices/{inv.id}/paid", headers=ops_h).json()["status"] == "Paid"


def test_overdue_invoice_flags_client(cl_db):
    client, _ = make_client(cl_db)
    inv = Invoice(number="WLC-TEST", client_id=client.id, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
                  fee_percent=20, status="Issued", due_at=date(2026, 2, 14))
    cl_db.add(inv)
    cl_db.flush()
    billing.check_overdue_invoices(cl_db, date(2026, 2, 20))
    assert inv.status == "Overdue" and client.flagged_at is None
    billing.check_overdue_invoices(cl_db, date(2026, 3, 20))
    assert client.flagged_at is not None and "overdue" in client.flagged_reason


# ---------------------------------------------------------------- tenant isolation


def test_client_a_cannot_read_or_change_client_b_data(cl_api, cl_db):
    a, a_user = make_client(cl_db, name="School A")
    b, b_user = make_client(cl_db, name="School B")
    ha = client_headers(cl_db, a_user)
    b_account = make_account(cl_db, b)
    b_payment = _proof(cl_db, b_account, 1_000_00, TODAY())
    b_dispute_acc = make_account(cl_db, b, phone="+2348030002222")
    b_dispute = service.raise_dispute(cl_db, b_dispute_acc, type="Other", details=None, evidence_bytes=None, raised_by="Debtor", actor=audit.SYSTEM)
    b_batch = b_account.batch_id
    b_inv = Invoice(number="WLC-B", client_id=b.id, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
                    fee_percent=20, status="Issued")
    cl_db.add(b_inv)
    cl_db.flush()

    reads = [
        f"/collections/client/accounts/{b_account.id}",
        f"/collections/client/uploads/{b_batch}",
        f"/collections/client/invoices/{b_inv.id}",
    ]
    for url in reads:
        assert cl_api.get(url, headers=ha).status_code == 404, url
    writes = [
        (f"/collections/client/payments/{b_payment.id}/confirm", {}),
        (f"/collections/client/payments/{b_payment.id}/reject", {"reason": "Not received"}),
        (f"/collections/client/disputes/{b_dispute.id}/resolve", {"outcome": "Withdraw"}),
        (f"/collections/client/accounts/{b_account.id}/pause", None),
        (f"/collections/client/accounts/{b_account.id}/withdraw", None),
        (f"/collections/client/accounts/{b_account.id}/payments", {"amount": "1", "paid_on": TODAY().isoformat()}),
        (f"/collections/client/uploads/{b_batch}/discard", None),
    ]
    for url, body in writes:
        assert cl_api.post(url, json=body, headers=ha).status_code == 404, url
    assert b_payment.status == "Proof submitted" and b_dispute.status == "Open"

    # lists only ever contain A's own rows
    listing = cl_api.get("/collections/client/accounts", headers=ha).json()
    assert listing["total"] == 0
    assert cl_api.get("/collections/client/confirmations", headers=ha).json() == []
    assert cl_api.get("/collections/client/disputes", headers=ha).json() == []
    assert b_account.reference_code not in cl_api.get("/collections/client/export.csv", headers=ha).text


def test_role_boundaries(cl_api, cl_db):
    client, user = make_client(cl_db)
    h = client_headers(cl_db, user)
    _, ops_h = make_ops(cl_db, role="Ops")
    assert cl_api.get("/collections/ops/clients", headers=h).status_code == 401  # client session isn't an ops session
    assert cl_api.get("/collections/client/me", headers=ops_h).status_code == 401
    assert cl_api.get("/collections/client/me").status_code == 401
    # ops (non-admin) can't edit templates or config
    assert cl_api.post("/collections/ops/templates/INTRO", json={"body": "x" * 30}, headers=ops_h).status_code == 403
    assert cl_api.put("/collections/ops/config/default_fee_percent", json={"value": 10}, headers=ops_h).status_code == 403
    # there is no ops endpoint to confirm a payment
    assert not any("confirm" in getattr(r, "path", "") for r in __import__("app.collections.api.ops", fromlist=["router"]).router.routes)
    # teammates (Members) can't change bank details
    member = ClientUser(client_id=client.id, name="M", email="m@x.example", email_index="m-idx", role="Member",
                        email_verified_at=utcnow())
    cl_db.add(member)
    cl_db.flush()
    assert cl_api.put("/collections/client/settings", json={"account_number": "9999999999"},
                      headers=client_headers(cl_db, member)).status_code == 403


def test_admin_template_edit_is_versioned_and_checked(cl_api, cl_db):
    _, admin_h = make_ops(cl_db, role="Admin")
    bad = "Hello {{debtor_first_name}}, pay or we go to court. {{client_name}} via Waterline {{link}} Reply STOP to stop messages."
    assert cl_api.post("/collections/ops/templates/REMINDER", json={"body": bad}, headers=admin_h).status_code == 409
    good = "Hello {{debtor_first_name}}, a gentle reminder from {{client_name}} via Waterline: {{balance}}. {{link}} Reply STOP to stop messages."
    r = cl_api.post("/collections/ops/templates/REMINDER", json={"body": good}, headers=admin_h)
    assert r.status_code == 200 and r.json()["version"] == 2


# ---------------------------------------------------------------- audit


def test_audit_covers_required_actions_and_exports_csv(cl_api, cl_db):
    _, ops_h = make_ops(cl_db)
    client, user = make_client(cl_db)
    h = client_headers(cl_db, user)
    account = make_account(cl_db, client)
    day = a_weekday()
    outreach.build_queue(cl_db, wat(day, 8))
    item = cl_db.execute(select(OutreachItem).where(OutreachItem.account_id == account.id)).scalar_one()
    outreach.mark_sent(cl_db, item, "WhatsApp", audit.SYSTEM, now=wat(day, 9))
    p = _proof(cl_db, account, 1_000_00, TODAY())
    cl_api.post(f"/collections/client/payments/{p.id}/reject", json={"reason": "Not received"}, headers=h)
    p2 = _proof(cl_db, account, 1_000_00, TODAY() - timedelta(days=1))
    cl_api.post(f"/collections/client/payments/{p2.id}/confirm", json={}, headers=h)
    d = service.raise_dispute(cl_db, account, type="Other", details=None, evidence_bytes=None, raised_by="Debtor", actor=audit.SYSTEM)
    cl_api.post(f"/collections/client/disputes/{d.id}/resolve", json={"outcome": "Uphold"}, headers=h)
    pending, _ = make_client(cl_db, name="Pending Co", status=ClientStatus.PENDING)
    cl_api.post(f"/collections/ops/clients/{pending.id}/status", json={"status": "Rejected", "reason": "no"}, headers=ops_h)

    r = cl_api.get("/collections/ops/audit.csv", headers=ops_h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    actions = {row["action"] for row in csv.DictReader(io.StringIO(r.text))}
    for required in ["outreach.sent", "payment.proof_submitted", "payment.rejected", "payment.confirmed",
                     "dispute.opened", "dispute.resolved", "client.rejected", "queue.built"]:
        assert required in actions, required
