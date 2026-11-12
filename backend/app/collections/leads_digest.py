"""Daily leads digest: today's new prospects, sent to our own team on
WhatsApp as the approved template wl_internal_leads_digest_v1.

- Leads: prospects created today (Lagos time) whose status is still New.
- Recipients: only the numbers in Config → leads_digest_recipients (Admin
  edits it). whatsapp._send refuses the template for any other number, so
  nothing here, or anywhere else, can send it outside that list.
- One message per recipient, each logged in cl_wa_message (the Outbox).
  A recipient who already got today's digest is skipped unless resend=True.

WhatsApp parameters can't contain line breaks, so the list is one line,
numbered, and cut short (with "and N more") to stay within Meta's limits.
"""

import re
from datetime import time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collections import settings_store, whatsapp
from app.collections.models import Prospect, WaMessage, WaTemplate
from app.collections.timeutil import lagos_date, lagos_datetime, utcnow

RECIPIENTS_KEY = "leads_digest_recipients"
MAX_LIST_CHARS = 700  # the template's fixed text plus this stays under Meta's 1024


class DigestRefused(ValueError):
    pass


def _today_bounds(now):
    today = lagos_date(now)
    start = lagos_datetime(today, time(0, 0))
    return today, start, start + timedelta(days=1)


def recipients(db: Session) -> list[str]:
    return [whatsapp.to_e164(n) for n in (settings_store.get(db, RECIPIENTS_KEY) or [])]


def todays_leads(db: Session, now=None) -> list[Prospect]:
    _, start, end = _today_bounds(now or utcnow())
    return db.execute(select(Prospect).where(Prospect.status == "New", Prospect.created_at >= start,
                                             Prospect.created_at < end).order_by(Prospect.created_at)).scalars().all()


def _one_line(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _lead_line(n: int, p: Prospect) -> str:
    why = _one_line(p.fit_note) or (f"{p.fit} fit" if p.fit else "not noted")
    parts = [_one_line(p.org_name), _one_line(p.city) or "city not noted", p.phone or "no phone"]
    return f"{n}) " + ", ".join(parts) + f". Why: {why}"


def compose(leads: list[Prospect], today) -> tuple[dict, str]:
    """Template values and the readable text kept in the Outbox."""
    budget = MAX_LIST_CHARS - 40  # room for "...and N more in the portal."
    text, shown = "", 0
    for i, p in enumerate(leads, 1):
        line = _lead_line(i, p)
        if len(line) > budget:
            line = line[:budget - 1].rstrip() + "…"
        candidate = f"{text} {line}" if text else line
        if len(candidate) > budget:
            break
        text, shown = candidate, i
    if shown < len(leads):
        text += f" ...and {len(leads) - shown} more in the portal."
    values = {"digest_date": today.strftime("%d/%m/%Y"), "lead_count": str(len(leads)), "lead_list": text}
    body = whatsapp.LEADS_DIGEST["body"]
    for k, v in values.items():
        body = body.replace("{{" + k + "}}", v)
    return values, body


def _sent_today(db: Session, name: str, start) -> set[str]:
    return set(db.execute(select(WaMessage.to_index).where(
        WaMessage.template_name == name, WaMessage.created_at >= start, WaMessage.status != "failed")).scalars())


def preview(db: Session, now=None) -> dict:
    now = now or utcnow()
    today, start, _ = _today_bounds(now)
    leads = todays_leads(db, now)
    name = whatsapp.leads_digest_name()
    status = db.execute(select(WaTemplate.status).where(WaTemplate.meta_name == name)).scalar_one_or_none()
    done = _sent_today(db, name, start)
    recips = recipients(db)
    return {
        "date": today.isoformat(), "template": name, "template_status": status or "NOT_SUBMITTED",
        "connected": whatsapp.connected(db), "recipients": len(recips),
        "already_sent": sum(1 for r in recips if whatsapp.phone_index(r) in done),
        "leads": [{"id": p.id, "org_name": p.org_name, "city": p.city, "phone": p.phone, "fit": p.fit,
                   "fit_note": p.fit_note} for p in leads],
        "text": compose(leads, today)[1] if leads else None,
    }


def send(db: Session, *, sent_by: str | None, resend: bool = False, now=None) -> dict:
    now = now or utcnow()
    today, start, _ = _today_bounds(now)
    recips = recipients(db)
    if not recips:
        raise DigestRefused("No recipients yet. An Admin adds team numbers under Config → leads digest recipients.")
    if not whatsapp.connected(db):
        raise DigestRefused("WhatsApp isn't connected yet.")
    tpl = whatsapp.approved_template(db, whatsapp.INTERNAL, whatsapp.LEADS_DIGEST["key"],
                                     whatsapp.LEADS_DIGEST["version"], whatsapp.LEADS_DIGEST["variant"])
    if tpl is None:
        raise DigestRefused("WhatsApp hasn't approved the leads digest template yet. An Admin submits it under Channels.")
    leads = todays_leads(db, now)
    if not leads:
        raise DigestRefused("No new prospects were added today, so there's nothing to send.")
    values, body = compose(leads, today)
    done = set() if resend else _sent_today(db, tpl.meta_name, start)
    sent, skipped, failed = [], [], []
    for to in recips:
        if whatsapp.phone_index(to) in done:
            skipped.append(to)
            continue
        try:
            whatsapp.send_template(db, to, tpl, values, body=body, sent_by=sent_by)
            sent.append(to)
        except whatsapp.WhatsAppError as exc:
            failed.append({"to": to, "error": str(exc)})
    return {"date": today.isoformat(), "leads": len(leads), "sent": sent, "skipped": skipped, "failed": failed}
