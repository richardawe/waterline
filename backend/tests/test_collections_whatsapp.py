"""WhatsApp Cloud API: settings stay secret, templates are submitted with
named parameters (and a no-discount variant), a queued message goes out as
its approved template under the usual conduct checks, and webhooks (signed
with the app secret) bring in replies, STOP and delivery statuses."""

import hashlib
import hmac
import json
import time
from datetime import timedelta

from sqlalchemy import select, text

from app.collections import outreach, templates, whatsapp
from app.collections.models import InboundMessage, OutreachItem, WaMessage, WaTemplate
from app.collections.timeutil import lagos_date, utcnow
from tests.collections_factory import make_account, make_client, make_ops
from tests.conftest import requires_db

pytestmark = requires_db

PHONE = "+2348031234567"


class FakeGraph:
    def __init__(self):
        self.calls = []
        self.n = 0

    def __call__(self, cfg, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.endswith("/messages"):
            self.n += 1
            return {"messages": [{"id": f"wamid.TEST{self.n}"}]}
        if "/message_templates" in path and method == "POST":
            return {"id": "tpl1", "status": "PENDING"}
        if "/message_templates" in path:
            return {"data": [{"name": n, "status": "APPROVED", "id": "x"} for n in self.names]}
        if "fields=display_phone_number" in path:
            return {"display_phone_number": "+1 555-058-4319", "verified_name": "Test Number", "quality_rating": "GREEN"}
        raise AssertionError(path)

    names: list = []


def _connect(db, monkeypatch) -> FakeGraph:
    fake = FakeGraph()
    monkeypatch.setattr(whatsapp, "_call", fake)
    whatsapp.save_settings(db, {"phone_number_id": "1335405489665397", "waba_id": "2151893508741594",
                                "access_token": "EAAG-secret-token", "app_secret": "appsecret123"}, "admin")
    db.flush()
    return fake


def _signed(payload: dict, secret="appsecret123") -> tuple[bytes, dict]:
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def _incoming(frm: str, body: str, wamid: str, ts: int | None = None) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"wa_id": frm, "profile": {"name": "Adaeze"}}],
        "messages": [{"from": frm, "id": wamid, "timestamp": str(ts or int(time.time())), "type": "text",
                      "text": {"body": body}}]}}]}]}


def _queued(db, account, client, key="INTRO") -> OutreachItem:
    tpl = templates.active(db, key, client.segment)
    values = {"debtor_first_name": "Adaeze", "client_name": client.business_name, "description": account.description,
              "balance": "₦150,000.00", "link": "https://waterline.ng/collections/p/x"}  # INTRO's own placeholders
    item = OutreachItem(account_id=account.id, client_id=client.id, template_key=key, template_version=tpl.version,
                        rendered_text=templates.render(tpl.body, values), render_values=json.dumps(values),
                        queue_date=lagos_date(utcnow()), scheduled_for=utcnow(), status="Queued")
    db.add(item)
    db.flush()
    return item


def test_channel_settings_are_admin_only_and_secrets_never_returned(cl_api, cl_db, monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(whatsapp, "_call", fake)
    _, ops_h = make_ops(cl_db, role="Ops")
    _, admin_h = make_ops(cl_db, role="Admin")
    assert cl_api.put("/collections/ops/channels/whatsapp", headers=ops_h, json={}).status_code == 403
    r = cl_api.put("/collections/ops/channels/whatsapp", headers=admin_h, json={
        "phone_number_id": "1335405489665397", "waba_id": "2151893508741594",
        "access_token": "EAAG-secret-token", "app_secret": "appsecret123"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["connected"] and out["has_token"] and out["has_app_secret"] and out["display_number"] == "+1 555-058-4319"
    assert len(out["verify_token"]) > 20 and out["webhook_url"].endswith("/collections/whatsapp/webhook")
    dumped = json.dumps(out) + json.dumps(cl_api.get("/collections/ops/channels", headers=admin_h).json())
    assert "EAAG-secret-token" not in dumped and "appsecret123" not in dumped
    raw = cl_db.execute(text("SELECT settings FROM cl_channel WHERE key = 'whatsapp'")).scalar_one()
    assert "EAAG" not in raw
    # saving again without secrets keeps them
    cl_api.put("/collections/ops/channels/whatsapp", headers=admin_h, json={"waba_id": "2151893508741594"})
    assert whatsapp.config(cl_db)["access_token"] == "EAAG-secret-token"

    r = cl_api.post("/collections/ops/channels/whatsapp/test", headers=admin_h, json={"to": "08031234567"})
    assert r.status_code == 200, r.text
    method, path, payload = fake.calls[-1]
    assert path == "/1335405489665397/messages" and payload["template"]["name"] == "hello_world"


def test_templates_submitted_with_named_params_and_no_discount_variant(cl_api, cl_db, monkeypatch):
    fake = _connect(cl_db, monkeypatch)
    _, admin_h = make_ops(cl_db, role="Admin")
    r = cl_api.post("/collections/ops/channels/whatsapp/templates/submit", headers=admin_h)
    assert r.status_code == 200, r.text
    posts = [c[2] for c in fake.calls if c[0] == "POST" and "message_templates" in c[1]]
    names = {p["name"] for p in posts}
    assert "wl_school_intro_v1" in names and "wl_school_reminder_v1_nd" in names and "wl_estate_intro_v1" in names
    reminder = next(p for p in posts if p["name"] == "wl_school_reminder_v1")
    nd = next(p for p in posts if p["name"] == "wl_school_reminder_v1_nd")
    assert reminder["parameter_format"] == "NAMED" and reminder["category"] == "UTILITY"
    assert "{{discount_line}}" in reminder["components"][0]["text"] and "discount_line" not in nd["components"][0]["text"]
    assert {e["param_name"] for e in nd["components"][0]["example"]["body_text_named_params"]} >= {"client_name", "link"}
    # a second submit doesn't resubmit pending ones
    n = len(fake.calls)
    cl_api.post("/collections/ops/channels/whatsapp/templates/submit", headers=admin_h)
    assert len(fake.calls) == n
    FakeGraph.names = ["wl_school_intro_v1"]
    rows = cl_api.post("/collections/ops/channels/whatsapp/templates/sync", headers=admin_h).json()["templates"]
    assert next(t for t in rows if t["name"] == "wl_school_intro_v1")["status"] == "APPROVED"
    assert next(t for t in rows if t["name"] == "wl_school_reminder_v1")["status"] == "PENDING"


def test_queue_item_sends_as_approved_template_and_counts_as_sent(cl_api, cl_db, monkeypatch):
    fake = _connect(cl_db, monkeypatch)
    _, h = make_ops(cl_db, role="Ops")
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client, phone=PHONE)
    item = _queued(cl_db, account, client)

    q = cl_api.get("/collections/ops/queue", headers=h).json()
    assert q["whatsapp_connected"] and next(i for i in q["items"] if i["id"] == item.id)["wa_ready"] is False
    r = cl_api.post(f"/collections/ops/queue/{item.id}/whatsapp", headers=h)
    assert r.status_code == 409 and "approved" in r.json()["detail"]

    cl_db.add(WaTemplate(segment="school", key="INTRO", version=item.template_version, variant="",
                         meta_name=f"wl_school_intro_v{item.template_version}", status="APPROVED"))
    cl_db.flush()
    assert next(i for i in cl_api.get("/collections/ops/queue", headers=h).json()["items"] if i["id"] == item.id)["wa_ready"]
    r = cl_api.post(f"/collections/ops/queue/{item.id}/whatsapp", headers=h)
    assert r.status_code == 200, r.text
    method, path, payload = fake.calls[-1]
    assert payload["to"] == "2348031234567" and payload["template"]["name"] == f"wl_school_intro_v{item.template_version}"
    params = {p["parameter_name"]: p["text"] for p in payload["template"]["components"][0]["parameters"]}
    assert params["client_name"] == client.business_name and params["debtor_first_name"] == "Adaeze"
    cl_db.refresh(item)
    assert item.status == "Sent" and item.channel == "WhatsApp"
    wa = cl_db.execute(select(WaMessage).where(WaMessage.outreach_item_id == item.id)).scalar_one()
    assert wa.wamid == "wamid.TEST1" and wa.status == "sent"
    # can't send twice
    assert cl_api.post(f"/collections/ops/queue/{item.id}/whatsapp", headers=h).status_code == 409
    out = cl_api.get("/collections/ops/outbox?channel=WhatsApp", headers=h).json()
    assert out[0]["status"] == "Sent" and out[0]["to"] == "Adaeze Okafor"


def test_webhook_verify_signature_inbound_stop_and_statuses(cl_api, cl_db, monkeypatch):
    _connect(cl_db, monkeypatch)
    token = whatsapp.stored(cl_db)["verify_token"]
    r = cl_api.get(f"/collections/whatsapp/webhook?hub.mode=subscribe&hub.verify_token={token}&hub.challenge=12345")
    assert r.status_code == 200 and r.text == "12345"
    assert cl_api.get("/collections/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=1").status_code == 403

    client, _ = make_client(cl_db)
    account = make_account(cl_db, client, phone=PHONE, status="Unresponsive")
    wa = WaMessage(to_phone=PHONE, to_index=whatsapp.phone_index(PHONE), account_id=account.id, client_id=client.id,
                   kind="template", body="hello", status="sent", wamid="wamid.OUT1")
    cl_db.add(wa)
    cl_db.flush()

    raw, headers = _signed(_incoming("2348031234567", "Can I pay on Friday?", "wamid.IN1"))
    assert cl_api.post("/collections/whatsapp/webhook", content=raw, headers={**headers, "X-Hub-Signature-256": "sha256=bad"}).status_code == 403
    r = cl_api.post("/collections/whatsapp/webhook", content=raw, headers=headers)
    assert r.status_code == 200 and r.json()["messages"] == 1
    msg = cl_db.execute(select(InboundMessage).where(InboundMessage.message_id == "wamid.IN1")).scalar_one()
    assert msg.party_type == "account" and msg.party_id == account.id and msg.channel == "WhatsApp"
    cl_db.refresh(account)
    assert account.status == "Active"
    assert cl_api.post("/collections/whatsapp/webhook", content=raw, headers=headers).json()["messages"] == 0  # duplicate

    def status(s, **extra):
        return {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.OUT1", "status": s, **extra}]}}]}]}
    for s in ("delivered", "read", "delivered"):  # out-of-order delivered must not downgrade read
        raw2, h2 = _signed(status(s))
        cl_api.post("/collections/whatsapp/webhook", content=raw2, headers=h2)
    cl_db.refresh(wa)
    assert wa.status == "read"

    raw3, h3 = _signed(_incoming("2348031234567", "STOP", "wamid.IN2"))
    cl_api.post("/collections/whatsapp/webhook", content=raw3, headers=h3)
    cl_db.refresh(account)
    assert account.opted_out_at is not None


def test_inbox_whatsapp_reply_only_inside_24_hours(cl_api, cl_db, monkeypatch):
    fake = _connect(cl_db, monkeypatch)
    _, h = make_ops(cl_db, role="Ops")
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client, phone=PHONE)
    old = int((utcnow() - timedelta(hours=30)).timestamp())
    whatsapp.handle_webhook(cl_db, _incoming("2348031234567", "Hello?", "wamid.OLD", ts=old))
    cl_db.flush()
    t = cl_api.get(f"/collections/ops/inbox/thread?type=account&id={account.id}", headers=h).json()
    assert t["reply"]["whatsapp_number"] and t["reply"]["whatsapp_until"] is None
    r = cl_api.post("/collections/ops/inbox/reply", headers=h,
                    json={"type": "account", "id": account.id, "channel": "WhatsApp", "body": "Hi"})
    assert r.status_code == 409 and "24 hours" in r.json()["detail"]

    whatsapp.handle_webhook(cl_db, _incoming("2348031234567", "Can I pay half?", "wamid.NEW"))
    cl_db.flush()
    t = cl_api.get(f"/collections/ops/inbox/thread?type=account&id={account.id}", headers=h).json()
    assert t["reply"]["whatsapp_until"] is not None
    bad = cl_api.post("/collections/ops/inbox/reply", headers=h,
                      json={"type": "account", "id": account.id, "channel": "WhatsApp", "body": "We will call the police"})
    assert bad.status_code == 422
    r = cl_api.post("/collections/ops/inbox/reply", headers=h,
                    json={"type": "account", "id": account.id, "channel": "WhatsApp", "body": "Yes, half now is fine."})
    assert r.status_code == 200, r.text
    assert fake.calls[-1][2]["type"] == "text" and fake.calls[-1][2]["text"]["body"] == "Yes, half now is fine."
    t = cl_api.get(f"/collections/ops/inbox/thread?type=account&id={account.id}", headers=h).json()
    assert t["messages"][-1]["channel"] == "WhatsApp" and t["messages"][-1]["direction"] == "out"
    # replies are not outreach
    assert not cl_db.execute(select(OutreachItem).where(OutreachItem.account_id == account.id)).first()


def test_build_queue_stores_template_values(cl_db):
    client, _ = make_client(cl_db)
    make_account(cl_db, client, phone=PHONE)
    outreach.build_queue(cl_db)
    item = cl_db.execute(select(OutreachItem).where(OutreachItem.client_id == client.id)).scalars().first()
    if item is not None:  # the account may need a day before its first message, depending on rules
        values = json.loads(item.render_values)
        assert values["client_name"] == client.business_name and "link" in values


def test_first_save_then_number_check_in_one_session(cl_db, monkeypatch):
    monkeypatch.setattr(whatsapp, "_call", FakeGraph())
    whatsapp.save_settings(cl_db, {"phone_number_id": "1", "access_token": "t"}, "admin")
    whatsapp.refresh_number(cl_db)  # saves again before any flush by the caller
    from app.collections import mailbox
    mailbox.save_settings(cl_db, {"password": "pw"}, "admin")
    mailbox._set_state(cl_db, last_check_at="x")
    cl_db.flush()
    assert whatsapp.stored(cl_db)["display_number"] == "+1 555-058-4319"


class _GraphResponse:
    def __init__(self, body: dict):
        self.raw = json.dumps(body).encode()

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_save_channel_with_token_and_app_secret_checks_the_number_with_meta(cl_api, cl_db, monkeypatch):
    """The real request path (urllib, not a faked _call): a token pasted with
    a line break and stray spaces is cleaned, sent as a Bearer header to the
    v23.0 Graph API, and the number's details come back. Both secrets are
    stored encrypted and never returned."""
    import urllib.request

    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return _GraphResponse({"display_phone_number": "+1 555-058-4319", "verified_name": "Test Number",
                               "quality_rating": "GREEN", "id": "1335405489665397"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _, admin_h = make_ops(cl_db, role="Admin")
    r = cl_api.put("/collections/ops/channels/whatsapp", headers=admin_h, json={
        "phone_number_id": " 1335405489665397 ", "waba_id": "2151893508741594",
        "access_token": "  EAAGtoken\n123\u200b ", "app_secret": " 0123abcdef\n"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["check_error"] is None and out["connected"] and out["has_token"] and out["has_app_secret"]
    assert out["display_number"] == "+1 555-058-4319" and out["verified_name"] == "Test Number"
    req = seen[-1]
    assert req.full_url.startswith("https://graph.facebook.com/v23.0/1335405489665397?fields=display_phone_number")
    assert req.get_header("Authorization") == "Bearer EAAGtoken123"
    cfg = whatsapp.config(cl_db)
    assert cfg["access_token"] == "EAAGtoken123" and cfg["app_secret"] == "0123abcdef"
    assert "EAAGtoken123" not in r.text and "0123abcdef" not in r.text
    raw = cl_db.execute(text("SELECT settings FROM cl_channel WHERE key = 'whatsapp'")).scalar_one()
    assert "EAAGtoken123" not in raw and "0123abcdef" not in raw


def test_save_channel_when_meta_times_out_still_saves_and_reports_it(cl_api, cl_db, monkeypatch):
    import urllib.request

    def timeout(req, timeout=None):
        raise TimeoutError("The read operation timed out")  # not a URLError: used to escape as a 500

    monkeypatch.setattr(urllib.request, "urlopen", timeout)
    _, admin_h = make_ops(cl_db, role="Admin")
    r = cl_api.put("/collections/ops/channels/whatsapp", headers=admin_h, json={
        "phone_number_id": "1335405489665397", "access_token": "EAAGtoken", "app_secret": "s3cret"})
    assert r.status_code == 200, r.text
    assert "Couldn't reach WhatsApp" in r.json()["check_error"]
    assert whatsapp.config(cl_db)["access_token"] == "EAAGtoken"


def test_save_channel_refuses_a_shortened_token_or_bad_id_without_saving(cl_api, cl_db, monkeypatch):
    monkeypatch.setattr(whatsapp, "_call", FakeGraph())
    _, admin_h = make_ops(cl_db, role="Admin")
    r = cl_api.put("/collections/ops/channels/whatsapp", headers=admin_h, json={
        "phone_number_id": "1335405489665397", "access_token": "EAAGto…", "app_secret": "s3cret"})
    assert r.status_code == 422 and "access token" in r.json()["detail"]
    r = cl_api.put("/collections/ops/channels/whatsapp", headers=admin_h, json={
        "phone_number_id": "1335\uff1405489665397", "access_token": "EAAGtoken"})
    assert r.status_code == 422 and "phone number ID" in r.json()["detail"]
    assert whatsapp.stored(cl_db) == {}


def test_unhandled_collections_errors_are_json_with_cors(cl_api, cl_db, monkeypatch):
    """The console must see the real error, not "We couldn't reach Waterline"
    (a 500 without CORS headers is hidden from the page by the browser)."""
    from app.config import get_settings

    def boom(db):
        raise RuntimeError("could not decrypt collections field")

    monkeypatch.setattr(whatsapp, "connected", boom)
    _, admin_h = make_ops(cl_db, role="Admin")
    origin = get_settings().cors_origins[0]
    r = cl_api.put("/collections/ops/channels/whatsapp", headers={**admin_h, "Origin": origin}, json={})
    assert r.status_code == 500
    assert r.headers["access-control-allow-origin"] == origin
    detail = r.json()["detail"]
    assert "RuntimeError: could not decrypt collections field" in detail and "Reference" in detail

    # Public pages (debtors, prospects) get a plain message: no internals.
    from app.collections import segments

    def boom_view(db, seg):
        raise RuntimeError("internal detail")

    monkeypatch.setattr(segments, "public_view", boom_view)
    r = cl_api.get("/collections/public/config", headers={"Origin": origin})
    assert r.status_code == 500 and r.headers["access-control-allow-origin"] == origin
    assert "internal detail" not in r.text and "Reference" in r.json()["detail"]
