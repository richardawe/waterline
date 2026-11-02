"""Ops messaging: the Inbox (replies people send us), the Outbox (everything
we sent), Channels (Admin: connect the collections mailbox and WhatsApp), and
one-click WhatsApp sending from the outreach queue.

Replies typed in the Inbox are answers to someone who wrote to us, so they
are not outreach: they don't use up the daily/weekly outreach limits. They
still go through the conduct wording check for debtors, and nobody who has
opted out (or asked a prospect "no thanks") can be written to."""

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.collections import audit, emailer, mailbox, outreach, prospects, segments, whatsapp
from app.collections.auth import OpsContext, require_ops, require_ops_admin
from app.collections.models import (
    Client,
    DebtorAccount,
    EmailMessage,
    InboundMessage,
    OpsUser,
    OutreachItem,
    Prospect,
    WaMessage,
)
from app.collections.templates import FORBIDDEN_PATTERNS
from app.collections.timeutil import utcnow
from app.collections.whatsapp import WhatsAppError
from app.db import get_db

router = APIRouter(prefix="/collections/ops", tags=["collections-ops"])

PARTY_TYPES = ("account", "prospect", "client", "from")


# ---------------------------------------------------------------- helpers


def _key(m: InboundMessage) -> tuple[str, str]:
    return (m.party_type, m.party_id) if m.party_type else ("from", m.from_index)


def _preview(text: str, n: int = 140) -> str:
    return re.sub(r"\s+", " ", mailbox.reply_text(text or ""))[:n]


def _party(db: Session, ptype: str, pid: str, fallback: InboundMessage | None = None) -> dict:
    if ptype == "account":
        a = db.get(DebtorAccount, pid)
        if a is None:
            raise HTTPException(404, "Not found.")
        c = db.get(Client, a.client_id)
        return {"type": ptype, "id": pid, "name": a.debtor_name, "kind": "Debtor", "client_name": c.business_name if c else None,
                "reference_code": a.reference_code, "balance": a.balance, "status": a.status,
                "opted_out": bool(a.opted_out_at), "email": a.email}
    if ptype == "prospect":
        p = db.get(Prospect, pid)
        if p is None:
            raise HTTPException(404, "Not found.")
        return {"type": ptype, "id": pid, "name": p.org_name, "kind": "Prospect", "status": p.status, "email": p.email,
                "opted_out": p.status == "Do not contact"}
    if ptype == "client":
        c = db.get(Client, pid)
        if c is None:
            raise HTTPException(404, "Not found.")
        return {"type": ptype, "id": pid, "name": c.business_name, "kind": "Client", "status": c.status, "opted_out": False}
    if ptype == "from":
        name = fallback.from_name or fallback.from_address if fallback else "Unknown sender"
        return {"type": ptype, "id": pid, "name": name, "kind": "Not matched", "opted_out": False,
                "email": fallback.from_address if fallback else None}
    raise HTTPException(400, "Unknown conversation type.")


def _inbound_filter(ptype: str, pid: str):
    if ptype == "from":
        return and_(InboundMessage.party_type.is_(None), InboundMessage.from_index == pid)
    return and_(InboundMessage.party_type == ptype, InboundMessage.party_id == pid)


def _outbound_filter(ptype: str, pid: str):
    if ptype == "account":
        return EmailMessage.account_id == pid
    if ptype == "prospect":
        return EmailMessage.prospect_id == pid
    if ptype == "client":
        return and_(EmailMessage.client_id == pid, EmailMessage.account_id.is_(None), EmailMessage.prospect_id.is_(None))
    return EmailMessage.to_index == pid


def _names(db: Session) -> dict:
    return {u.id: u.name for u in db.execute(select(OpsUser)).scalars()}


# ---------------------------------------------------------------- inbox


@router.get("/inbox")
def inbox(view: str = "needs_reply", ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    check = mailbox.check_mailbox(db, if_older_than=mailbox.AUTO_CHECK_SECONDS)
    db.commit()
    rows = db.execute(select(InboundMessage).order_by(InboundMessage.received_at.desc()).limit(2000)).scalars().all()
    groups: dict[tuple, dict] = {}
    for m in rows:
        g = groups.get(_key(m))
        if g is None:
            g = groups[_key(m)] = {"latest": m, "unhandled": 0, "count": 0}
        g["count"] += 1
        if m.handled_at is None:
            g["unhandled"] += 1
    counts = {"needs_reply": sum(1 for g in groups.values() if g["unhandled"]),
              "unmatched": sum(1 for k in groups if k[0] == "from"), "all": len(groups)}
    if view == "needs_reply":
        keys = [k for k, g in groups.items() if g["unhandled"]]
    elif view == "unmatched":
        keys = [k for k in groups if k[0] == "from"]
    else:
        keys = list(groups)
    out = []
    for k in keys[:300]:
        g, m = groups[k], groups[k]["latest"]
        try:
            party = _party(db, k[0], k[1], m)
        except HTTPException:
            continue  # the person was deleted (e.g. anonymised); the messages stay for audit
        out.append({"type": k[0], "id": k[1], "name": party["name"], "kind": party["kind"],
                    "client_name": party.get("client_name"), "channel": m.channel, "subject": m.subject,
                    "preview": _preview(m.body), "received_at": m.received_at, "unhandled": g["unhandled"],
                    "auto_action": m.auto_action})
    st = mailbox.state(db)
    return {"conversations": out, "counts": counts, "connected": mailbox.reading_config(db) is not None,
            "last_check_at": st.get("last_check_at"), "last_error": st.get("last_error"),
            "check_error": None if check.get("ok") else check.get("error")}


@router.post("/inbox/check")
def inbox_check(ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    result = mailbox.check_mailbox(db)
    db.commit()
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "Couldn't check the mailbox.")
    return result


@router.get("/inbox/thread")
def inbox_thread(type: str, id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    if type not in PARTY_TYPES:
        raise HTTPException(400, "Unknown conversation type.")
    inbound = db.execute(select(InboundMessage).where(_inbound_filter(type, id))
                         .order_by(InboundMessage.received_at.desc()).limit(200)).scalars().all()
    party = _party(db, type, id, inbound[0] if inbound else None)
    party.pop("email", None)
    names = _names(db)
    items = [{"direction": "in", "channel": m.channel, "at": m.received_at, "subject": m.subject, "body": m.body,
              "from": m.from_address, "attachments": json.loads(m.attachments) if m.attachments else [],
              "handled": m.handled_at is not None, "auto_action": m.auto_action} for m in inbound]
    for e in db.execute(select(EmailMessage).where(_outbound_filter(type, id))
                        .order_by(EmailMessage.created_at.desc()).limit(200)).scalars():
        items.append({"direction": "out", "channel": "Email", "at": e.sent_at or e.created_at, "subject": e.subject,
                      "body": e.body, "kind": e.kind, "status": e.status, "error": e.error,
                      "by": names.get(e.sent_by) if e.sent_by else ("System" if e.kind != "debtor_outreach" else None)})
    wa_items = set()
    for w in db.execute(select(WaMessage).where(_wa_filter(type, id)).order_by(WaMessage.created_at.desc()).limit(200)).scalars():
        wa_items.add(w.outreach_item_id)
        items.append({"direction": "out", "channel": "WhatsApp", "at": w.created_at, "body": w.body, "kind": w.kind,
                      "status": w.status, "error": w.error, "by": names.get(w.sent_by) if w.sent_by else None})
    if type == "account":
        for o in db.execute(select(OutreachItem).where(OutreachItem.account_id == id, OutreachItem.status == "Sent",
                                                       OutreachItem.channel.in_(("WhatsApp", "SMS")))).scalars():
            if o.id in wa_items:
                continue  # already shown with its delivery status
            items.append({"direction": "out", "channel": o.channel, "at": o.sent_at, "body": o.rendered_text,
                          "kind": o.template_key, "status": "Marked sent", "by": names.get(o.sent_by)})
    items.sort(key=lambda i: i["at"] or utcnow())
    return {"party": party, "messages": items[-300:], "reply": _reply_options(db, type, id, inbound)}


def _wa_filter(ptype: str, pid: str):
    if ptype == "account":
        return WaMessage.account_id == pid
    if ptype == "prospect":
        return WaMessage.prospect_id == pid
    if ptype == "client":
        return WaMessage.client_id == pid
    return WaMessage.to_index == pid


def _reply_options(db: Session, ptype: str, pid: str, inbound: list) -> dict:
    email_to = next((m.from_address for m in inbound if m.channel == "Email"), None)
    if not email_to and ptype in ("account", "prospect"):
        email_to = _party(db, ptype, pid).get("email")
    wa_from = next((m.from_address for m in inbound if m.channel == "WhatsApp"), None)
    until = whatsapp.window_until(db, wa_from) if wa_from else None
    return {"email": bool(email_to), "whatsapp_connected": whatsapp.connected(db), "whatsapp_number": bool(wa_from),
            "whatsapp_until": until}


class ReplyIn(BaseModel):
    type: str
    id: str
    channel: str = "Email"
    body: str = Field(min_length=1, max_length=6000)
    subject: str | None = Field(default=None, max_length=200)


def _check_wording(body: str, segment_key: str | None) -> None:
    seg = None
    try:
        seg = segments.get(segment_key) if segment_key else None
    except Exception:
        seg = None
    lowered = body.lower()
    for pattern in FORBIDDEN_PATTERNS + list(getattr(seg, "extra_forbidden", ()) or ()):
        if re.search(pattern, lowered):
            raise HTTPException(422, f"This wording isn't allowed in messages to debtors (it matched '{pattern}'). "
                                     "Keep it polite, with no threats, no mention of courts, police or school results, "
                                     "and nothing about telling other people.")


@router.post("/inbox/reply")
def inbox_reply(body: ReplyIn, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    if body.type not in PARTY_TYPES:
        raise HTTPException(400, "Unknown conversation type.")
    text = body.body.strip()
    if not text:
        raise HTTPException(422, "The message is empty.")
    latest = db.execute(select(InboundMessage).where(_inbound_filter(body.type, body.id))
                        .order_by(InboundMessage.received_at.desc()).limit(1)).scalar_one_or_none()
    party = _party(db, body.type, body.id, latest)
    account_id = prospect_id = client_id = None
    if body.type == "account":
        if party["opted_out"]:
            raise HTTPException(409, "This person asked us to stop messaging them, so no more messages can be sent.")
        account = db.get(DebtorAccount, body.id)
        client = db.get(Client, account.client_id)
        _check_wording(text, client.segment if client else None)
        account_id, client_id = account.id, account.client_id
    elif body.type == "prospect":
        if party["opted_out"]:
            raise HTTPException(409, "This organisation asked not to be contacted.")
        prospect_id = body.id
    elif body.type == "client":
        client_id = body.id
    if body.channel == "WhatsApp":
        return _reply_whatsapp(db, ctx, body, text, account_id=account_id, prospect_id=prospect_id, client_id=client_id)
    latest = db.execute(select(InboundMessage).where(_inbound_filter(body.type, body.id), InboundMessage.channel == "Email")
                        .order_by(InboundMessage.received_at.desc()).limit(1)).scalar_one_or_none()
    to = latest.from_address if latest else party.get("email")
    if not to:
        raise HTTPException(409, "There's no email address to reply to.")
    subject = (body.subject or "").strip() or (latest.subject if latest and latest.subject else "Your message to Waterline")
    if not subject.lower().startswith("re:") and latest is not None and not body.subject:
        subject = "Re: " + subject
    row = emailer.send(db, to, subject[:300], text, kind="inbox_reply", client_id=client_id, account_id=account_id,
                       prospect_id=prospect_id, in_reply_to=latest.message_id if latest else None, sent_by=ctx.user.id)
    audit.record(db, ctx.actor, "inbox.replied", "EmailMessage", row.id, client_id=client_id,
                 after={"to": body.type, "status": row.status})
    if row.status != "Sent":
        db.commit()  # keep the failed attempt in the Outbox
        raise HTTPException(502, "The email didn't send: " + (row.error or "email isn't connected yet. An Admin can connect "
                                                               "the mailbox under Channels."))
    now = utcnow()
    for m in db.execute(select(InboundMessage).where(_inbound_filter(body.type, body.id),
                                                     InboundMessage.handled_at.is_(None))).scalars():
        m.handled_at, m.handled_by = now, ctx.user.id
    if prospect_id:
        p = db.get(Prospect, prospect_id)
        prospects._record(db, p, "Email", key="inbox_reply", subject=subject[:200], body=text, status="Sent",
                          ops_user_id=ctx.user.id)
    db.commit()
    return {"ok": True}


def _mark_handled(db: Session, ptype: str, pid: str, user_id: str) -> None:
    now = utcnow()
    for m in db.execute(select(InboundMessage).where(_inbound_filter(ptype, pid), InboundMessage.handled_at.is_(None))).scalars():
        m.handled_at, m.handled_by = now, user_id


def _reply_whatsapp(db: Session, ctx: OpsContext, body: ReplyIn, text: str, **links) -> dict:
    if len(text) > 4096:
        raise HTTPException(422, "WhatsApp messages can be at most 4,096 characters.")
    last = db.execute(select(InboundMessage).where(_inbound_filter(body.type, body.id), InboundMessage.channel == "WhatsApp")
                      .order_by(InboundMessage.received_at.desc()).limit(1)).scalar_one_or_none()
    if last is None:
        raise HTTPException(409, "They haven't messaged us on WhatsApp, so there's no WhatsApp chat to reply to.")
    if not whatsapp.window_until(db, last.from_address):
        raise HTTPException(409, "WhatsApp only allows a typed reply within 24 hours of their last message. "
                                 "Reply by email, or wait for the next message in the outreach queue.")
    try:
        msg = whatsapp.send_text(db, last.from_address, text, sent_by=ctx.user.id, **links)
    except WhatsAppError as exc:
        audit.record(db, ctx.actor, "inbox.replied", "WaMessage", None, client_id=links.get("client_id"),
                     after={"to": body.type, "channel": "WhatsApp", "status": "failed"})
        db.commit()  # keep the failed attempt in the Outbox
        raise HTTPException(502, f"The WhatsApp message didn't send: {exc}") from exc
    audit.record(db, ctx.actor, "inbox.replied", "WaMessage", msg.id, client_id=links.get("client_id"),
                 after={"to": body.type, "channel": "WhatsApp", "status": msg.status})
    _mark_handled(db, body.type, body.id, ctx.user.id)
    if links.get("prospect_id"):
        p = db.get(Prospect, links["prospect_id"])
        prospects._record(db, p, "WhatsApp", key="inbox_reply", subject=None, body=text, status="Sent", ops_user_id=ctx.user.id)
    db.commit()
    return {"ok": True}


class DoneIn(BaseModel):
    type: str
    id: str


@router.post("/inbox/done")
def inbox_done(body: DoneIn, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    if body.type not in PARTY_TYPES:
        raise HTTPException(400, "Unknown conversation type.")
    now = utcnow()
    n = 0
    for m in db.execute(select(InboundMessage).where(_inbound_filter(body.type, body.id),
                                                     InboundMessage.handled_at.is_(None))).scalars():
        m.handled_at, m.handled_by = now, ctx.user.id
        n += 1
    db.commit()
    return {"ok": True, "marked": n}


# ---------------------------------------------------------------- outbox


@router.get("/outbox")
def outbox(channel: str = "", failed: bool = False, limit: int = 200, ctx: OpsContext = Depends(require_ops),
           db: Session = Depends(get_db)):
    limit = max(1, min(limit, 500))
    names = _names(db)
    clients = {c.id: c.business_name for c in db.execute(select(Client)).scalars()}
    rows = []
    if channel in ("", "Email"):
        stmt = select(EmailMessage).order_by(EmailMessage.created_at.desc()).limit(limit)
        if failed:
            stmt = stmt.where(EmailMessage.status.in_(("Failed", "Logged")))
        for e in db.execute(stmt).scalars():
            rows.append({"at": e.sent_at or e.created_at, "channel": "Email", "to": e.to_address, "what": e.subject,
                         "kind": e.kind, "client_name": clients.get(e.client_id), "status": e.status, "error": e.error,
                         "by": names.get(e.sent_by) if e.sent_by else "System"})
    accounts_by_index: dict = {}
    if channel in ("", "WhatsApp"):
        stmt = select(WaMessage).order_by(WaMessage.created_at.desc()).limit(limit)
        if failed:
            stmt = stmt.where(WaMessage.status == "failed")
        was = db.execute(stmt).scalars().all()
        ids = [w.account_id for w in was if w.account_id]
        if ids:
            accounts_by_index = {a.id: a for a in db.execute(select(DebtorAccount).where(DebtorAccount.id.in_(ids))).scalars()}
        for w in was:
            a = accounts_by_index.get(w.account_id)
            rows.append({"at": w.created_at, "channel": "WhatsApp", "to": a.debtor_name if a else w.to_phone,
                         "what": w.template_name or "Reply", "kind": w.kind, "client_name": clients.get(w.client_id),
                         "status": w.status.capitalize(), "error": w.error, "by": names.get(w.sent_by) if w.sent_by else "System"})
    if channel in ("", "WhatsApp", "SMS") and not failed:
        chans = ("WhatsApp", "SMS") if not channel else (channel,)
        sent_via_api = select(WaMessage.outreach_item_id).where(WaMessage.outreach_item_id.is_not(None))
        items = db.execute(select(OutreachItem).where(OutreachItem.status == "Sent", OutreachItem.channel.in_(chans),
                                                      OutreachItem.id.not_in(sent_via_api))
                           .order_by(OutreachItem.sent_at.desc()).limit(limit)).scalars().all()
        accounts = {a.id: a for a in db.execute(select(DebtorAccount).where(
            DebtorAccount.id.in_([i.account_id for i in items]))).scalars()} if items else {}
        for i in items:
            a = accounts.get(i.account_id)
            rows.append({"at": i.sent_at, "channel": i.channel, "to": a.debtor_name if a else "", "what": i.template_key,
                         "kind": "debtor_outreach", "client_name": clients.get(i.client_id), "status": "Marked sent",
                         "error": None, "by": names.get(i.sent_by)})
    rows.sort(key=lambda r: r["at"] or utcnow(), reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------- channels (Admin)


@router.get("/channels")
def channels(request: Request, ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    from app.migrate_on_start import status as migration_status

    return {"email": {**mailbox.public_settings(db), "state": mailbox.state(db)},
            "whatsapp": {**whatsapp.public_settings(db, str(request.base_url)), "templates": whatsapp.template_rows(db)},
            "database": migration_status()}


class EmailChannelIn(BaseModel):
    address: str | None = Field(default=None, max_length=200)
    from_name: str | None = Field(default=None, max_length=120)
    password: str | None = Field(default=None, max_length=500)
    smtp_host: str | None = Field(default=None, max_length=200)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_ssl: bool | None = None
    imap_host: str | None = Field(default=None, max_length=200)
    imap_port: int | None = Field(default=None, ge=1, le=65535)


@router.put("/channels/email")
def save_email_channel(body: EmailChannelIn, ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    data = body.model_dump(exclude_none=True)
    if "address" in data and "@" not in data["address"]:
        raise HTTPException(422, "Enter the mailbox's full email address.")
    mailbox.save_settings(db, data, ctx.user.id)
    audit.record(db, ctx.actor, "channel.email_saved", "Channel", "email",
                 after={k: v for k, v in data.items() if k != "password"} | {"password_changed": bool(data.get("password"))})
    db.commit()
    return {**mailbox.public_settings(db), "state": mailbox.state(db)}


class TestIn(BaseModel):
    to: str | None = Field(default=None, max_length=254)


@router.post("/channels/email/test")
def test_email_channel(body: TestIn, ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    to = (body.to or ctx.user.email).strip()
    row = emailer.send(db, to, "Waterline Collections: test email",
                       "This is a test from the Waterline Collections ops console. If you can read this, sending works.\n\n"
                       "Reply to this email to check that replies reach the portal Inbox.",
                       kind="channel_test", sent_by=ctx.user.id)
    sending = {"ok": row.status == "Sent", "to": to,
               "error": None if row.status == "Sent" else (row.error or "No mail server is set up yet.")}
    reading = mailbox.test_reading(db)
    audit.record(db, ctx.actor, "channel.email_tested", "Channel", "email",
                 after={"sending": sending["ok"], "reading": reading["ok"]})
    db.commit()
    return {"sending": sending, "reading": reading, "source": mailbox.source(db)}


# ---------------------------------------------------------------- WhatsApp channel (Admin)


class WhatsAppChannelIn(BaseModel):
    phone_number_id: str | None = Field(default=None, max_length=64)
    waba_id: str | None = Field(default=None, max_length=64)
    access_token: str | None = Field(default=None, max_length=1000)
    app_secret: str | None = Field(default=None, max_length=200)
    api_version: str | None = Field(default=None, pattern=r"^v\d{2}\.\d$")


def _wa_out(db: Session, request_base: str | None = None) -> dict:
    return {**whatsapp.public_settings(db, request_base), "templates": whatsapp.template_rows(db)}


@router.put("/channels/whatsapp")
def save_whatsapp_channel(body: WhatsAppChannelIn, request: Request, ctx: OpsContext = Depends(require_ops_admin),
                          db: Session = Depends(get_db)):
    data = body.model_dump(exclude_none=True)
    whatsapp.save_settings(db, data, ctx.user.id)
    check = None
    if whatsapp.connected(db):
        try:
            whatsapp.refresh_number(db)
        except WhatsAppError as exc:
            check = str(exc)
    audit.record(db, ctx.actor, "channel.whatsapp_saved", "Channel", "whatsapp",
                 after={k: v for k, v in data.items() if k not in ("access_token", "app_secret")}
                 | {"token_changed": bool(data.get("access_token")), "secret_changed": bool(data.get("app_secret"))})
    db.commit()
    return {**_wa_out(db, str(request.base_url)), "check_error": check}


class WaTestIn(BaseModel):
    to: str = Field(min_length=7, max_length=20)


@router.post("/channels/whatsapp/test")
def test_whatsapp_channel(body: WaTestIn, ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    try:
        msg = whatsapp.send_test(db, body.to)
    except WhatsAppError as exc:
        db.commit()
        raise HTTPException(502, str(exc)) from exc
    audit.record(db, ctx.actor, "channel.whatsapp_tested", "WaMessage", msg.id)
    db.commit()
    return {"ok": True, "to": whatsapp.to_e164(body.to)}


@router.post("/channels/whatsapp/templates/submit")
def submit_wa_templates(ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    try:
        result = whatsapp.submit_templates(db)
    except WhatsAppError as exc:
        raise HTTPException(502, str(exc)) from exc
    audit.record(db, ctx.actor, "channel.whatsapp_templates_submitted", "Channel", "whatsapp",
                 after={"submitted": result["submitted"], "errors": len(result["errors"])})
    db.commit()
    return {**result, "templates": whatsapp.template_rows(db)}


@router.post("/channels/whatsapp/templates/sync")
def sync_wa_templates(ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    try:
        result = whatsapp.sync_templates(db)
    except WhatsAppError as exc:
        raise HTTPException(502, str(exc)) from exc
    db.commit()
    return {**result, "templates": whatsapp.template_rows(db)}


# ---------------------------------------------------------------- queue: send on WhatsApp


@router.post("/queue/{item_id}/whatsapp")
def queue_send_whatsapp(item_id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    """One-click send of a queued message as its approved WhatsApp template.
    The same conduct and frequency checks as "Mark sent" apply first."""
    item = db.get(OutreachItem, item_id)
    if item is None:
        raise HTTPException(404, "Not found.")
    if not whatsapp.connected(db):
        raise HTTPException(409, "WhatsApp isn't connected yet. Use Open WhatsApp instead.")
    now = utcnow()
    try:
        account, client = outreach._check_sendable(db, item, now)
    except outreach.OutreachRefused as exc:
        db.commit()
        raise HTTPException(409, str(exc)) from exc
    if not account.phone:
        raise HTTPException(409, "There's no phone number for this person.")
    values = json.loads(item.render_values) if item.render_values else None
    if values is None:
        raise HTTPException(409, "This item was queued before WhatsApp sending existed. Use Open WhatsApp for it.")
    tpl = whatsapp.approved_template(db, client.segment, item.template_key, item.template_version, whatsapp.variant_for(values))
    if tpl is None:
        raise HTTPException(409, "WhatsApp hasn't approved this message template yet. Use Open WhatsApp for now.")
    try:
        whatsapp.send_template(db, account.phone, tpl, values, body=item.rendered_text, account_id=account.id,
                               client_id=client.id, outreach_item_id=item.id, sent_by=ctx.user.id)
    except WhatsAppError as exc:
        db.commit()  # keep the failed attempt in the Outbox
        raise HTTPException(502, f"WhatsApp didn't send it: {exc}") from exc
    outreach._record_sent(db, item, account, "WhatsApp", ctx.actor, now)
    db.commit()
    return {"ok": True}
