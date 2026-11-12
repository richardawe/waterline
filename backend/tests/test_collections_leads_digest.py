"""Daily leads digest: Admin-only recipient list, the internal template is
part of submit/sync, Ops can send today's New prospects to each recipient
(logged in the Outbox), and the template can't reach any other number."""

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.collections import leads_digest, whatsapp
from app.collections.models import Prospect, WaMessage, WaTemplate
from app.collections.timeutil import utcnow
from tests.collections_factory import make_ops
from tests.conftest import requires_db
from tests.test_collections_whatsapp import FakeGraph, _connect

pytestmark = requires_db

TEAM = ["+2348031110001", "+15550584319"]
NAME = "wl_internal_leads_digest_v1"


def _approve(db):
    db.add(WaTemplate(segment="internal", key="leads_digest", version=1, variant="", meta_name=NAME, status="APPROVED"))
    db.flush()


def _fresh(db):
    # Seeded prospects were created when migrations ran; push them into the past.
    db.execute(update(Prospect).values(created_at=utcnow() - timedelta(days=3)))
    db.flush()


def _prospect(db, name, status="New", **kw):
    p = Prospect(segment="school", org_name=name, status=status, **kw)
    db.add(p)
    db.flush()
    return p


def _recipients(cl_api, h, value):
    return cl_api.put("/collections/ops/config/leads_digest_recipients", headers=h, json={"value": value})


def test_recipient_list_is_admin_only_and_normalised(cl_api, cl_db):
    _, ops_h = make_ops(cl_db, role="Ops")
    _, admin_h = make_ops(cl_db, role="Admin")
    assert cl_api.get("/collections/ops/config", headers=ops_h).json()["leads_digest_recipients"] == []
    assert _recipients(cl_api, ops_h, ["08031110001"]).status_code == 403
    r = _recipients(cl_api, admin_h, ["0803 111 0001", "+2348031110001", "+1 555 058 4319"])
    assert r.status_code == 200 and r.json()["value"] == TEAM
    assert _recipients(cl_api, admin_h, ["not a number"]).status_code == 422
    assert _recipients(cl_api, admin_h, "08031110001").status_code == 422


def test_internal_template_is_submitted_and_listed(cl_api, cl_db, monkeypatch):
    fake = _connect(cl_db, monkeypatch)
    _, admin_h = make_ops(cl_db, role="Admin")
    r = cl_api.post("/collections/ops/channels/whatsapp/templates/submit", headers=admin_h)
    assert r.status_code == 200
    post = next(p for m, path, p in fake.calls if m == "POST" and p and p.get("name") == NAME)
    assert post["category"] == "UTILITY" and post["language"] == "en"
    body = post["components"][0]
    assert {x["param_name"] for x in body["example"]["body_text_named_params"]} == {"digest_date", "lead_count", "lead_list"}
    assert not body["text"].startswith("{{") and not body["text"].endswith("}}")
    row = next(t for t in r.json()["templates"] if t["name"] == NAME)
    assert row["segment"] == "internal" and row["status"] == "PENDING"
    FakeGraph.names = [NAME]
    r = cl_api.post("/collections/ops/channels/whatsapp/templates/sync", headers=admin_h)
    assert next(t for t in r.json()["templates"] if t["name"] == NAME)["status"] == "APPROVED"
    FakeGraph.names = []


def test_ops_sends_todays_new_prospects_once_per_recipient(cl_api, cl_db, monkeypatch):
    fake = _connect(cl_db, monkeypatch)
    _, ops_h = make_ops(cl_db, role="Ops")
    _, admin_h = make_ops(cl_db, role="Admin")
    _fresh(cl_db)
    url = "/collections/ops/leads-digest/send"

    assert cl_api.post(url, headers=ops_h, json={}).status_code == 409  # no recipients
    _recipients(cl_api, admin_h, TEAM)
    assert "approved" in cl_api.post(url, headers=ops_h, json={}).json()["detail"]
    _approve(cl_db)
    assert "No new prospects" in cl_api.post(url, headers=ops_h, json={}).json()["detail"]

    _prospect(cl_db, "Greenfield Academy", city="Lekki", phone="+2348031234567", fit="High",
              fit_note="900 pupils,\nfee arrears every term")
    _prospect(cl_db, "Palm Court Estate", city="Ikeja")
    _prospect(cl_db, "Already Contacted School", status="Contacted")
    old = _prospect(cl_db, "Yesterday School")
    old.created_at = utcnow() - timedelta(days=1, hours=1)
    cl_db.flush()

    pre = cl_api.get("/collections/ops/leads-digest", headers=ops_h).json()
    assert [p["org_name"] for p in pre["leads"]] == ["Greenfield Academy", "Palm Court Estate"]
    assert pre["recipients"] == 2 and pre["template_status"] == "APPROVED"

    r = cl_api.post(url, headers=ops_h, json={})
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == TEAM and r.json()["leads"] == 2
    sends = [p for m, path, p in fake.calls if path.endswith("/messages")]
    assert sorted(p["to"] for p in sends) == sorted(n.lstrip("+") for n in TEAM)
    params = {x["parameter_name"]: x["text"] for x in sends[0]["template"]["components"][0]["parameters"]}
    assert sends[0]["template"]["name"] == NAME
    assert params["lead_count"] == "2" and "\n" not in params["lead_list"]
    assert "Greenfield Academy, Lekki, +2348031234567. Why: 900 pupils, fee arrears every term" in params["lead_list"]
    assert "Palm Court Estate, Ikeja, no phone" in params["lead_list"]
    assert "Contacted" not in params["lead_list"] and "Yesterday" not in params["lead_list"]

    out = cl_api.get("/collections/ops/outbox?channel=WhatsApp", headers=ops_h).json()
    assert sum(1 for o in out if o["what"] == "Daily leads digest") == 2

    again = cl_api.post(url, headers=ops_h, json={}).json()
    assert again["sent"] == [] and again["skipped"] == TEAM
    assert cl_api.post(url, headers=ops_h, json={"resend": True}).json()["sent"] == TEAM


def test_digest_template_never_goes_outside_the_recipient_list(cl_db, monkeypatch):
    fake = _connect(cl_db, monkeypatch)
    _approve(cl_db)
    tpl = whatsapp.approved_template(cl_db, "internal", "leads_digest", 1, "")
    with pytest.raises(whatsapp.WhatsAppError):
        whatsapp.send_template(cl_db, "+2348039999999", tpl, {"digest_date": "x", "lead_count": "1", "lead_list": "y"},
                               body="x")
    assert not [c for c in fake.calls if c[1].endswith("/messages")]
    assert cl_db.query(WaMessage).filter(WaMessage.template_name == NAME).count() == 0


def test_long_lists_are_cut_short_with_a_count():
    leads = [Prospect(segment="school", org_name=f"School number {i}", city="Lagos", phone="+2348031234567",
                      fit_note="A long reason why this school is a good fit for us " * 2) for i in range(30)]
    values, body = leads_digest.compose(leads, utcnow().date())
    assert len(values["lead_list"]) <= leads_digest.MAX_LIST_CHARS
    assert values["lead_list"].endswith("more in the portal.") and values["lead_count"] == "30"
    assert len(body) < 1024
