"""Ops Inbox / Outbox / Channels: replies pulled from the collections
mailbox are matched to the right person, STOP opts a debtor out, ops can
reply from the portal (with the conduct wording check), and the mailbox
password is stored encrypted and never returned."""

import uuid
from datetime import timedelta
from email.message import EmailMessage as Mime

from sqlalchemy import select, text

from app.collections import emailer, mailbox
from app.collections.models import EmailMessage, InboundMessage, OutreachItem, Prospect, ProspectTouch
from app.collections.timeutil import lagos_date, utcnow
from tests.collections_factory import make_account, make_client, make_ops
from tests.conftest import requires_db

pytestmark = requires_db


def _raw(frm: str, body: str, subject: str = "Re: your balance", in_reply_to: str | None = None, **headers) -> bytes:
    m = Mime()
    m["From"] = frm
    m["To"] = "collections@waterline.ng"
    m["Subject"] = subject
    m["Message-ID"] = f"<{uuid.uuid4().hex}@sender.example>"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return bytes(m)


def _sent_to_debtor(db, account, to="parent@example.com") -> EmailMessage:
    row = emailer.send(db, to, "A message from Greenfield School via Waterline", "hello", kind="debtor_outreach",
                       client_id=account.client_id, account_id=account.id)
    db.flush()
    return row


def _capture(monkeypatch) -> list:
    sent = []
    monkeypatch.setattr(emailer.mailbox, "sending_config", lambda db: {"smtp_host": "smtp.test"})
    monkeypatch.setattr(emailer, "_deliver", lambda cfg, to, subject, body, **kw: sent.append((to, subject, body, kw)))
    return sent


def test_debtor_reply_is_threaded_and_counts_as_a_response(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client, email="parent@example.com", status="Unresponsive")
    out = _sent_to_debtor(cl_db, account)
    row = mailbox.process_raw(cl_db, _raw("Mrs Okafor <Parent@Example.com>", "Can I pay half now?", in_reply_to=out.message_id))
    assert row.party_type == "account" and row.party_id == account.id and row.client_id == client.id
    assert row.handled_at is None and account.status == "Active" and account.last_response_at is not None
    # the same message pulled twice is stored once
    assert mailbox.process_raw(cl_db, _raw("x@y.example", "dup")) is not None
    dup = _raw("parent@example.com", "again")
    assert mailbox.process_raw(cl_db, dup) is not None and mailbox.process_raw(cl_db, dup) is None


def test_stop_reply_opts_the_debtor_out_and_pulls_queued_messages(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client, email="parent@example.com")
    _sent_to_debtor(cl_db, account)
    today = lagos_date(utcnow())
    queued = OutreachItem(account_id=account.id, client_id=client.id, template_key="REMINDER", rendered_text="x",
                          queue_date=today + timedelta(days=1), scheduled_for=utcnow(), status="Queued")
    cl_db.add(queued)
    cl_db.flush()
    # no In-Reply-To: matched by the address we wrote to
    row = mailbox.process_raw(cl_db, _raw("parent@example.com", "STOP.\n\nOn Mon, Waterline wrote:\n> hello"))
    assert row.party_type == "account" and row.auto_action == "opted_out" and row.handled_at is not None
    assert account.opted_out_at is not None and queued.status == "Skipped"


def test_prospect_no_thanks_and_unmatched_and_auto_replies(cl_db):
    p = Prospect(segment="school", org_name="Test Academy", email="Info@TestAcademy.example")
    cl_db.add(p)
    cl_db.flush()
    row = mailbox.process_raw(cl_db, _raw("info@testacademy.example", "No thanks!"))
    assert row.party_type == "prospect" and p.status == "Do not contact"
    assert cl_db.execute(select(ProspectTouch).where(ProspectTouch.prospect_id == p.id, ProspectTouch.channel == "Reply")).first()
    stranger = mailbox.process_raw(cl_db, _raw("someone@nowhere.example", "Who is this?"))
    assert stranger.party_type is None and stranger.handled_at is None
    ooo = mailbox.process_raw(cl_db, _raw("someone@nowhere.example", "I'm away", Auto_Submitted="auto-replied"))
    assert ooo.auto_action == "auto_reply" and ooo.handled_at is not None
    # our own address (a copy of something we sent) is ignored
    assert mailbox.process_raw(cl_db, _raw("collections@waterline.ng", "x"), own_address="collections@waterline.ng") is None


def test_inbox_list_thread_reply_and_done(cl_api, cl_db, monkeypatch):
    sent = _capture(monkeypatch)
    _, h = make_ops(cl_db, role="Ops")
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client, email="parent@example.com")
    out = _sent_to_debtor(cl_db, account)
    inbound = mailbox.process_raw(cl_db, _raw("parent@example.com", "Can I pay half now?", in_reply_to=out.message_id))
    cl_db.flush()
    sent.clear()

    r = cl_api.get("/collections/ops/inbox", headers=h)
    assert r.status_code == 200, r.text
    conv = next(c for c in r.json()["conversations"] if c["id"] == account.id)
    assert conv["type"] == "account" and conv["name"] == "Adaeze Okafor" and conv["unhandled"] == 1
    assert r.json()["connected"] is False

    t = cl_api.get(f"/collections/ops/inbox/thread?type=account&id={account.id}", headers=h).json()
    assert [m["direction"] for m in t["messages"]] == ["out", "in"] and t["party"]["balance"] == account.balance
    assert "email" not in t["party"]

    bad = cl_api.post("/collections/ops/inbox/reply", headers=h,
                      json={"type": "account", "id": account.id, "body": "Pay now or we go to the police."})
    assert bad.status_code == 422 and not sent

    ok = cl_api.post("/collections/ops/inbox/reply", headers=h,
                     json={"type": "account", "id": account.id, "body": "Yes, you can split it. Use your link."})
    assert ok.status_code == 200, ok.text
    to, subject, body, kw = sent[0]
    assert to == "parent@example.com" and subject == "Re: your balance"
    assert kw["in_reply_to"] == inbound.message_id
    cl_db.refresh(inbound)
    assert inbound.handled_at is not None
    # replies are not outreach: no OutreachItem was created
    assert not cl_db.execute(select(OutreachItem).where(OutreachItem.account_id == account.id)).first()

    # opted-out debtors can't be written to
    account.opted_out_at = utcnow()
    cl_db.flush()
    r = cl_api.post("/collections/ops/inbox/reply", headers=h, json={"type": "account", "id": account.id, "body": "Hello"})
    assert r.status_code == 409

    stranger = mailbox.process_raw(cl_db, _raw("someone@nowhere.example", "Who is this?"))
    cl_db.flush()
    r = cl_api.get("/collections/ops/inbox?view=unmatched", headers=h).json()
    assert any(c["id"] == stranger.from_index for c in r["conversations"])
    assert cl_api.post("/collections/ops/inbox/done", headers=h, json={"type": "from", "id": stranger.from_index}).json()["marked"] == 1


def test_failed_reply_is_kept_in_the_outbox(cl_api, cl_db):
    _, h = make_ops(cl_db, role="Ops")
    stranger = mailbox.process_raw(cl_db, _raw("someone@nowhere.example", "Hello?"))
    cl_db.flush()
    r = cl_api.post("/collections/ops/inbox/reply", headers=h, json={"type": "from", "id": stranger.from_index, "body": "Hi"})
    assert r.status_code == 502 and "Channels" in r.json()["detail"]
    rows = cl_api.get("/collections/ops/outbox?failed=true", headers=h).json()
    assert any(x["kind"] == "inbox_reply" and x["status"] == "Logged" for x in rows)


def test_channels_admin_only_and_password_never_returned(cl_api, cl_db, monkeypatch):
    _, ops_h = make_ops(cl_db, role="Ops")
    _, admin_h = make_ops(cl_db, role="Admin")
    assert cl_api.get("/collections/ops/channels", headers=ops_h).status_code == 403
    assert cl_api.put("/collections/ops/channels/email", headers=ops_h, json={"password": "x"}).status_code == 403

    r = cl_api.put("/collections/ops/channels/email", headers=admin_h,
                   json={"address": "Collections@Waterline.ng", "password": "s3cret-mailbox-pw"})
    assert r.status_code == 200, r.text
    assert r.json()["has_password"] is True and "password" not in r.json() and r.json()["source"] == "portal"
    raw = cl_db.execute(text("SELECT settings FROM cl_channel WHERE key = 'email'")).scalar_one()
    assert "s3cret" not in raw
    cfg = mailbox.sending_config(cl_db)
    assert cfg["username"] == "collections@waterline.ng" and cfg["password"] == "s3cret-mailbox-pw"
    assert cfg["from"] == "Waterline Collections <collections@waterline.ng>" and cfg["smtp_port"] == 465
    # saving again without a password keeps the old one
    cl_api.put("/collections/ops/channels/email", headers=admin_h, json={"from_name": "Waterline"})
    assert mailbox.sending_config(cl_db)["password"] == "s3cret-mailbox-pw"
    audit_rows = cl_db.execute(text("SELECT after FROM cl_audit_log WHERE action = 'channel.email_saved'")).scalars().all()
    assert audit_rows and all("s3cret" not in (a or "") for a in audit_rows)

    sent = []
    monkeypatch.setattr(emailer, "_deliver", lambda cfg, to, subject, body, **kw: sent.append(to))
    monkeypatch.setattr(mailbox, "test_reading", lambda db: {"ok": True, "messages": 3})
    r = cl_api.post("/collections/ops/channels/email/test", headers=admin_h, json={"to": "me@example.com"}).json()
    assert r["sending"]["ok"] and r["reading"]["ok"] and sent == ["me@example.com"]


def test_reply_text_strips_quoted_original():
    body = "Thanks, I'll pay Friday.\n\nOn Tue, 28 Oct 2026, Waterline <collections@waterline.ng> wrote:\n> Hello"
    assert mailbox.reply_text(body) == "Thanks, I'll pay Friday."
    assert mailbox.is_stop("Stop!\n> old") and not mailbox.is_stop("Please don't stop helping me")


class _FakeImap:
    """Just enough of imaplib.IMAP4_SSL for check_mailbox."""

    def __init__(self, store: dict):
        self.store, self.searches, self.untagged_responses = store, [], {"UIDVALIDITY": [b"42"]}
        self.readonly = None

    def select(self, mailbox, readonly=False):
        self.readonly = readonly
        return "OK", [str(len(self.store)).encode()]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            self.searches.append(args[1])
            if args[1].startswith("UID "):
                lo = int(args[1].split()[1].split(":")[0])
                hits = [u for u in self.store if u >= lo] or [max(self.store)]  # IMAP returns the last message for n:*
            else:
                hits = list(self.store)
            return "OK", [" ".join(str(u) for u in hits).encode()]
        if cmd == "FETCH":
            return "OK", [(b"1 (BODY[] {n}", self.store[int(args[0])]), b")"]
        raise AssertionError(cmd)

    def logout(self):
        pass


def test_check_mailbox_reads_new_mail_only_and_never_marks_it_read(cl_db, monkeypatch):
    mailbox.save_settings(cl_db, {"password": "pw"}, "admin")
    store = {7: _raw("a@one.example", "first"), 8: _raw("b@two.example", "second")}
    fake = _FakeImap(store)
    monkeypatch.setattr(mailbox, "_imap", lambda cfg: fake)
    r = mailbox.check_mailbox(cl_db)
    assert r == {"ok": True, "checked": True, "new": 2} and fake.readonly is True
    assert fake.searches[0].startswith("SINCE ")  # first run only looks back 14 days
    assert mailbox.state(cl_db)["last_uid"] == 8
    r = mailbox.check_mailbox(cl_db)
    assert r["new"] == 0 and fake.searches[-1] == "UID 9:*"
    store[9] = _raw("c@three.example", "third")
    assert mailbox.check_mailbox(cl_db)["new"] == 1
    # throttled when asked to skip recent checks
    assert mailbox.check_mailbox(cl_db, if_older_than=120)["checked"] is False
    n = cl_db.execute(select(InboundMessage).where(InboundMessage.from_index.in_(
        [mailbox.crypto.blind_index(a) for a in ("a@one.example", "b@two.example", "c@three.example")]))).scalars().all()
    assert len(n) == 3


def test_check_mailbox_reports_a_bad_password(cl_db, monkeypatch):
    mailbox.save_settings(cl_db, {"password": "wrong"}, "admin")

    def boom(cfg):
        raise mailbox.imaplib.IMAP4.error("b'[AUTHENTICATIONFAILED] Authentication failed.'")
    monkeypatch.setattr(mailbox, "_imap", boom)
    r = mailbox.check_mailbox(cl_db)
    assert not r["ok"] and "rejected" in r["error"] and mailbox.state(cl_db)["last_error"] == r["error"]
