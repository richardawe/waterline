"""Ops messaging: the Inbox (replies people send us), the Outbox (everything
we sent), and Channels (Admin: connect the collections mailbox).

Replies typed in the Inbox are answers to someone who wrote to us, so they
are not outreach: they don't use up the daily/weekly outreach limits. They
still go through the conduct wording check for debtors, and nobody who has
opted out (or asked a prospect "no thanks") can be written to."""

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.collections import audit, emailer, mailbox, prospects, segments
from app.collections.auth import OpsContext, require_ops, require_ops_admin
from app.collections.models import (
    Client,
    DebtorAccount,
    EmailMessage,
    InboundMessage,
    OpsUser,
    OutreachItem,
    Prospect,
)
from app.collections.templates import FORBIDDEN_PATTERNS
from app.collections.timeutil import utcnow
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
    if type == "account":
        for o in db.execute(select(OutreachItem).where(OutreachItem.account_id == id, OutreachItem.status == "Sent",
                                                       OutreachItem.channel.in_(("WhatsApp", "SMS")))).scalars():
            items.append({"direction": "out", "channel": o.channel, "at": o.sent_at, "body": o.rendered_text,
                          "kind": o.template_key, "status": "Sent", "by": names.get(o.sent_by)})
    items.sort(key=lambda i: i["at"] or utcnow())
    return {"party": party, "messages": items[-300:]}


class ReplyIn(BaseModel):
    type: str
    id: str
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
    if channel in ("", "WhatsApp", "SMS") and not failed:
        chans = ("WhatsApp", "SMS") if not channel else (channel,)
        items = db.execute(select(OutreachItem).where(OutreachItem.status == "Sent", OutreachItem.channel.in_(chans))
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
def channels(ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    from app.migrate_on_start import status as migration_status

    return {"email": {**mailbox.public_settings(db), "state": mailbox.state(db)}, "database": migration_status()}


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
