"""Waterline's own sales outreach to prospects: drafts, sending rules, and the
seeded "First 20 Prospects" list."""

from datetime import timedelta
from urllib.parse import unquote

from sqlalchemy import select

from app.collections import emailer, prospects
from app.collections.models import EmailMessage, Prospect, ProspectList, ProspectTouch
from app.collections.timeutil import utcnow
from tests.collections_factory import make_ops
from tests.conftest import requires_db

pytestmark = requires_db


def _prospect(db, **kw) -> Prospect:
    data = {"segment": "school", "org_name": "Test Academy, Yaba", "email": "info@testacademy.example",
            "phone": "+2348031112222", "contact_name": "Mrs Ada Obi", "contact_role": "Head of School"} | kw
    p = Prospect(**data)
    db.add(p)
    db.flush()
    return p


def test_first_20_prospects_are_seeded(cl_db):
    plist = cl_db.execute(select(ProspectList).where(ProspectList.name == "Waterline Collections — First 20 Prospects")).scalar_one()
    rows = cl_db.execute(select(Prospect).where(Prospect.list_id == plist.id)).scalars().all()
    assert len(rows) == 20
    assert sum(p.segment == "school" for p in rows) == 14 and sum(p.segment == "estate" for p in rows) == 5
    assert not any(p.contact_confirmed for p in rows)  # names may be out of date until ops confirm
    corona = next(p for p in rows if p.org_name == "Corona School Lekki")
    assert corona.email == "lks@coronaschools.org" and corona.phone == "+2347072713342" and corona.fit == "High"


def test_greeting_uses_name_only_once_confirmed(cl_db):
    p = _prospect(cl_db)
    assert prospects.greeting(p) == "Dear Head of School"
    p.contact_confirmed = True
    assert prospects.greeting(p) == "Dear Mrs Ada Obi"
    estate = _prospect(cl_db, segment="estate", org_name="Palm Court FM", contact_name=None)
    assert prospects.greeting(estate) == "Dear Facility Manager"


def test_ops_emails_a_prospect_with_footer_and_reply_to(cl_api, cl_db, monkeypatch):
    sent = []
    monkeypatch.setattr(emailer, "get_settings", lambda: type("S", (), {"smtp_host": "smtp.test", "collections_site_base_url": "https://waterline.ng"})())
    monkeypatch.setattr(emailer, "_deliver", lambda to, subject, body, reply_to=None: sent.append((to, subject, body, reply_to)))
    _, h = make_ops(cl_db, role="Ops")
    p = _prospect(cl_db)
    d = cl_api.get(f"/collections/ops/prospects/{p.id}", headers=h).json()
    intro = d["drafts"]["intro"]
    assert intro["email"].startswith("Dear Head of School,") and "Test Academy, Yaba" in intro["email"]
    assert "unpaid school fees" in intro["subject"] and "/collections/schools/" in intro["email"]
    r = cl_api.post(f"/collections/ops/prospects/{p.id}/email", json={"subject": intro["subject"], "body": intro["email"],
                                                                      "template_key": "intro"}, headers=h)
    assert r.status_code == 200, r.text
    to, subject, body, reply_to = sent[0]
    assert to == "info@testacademy.example" and reply_to == "collections@waterline.ng"
    assert "3D7 Technologies Limited (RC 6979200)" in body and "no thanks" in body
    cl_db.refresh(p)
    assert p.status == "Contacted" and p.last_contacted_at is not None
    assert cl_db.execute(select(EmailMessage).where(EmailMessage.kind == "prospect", EmailMessage.status == "Sent")).scalars().first()
    # one message a day per organisation, across channels
    r = cl_api.post(f"/collections/ops/prospects/{p.id}/whatsapp-link", json={"body": "hello"}, headers=h)
    assert r.status_code == 409 and "today" in r.json()["detail"]


def test_email_without_smtp_is_not_recorded_as_sent(cl_api, cl_db):
    _, h = make_ops(cl_db, role="Ops")
    p = _prospect(cl_db)
    r = cl_api.post(f"/collections/ops/prospects/{p.id}/email", json={"subject": "Hi", "body": "Hello"}, headers=h)
    assert r.status_code == 409 and "SMTP" in r.json()["detail"]
    cl_db.refresh(p)
    assert p.status == "New"
    touch = cl_db.execute(select(ProspectTouch).where(ProspectTouch.prospect_id == p.id)).scalar_one()
    assert touch.status == "Failed"


def test_whatsapp_link_then_mark_sent(cl_api, cl_db):
    _, h = make_ops(cl_db, role="Ops")
    p = _prospect(cl_db, email=None)
    r = cl_api.post(f"/collections/ops/prospects/{p.id}/whatsapp-link", json={"body": "Good morning"}, headers=h)
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://wa.me/2348031112222?text=") and "RC 6979200" in unquote(url)
    assert cl_db.execute(select(ProspectTouch).where(ProspectTouch.prospect_id == p.id)).first() is None  # nothing until sent
    r = cl_api.post(f"/collections/ops/prospects/{p.id}/whatsapp-sent", json={"body": "Good morning", "template_key": "intro"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "Contacted"
    r = cl_api.post(f"/collections/ops/prospects/{p.id}/email", json={"subject": "Hi", "body": "Hi"}, headers=h)
    assert r.status_code == 409 and "no email" in r.json()["detail"]


def test_do_not_contact_and_three_unanswered_limit(cl_db):
    ops, _ = make_ops(cl_db)
    p = _prospect(cl_db)
    for days_ago in (9, 6, 3):  # three earlier messages, none answered
        cl_db.add(ProspectTouch(prospect_id=p.id, channel="Email", status="Sent", body="x",
                                created_at=utcnow() - timedelta(days=days_ago)))
    cl_db.flush()
    try:
        prospects.check_can_message(cl_db, p, "Email")
        raise AssertionError("expected a refusal")
    except prospects.ProspectRefused as exc:
        assert "no reply" in str(exc)
    prospects.log_touch(cl_db, p, channel="Reply", body="Send details please", ops_user_id=ops.id)
    assert p.status == "Replied" and prospects.unanswered_count(cl_db, p) == 0
    p.status = "Do not contact"
    try:
        prospects.check_can_message(cl_db, p, "WhatsApp")
        raise AssertionError("expected a refusal")
    except prospects.ProspectRefused as exc:
        assert "not to be contacted" in str(exc)


def test_create_edit_and_admin_only_delete(cl_api, cl_db):
    _, agent = make_ops(cl_db, role="Ops")
    _, admin = make_ops(cl_db, role="Admin")
    body = {"segment": "estate", "org_name": "Lekki Gardens FM", "phone": "0803 444 5555", "email": "INFO@LG.example",
            "fit": "High", "status": "New"}
    r = cl_api.post("/collections/ops/prospects", json=body, headers=agent)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["phone"] == "+2348034445555" and r.json()["email"] == "info@lg.example"
    r = cl_api.put(f"/collections/ops/prospects/{pid}", json=body | {"status": "Interested", "contact_name": "Mr Bayo",
                                                                     "contact_confirmed": True}, headers=agent)
    assert r.status_code == 200 and r.json()["status"] == "Interested"
    assert cl_api.put(f"/collections/ops/prospects/{pid}", json=body | {"status": "Maybe"}, headers=agent).status_code == 422
    assert cl_api.delete(f"/collections/ops/prospects/{pid}", headers=agent).status_code == 403
    assert cl_api.delete(f"/collections/ops/prospects/{pid}", headers=admin).status_code == 200
    assert cl_db.get(Prospect, pid) is None
