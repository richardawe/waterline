"""Waterline's own sales outreach to schools, estates and other prospects.

Ops write to an organisation's published business address or line, never a
personal one. Emails go out through the normal SMTP sender with Reply-To set
to the Collections inbox; WhatsApp is sent by hand from the Waterline phone
(a click-to-chat link with the text filled in) and then marked sent, the
same way the debtor queue works.

Conduct rules, enforced here rather than left to habit:
- Every message ends with who we are (company name and RC number) and how
  to opt out; ops can edit the body but not remove that footer.
- "Do not contact" blocks every channel for good.
- At most one message per organisation per day.
- Three messages without a reply is the limit: log a reply first, or stop.
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collections import emailer, settings_store
from app.collections.models import Prospect, ProspectTouch
from app.collections.phone import normalise_phone
from app.collections.timeutil import lagos_date, utcnow

STATUSES = ("New", "Contacted", "Replied", "Interested", "Signed up", "Not interested", "Do not contact")
FITS = ("High", "Medium", "Low")
OUTBOUND = ("Email", "WhatsApp")
MAX_UNANSWERED = 3
COMPANY = "3D7 Technologies Limited (RC 6979200)"


class ProspectRefused(ValueError):
    pass


# ---------------------------------------------------------------- wording

_AUDIENCE = {
    "school": {
        "greeting": "Dear Head of School",
        "owed": "unpaid school fees",
        "payers": "parents",
        "account": "the school's account",
        "page": "/collections/schools/",
    },
    "estate": {
        "greeting": "Dear Facility Manager",
        "owed": "unpaid service charges and levies",
        "payers": "residents",
        "account": "the estate's account",
        "page": "/collections/estates/",
    },
}
_OTHER = {"greeting": "Dear Sir or Madam", "owed": "unpaid balances", "payers": "customers",
          "account": "your account", "page": "/collections/"}

TEMPLATES = {
    "intro": {
        "subject": "Recovering {owed} for {org_name}",
        "email": (
            "{greeting},\n\n"
            "I'm {sender_first} from Waterline Collections. We help organisations like {org_name} recover {owed} "
            "from past terms and periods, without your team having to chase {payers} themselves.\n\n"
            "You upload who owes (or add them one at a time), and we send polite WhatsApp reminders in your name, "
            "offer instalment plans you approve, and {payers} pay straight into {account}. We never threaten or "
            "embarrass anyone, and nothing goes out until you've checked the list.\n\n"
            "There's no setup or monthly fee. We charge 20% only on payments you confirm were recovered, so the "
            "balances you've already given up on are a good place to start.\n\n"
            "You can see exactly what {payers} receive here: {page_url}\n\n"
            "Would you be open to a 15-minute call this week?\n\n"
            "{sender_name}\nWaterline Collections"
        ),
        "whatsapp": (
            "{greeting}, I'm {sender_first} from Waterline Collections. We help organisations like {org_name} recover "
            "{owed} with polite WhatsApp reminders and instalment plans, and {payers} pay straight into {account}. "
            "No upfront cost: we charge 20% only on what you recover. See how it works: {page_url} "
            "Could we have a quick 15-minute call this week?"
        ),
    },
    "follow_up": {
        "subject": "Re: Recovering {owed} for {org_name}",
        "email": (
            "{greeting},\n\n"
            "Following up on my earlier note about recovering {owed} for {org_name}. If older balances are still "
            "outstanding, we can start with just those: there's nothing to pay unless {payers} pay.\n\n"
            "Happy to send more detail or set up a short call. If it isn't a priority right now, no problem, and "
            "I won't keep writing.\n\n"
            "{sender_name}\nWaterline Collections\n{page_url}"
        ),
        "whatsapp": (
            "{greeting}, following up on my message about recovering {owed} for {org_name}. We can start with just "
            "the old balances, and there's nothing to pay unless {payers} pay. Would a short call help? {page_url}"
        ),
    },
}


def _audience(p: Prospect) -> dict:
    return _AUDIENCE.get(p.segment, _OTHER)


def greeting(p: Prospect) -> str:
    """A named greeting only once ops have confirmed the person still holds the role."""
    if p.contact_confirmed and p.contact_name:
        return f"Dear {p.contact_name.strip()}"
    return _audience(p)["greeting"]


def _fields(db: Session, p: Prospect, sender_name: str) -> dict:
    a = _audience(p)
    return {
        "greeting": greeting(p), "org_name": p.org_name, "owed": a["owed"], "payers": a["payers"],
        "account": a["account"], "page_url": emailer.site_url(a["page"]),
        "sender_name": sender_name, "sender_first": sender_name.split(" ")[0],
    }


def footer(db: Session, channel: str) -> str:
    phone = settings_store.get(db, "contact_whatsapp")
    if channel == "Email":
        return (f"\n\n--\nWaterline Collections is a service of {COMPANY}, Lagos, Nigeria. "
                f"Call or WhatsApp {phone}.\n"
                "We found your details on your organisation's public website or listing. If you'd rather not hear "
                "from us, reply \"no thanks\" and we won't contact you again.")
    return f"\n\n— Waterline Collections, {COMPANY}. Reply \"no thanks\" and we won't message again."


def drafts(db: Session, p: Prospect, sender_name: str) -> dict:
    """Ready-to-edit wording for each template and channel (footer shown separately)."""
    f = _fields(db, p, sender_name)
    return {
        key: {"subject": t["subject"].format(**f), "email": t["email"].format(**f), "whatsapp": t["whatsapp"].format(**f)}
        for key, t in TEMPLATES.items()
    } | {"footer": {"email": footer(db, "Email"), "whatsapp": footer(db, "WhatsApp")}}


# ---------------------------------------------------------------- rules


def whatsapp_number(p: Prospect) -> str | None:
    if not p.phone:
        return None
    try:
        return normalise_phone(p.phone)
    except ValueError:
        return None


def unanswered_count(db: Session, p: Prospect) -> int:
    """Outbound messages since the last reply (or ever, if no reply)."""
    last_reply = db.execute(
        select(func.max(ProspectTouch.created_at)).where(ProspectTouch.prospect_id == p.id, ProspectTouch.channel == "Reply")
    ).scalar_one()
    stmt = select(func.count()).select_from(ProspectTouch).where(
        ProspectTouch.prospect_id == p.id, ProspectTouch.channel.in_(OUTBOUND), ProspectTouch.status == "Sent")
    if last_reply is not None:
        stmt = stmt.where(ProspectTouch.created_at > last_reply)
    return db.execute(stmt).scalar_one()


def sent_today(db: Session, p: Prospect, today: date) -> bool:
    times = db.execute(
        select(ProspectTouch.created_at).where(ProspectTouch.prospect_id == p.id, ProspectTouch.channel.in_(OUTBOUND),
                                               ProspectTouch.status == "Sent")
    ).scalars().all()
    return any(lagos_date(t) == today for t in times if t is not None)


def check_can_message(db: Session, p: Prospect, channel: str) -> None:
    if p.status == "Do not contact":
        raise ProspectRefused("This organisation asked not to be contacted.")
    if channel == "Email" and not p.email:
        raise ProspectRefused("There's no email address for this organisation.")
    if channel == "WhatsApp" and not whatsapp_number(p):
        raise ProspectRefused("There's no valid mobile number for WhatsApp.")
    if sent_today(db, p, lagos_date(utcnow())):
        raise ProspectRefused("Already messaged today. One message a day per organisation.")
    if unanswered_count(db, p) >= MAX_UNANSWERED:
        raise ProspectRefused(f"{MAX_UNANSWERED} messages with no reply. Log a reply first, or leave them be.")


def _record(db: Session, p: Prospect, channel: str, *, key: str, subject: str | None, body: str, status: str,
            ops_user_id: str, error: str | None = None) -> ProspectTouch:
    touch = ProspectTouch(prospect_id=p.id, channel=channel, template_key=key, subject=subject, body=body,
                          status=status, error=error, ops_user_id=ops_user_id)
    db.add(touch)
    if status == "Sent":
        p.last_contacted_at = utcnow()
        if p.status == "New":
            p.status = "Contacted"
    db.flush()
    return touch


def _clean(text: str, limit: int) -> str:
    text = (text or "").strip()
    if not text:
        raise ProspectRefused("The message is empty.")
    if len(text) > limit:
        raise ProspectRefused(f"The message is too long ({limit} characters max).")
    return text


def send_email(db: Session, p: Prospect, *, subject: str, body: str, key: str, ops_user_id: str) -> ProspectTouch:
    check_can_message(db, p, "Email")
    subject = _clean(subject, 200)
    full = _clean(body, 6000) + footer(db, "Email")
    row = emailer.send(db, p.email, subject, full, kind="prospect", reply_to=settings_store.get(db, "contact_email"))
    if row.status == "Failed":
        _record(db, p, "Email", key=key, subject=subject, body=full, status="Failed", error=row.error, ops_user_id=ops_user_id)
        raise ProspectRefused(f"The email didn't send: {row.error}")
    return _record(db, p, "Email", key=key, subject=subject, body=full, status="Sent", ops_user_id=ops_user_id)


def whatsapp_text(db: Session, body: str) -> str:
    return _clean(body, 1500) + footer(db, "WhatsApp")


def mark_whatsapp_sent(db: Session, p: Prospect, *, body: str, key: str, ops_user_id: str) -> ProspectTouch:
    check_can_message(db, p, "WhatsApp")
    return _record(db, p, "WhatsApp", key=key, subject=None, body=whatsapp_text(db, body), status="Sent",
                   ops_user_id=ops_user_id)


def log_touch(db: Session, p: Prospect, *, channel: str, body: str, ops_user_id: str) -> ProspectTouch:
    """A call, a reply they sent us, or a note. A reply moves New/Contacted to Replied."""
    if channel not in ("Call", "Reply", "Note"):
        raise ProspectRefused("Log a call, a reply or a note.")
    touch = ProspectTouch(prospect_id=p.id, channel=channel, body=_clean(body, 4000), status="Logged",
                          ops_user_id=ops_user_id)
    db.add(touch)
    if channel == "Reply" and p.status in ("New", "Contacted"):
        p.status = "Replied"
    if channel == "Call":
        p.last_contacted_at = utcnow()
        if p.status == "New":
            p.status = "Contacted"
    db.flush()
    return touch
