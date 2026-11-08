"""WhatsApp Cloud API (Meta) for Collections.

Connection settings live in cl_channel["whatsapp"], entered by an Admin in
ops → Channels: phone number ID, WhatsApp Business account ID, a permanent
access token (system user) and the app secret. The token and app secret are
encrypted and never returned by the API.

Sending:
- Messages we start use our own message templates, submitted to Meta for
  approval (category UTILITY, named parameters that match our
  {{placeholders}}). Only APPROVED templates are used; until then the queue's
  "Open WhatsApp" link still works.
- Free text is only allowed inside WhatsApp's 24-hour window after the
  person last messaged us (Inbox replies).

Receiving: Meta calls /collections/whatsapp/webhook with incoming messages
and delivery statuses. Requests are checked against the app secret
(X-Hub-Signature-256). Incoming messages go into cl_inbound (same Inbox as
email): STOP opts a debtor out, "no thanks" marks a prospect Do not contact.

HTTP goes through urllib (standard library): new Python packages need a
manual pip install on the server, which we can't do without cPanel."""

import hashlib
import hmac
import http.client
import json
import logging
import re
import secrets
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collections import audit, crypto, mailbox, segments, templates
from app.collections.audit import SYSTEM
from app.collections.models import (
    Channel,
    DebtorAccount,
    InboundMessage,
    MessageTemplate,
    Prospect,
    ProspectTouch,
    WaMessage,
    WaTemplate,
)
from app.collections.timeutil import utcnow

logger = logging.getLogger(__name__)

KEY = "whatsapp"
GRAPH = "https://graph.facebook.com"
DEFAULTS = {"api_version": "v23.0", "language": "en"}
WINDOW = timedelta(hours=24)
STATUS_RANK = {"failed": -1, "accepted": 0, "sent": 1, "delivered": 2, "read": 3}
TEST_TEMPLATE = ("hello_world", "en_US")  # every new WhatsApp account has it

EXAMPLES = {
    "debtor_first_name": "Adaeze", "client_name": "Greenfield Academy", "description": "School fees, 2nd term 2025/26",
    "balance": "₦150,000.00", "link": "https://waterline.ng/collections/p/Xk2v9QpL7mN3", "instalment_amount": "₦50,000.00",
    "due_date": "20/11/2026", "discount_line": "Pay ₦135,000.00 in full by 30/11/2026 and save ₦15,000.00.",
}


class WhatsAppError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- settings


def _row(db: Session) -> Channel | None:
    return db.get(Channel, KEY)


def stored(db: Session) -> dict:
    row = _row(db)
    return json.loads(row.settings) if row and row.settings else {}


def config(db: Session) -> dict | None:
    s = {**DEFAULTS, **stored(db)}
    if not (s.get("access_token") and s.get("phone_number_id")):
        return None
    return s


def connected(db: Session) -> bool:
    return config(db) is not None


def webhook_url(request_base: str | None = None) -> str:
    """The callback URL to paste into Meta. Uses COLLECTIONS_API_BASE_URL,
    or, if that's still the local default, the host this request came in on
    (always https: Meta refuses plain http)."""
    from app.config import get_settings

    base = get_settings().collections_api_base_url.rstrip("/")
    if "localhost" in base and request_base and "localhost" not in request_base and "127.0.0.1" not in request_base:
        base = "https://" + request_base.split("://", 1)[-1].rstrip("/")
    return base + "/collections/whatsapp/webhook"


def public_settings(db: Session, request_base: str | None = None) -> dict:
    s = {**DEFAULTS, **stored(db)}
    return {
        "phone_number_id": s.get("phone_number_id") or "", "waba_id": s.get("waba_id") or "",
        "api_version": s["api_version"], "display_number": s.get("display_number"), "verified_name": s.get("verified_name"),
        "quality_rating": s.get("quality_rating"), "has_token": bool(s.get("access_token")),
        "has_app_secret": bool(s.get("app_secret")), "verify_token": s.get("verify_token") or "",
        "webhook_url": webhook_url(request_base), "connected": connected(db), "last_webhook_at": s.get("last_webhook_at"),
    }


def _save(db: Session, data: dict, actor_id: str | None = None) -> None:
    row = _row(db)
    if row is None:
        row = Channel(key=KEY)
        db.add(row)
        db.flush()  # so a second lookup in the same request finds it
    row.settings = json.dumps(data)
    if actor_id:
        row.updated_by = actor_id


SECRET_LABELS = {"access_token": "access token", "app_secret": "app secret"}
ID_LABELS = {"phone_number_id": "phone number ID", "waba_id": "WhatsApp Business account ID"}


def _clean_secret(value: str, label: str) -> str:
    """Tokens and app secrets are plain ASCII with no spaces. Copying them
    from Meta, email or a notes app can add line breaks, spaces or invisible
    characters (dropped here), or a "…" from a shortened display (refused:
    the value is incomplete). Without this, sending the token in an HTTP
    header fails outside our error handling."""
    cleaned = "".join(ch for ch in value if not (ch.isspace() or unicodedata.category(ch) == "Cf"))
    if not cleaned.isascii() or not cleaned.isprintable():
        raise WhatsAppError(f"The {label} contains characters that can't be part of it (for example \"…\" from a "
                            "shortened copy). Copy the full value from Meta again and paste it in.")
    return cleaned


def save_settings(db: Session, data: dict, actor_id: str) -> None:
    """Raises WhatsAppError (nothing saved) if a value can't be valid."""
    s = stored(db)
    for k in ("phone_number_id", "waba_id", "api_version"):
        if data.get(k) is not None:
            value = re.sub(r"\s", "", str(data[k]))[:64]
            if k in ID_LABELS and value and not (value.isascii() and value.isdigit()):
                raise WhatsAppError(f"The {ID_LABELS[k]} should be digits only, as shown in Meta's API Setup.")
            s[k] = value
    for k in ("access_token", "app_secret"):  # blank keeps the saved one
        if data.get(k):
            s[k] = _clean_secret(str(data[k]), SECRET_LABELS[k])
    s.setdefault("verify_token", secrets.token_urlsafe(24))
    _save(db, s, actor_id)


def refresh_number(db: Session) -> dict:
    """Ask Meta about the phone number: proves the token and IDs work."""
    cfg = config(db)
    if cfg is None:
        raise WhatsAppError("Enter the phone number ID and access token first.")
    info = _call(cfg, "GET", f"/{cfg['phone_number_id']}?fields=display_phone_number,verified_name,quality_rating")
    s = stored(db)
    s.update(display_number=info.get("display_phone_number"), verified_name=info.get("verified_name"),
             quality_rating=info.get("quality_rating"))
    _save(db, s)
    return info


def note_webhook(db: Session) -> None:
    s = stored(db)
    if s:
        s["last_webhook_at"] = utcnow().isoformat()
        _save(db, s)


# ---------------------------------------------------------------- HTTP


def _call(cfg: dict, method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{GRAPH}/{cfg['api_version']}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {cfg['access_token']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read().decode()).get("error", {})
        except Exception:
            err = {}
        detail = (err.get("error_data") or {}).get("details") or err.get("error_user_msg") or err.get("message") or str(exc)
        raise WhatsAppError(_friendly(err.get("code"), detail), err.get("code")) from exc
    except urllib.error.URLError as exc:
        raise WhatsAppError(f"Couldn't reach WhatsApp: {exc.reason}") from exc
    except (TimeoutError, OSError, http.client.HTTPException) as exc:
        # Read timeouts, dropped or reset connections: urllib doesn't wrap
        # these in URLError once the connection is open.
        raise WhatsAppError(f"Couldn't reach WhatsApp ({type(exc).__name__}). Try again in a minute.") from exc
    except ValueError:
        # http.client refuses header/URL values it can't send (line breaks,
        # non-ASCII). Its message quotes the value, i.e. the token, so it is
        # deliberately not passed on.
        raise WhatsAppError("The saved access token or IDs contain characters that can't be sent to WhatsApp. "
                            "Paste them in again under Channels.") from None
    try:
        return json.loads(raw.decode() or "{}")
    except ValueError as exc:
        raise WhatsAppError("WhatsApp sent back a reply we couldn't read. Try again in a minute.") from exc


def _friendly(code, detail: str) -> str:
    hints = {
        190: "The access token is invalid or expired. Generate a permanent system-user token and save it under Channels.",
        131030: "This number isn't on the test number's allowed list. Add it under API Setup in Meta, or wait for the real number.",
        131047: "More than 24 hours have passed since they last messaged, so only an approved template can be sent.",
        131026: "WhatsApp couldn't deliver to this number (it may not be on WhatsApp).",
        132001: "WhatsApp doesn't have this template (or it isn't approved yet).",
        131056: "Too many messages to this number in a short time. Try again later.",
    }
    return hints.get(code, detail)


# ---------------------------------------------------------------- phone helpers


def to_e164(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return "+" + digits


def phone_index(phone: str) -> str:
    return crypto.blind_index(to_e164(phone))


# ---------------------------------------------------------------- templates


def _meta_name(segment: str, key: str, version: int, variant: str) -> str:
    return f"wl_{segment}_{key.lower()}_v{version}" + (f"_{variant}" if variant else "")


def _body_for(body: str, variant: str) -> str:
    if variant == "nd":
        body = re.sub(r"\s*{{\s*discount_line\s*}}\s*", " ", body)
    return re.sub(r"\s{2,}", " ", body).strip()


def wanted_templates(db: Session) -> list[tuple[MessageTemplate, str]]:
    """Active templates of live segments, plus the no-discount variant."""
    live = [k for k in segments.SEGMENTS if segments.status(db, k) == "live"]
    for k in live:
        templates.ensure_defaults(db, k)
    rows = db.execute(select(MessageTemplate).where(MessageTemplate.segment.in_(live), MessageTemplate.language == "en",
                                                    MessageTemplate.is_active.is_(True))).scalars().all()
    out = []
    for t in rows:
        out.append((t, ""))
        if "discount_line" in t.body:
            out.append((t, "nd"))
    return out


def template_rows(db: Session) -> list[dict]:
    have = {(r.segment, r.key, r.version, r.variant): r for r in db.execute(select(WaTemplate)).scalars()}
    out = []
    for t, variant in wanted_templates(db):
        r = have.get((t.segment, t.key, t.version, variant))
        out.append({"segment": t.segment, "key": t.key, "version": t.version, "variant": variant,
                    "name": _meta_name(t.segment, t.key, t.version, variant),
                    "status": r.status if r else "NOT_SUBMITTED", "reason": r.reason if r else None})
    return sorted(out, key=lambda x: (x["segment"], x["key"], x["variant"]))


def submit_templates(db: Session) -> dict:
    cfg = config(db)
    if cfg is None or not cfg.get("waba_id"):
        raise WhatsAppError("Save the WhatsApp Business account ID and access token first.")
    have = {(r.segment, r.key, r.version, r.variant): r for r in db.execute(select(WaTemplate)).scalars()}
    submitted, errors = 0, []
    for t, variant in wanted_templates(db):
        row = have.get((t.segment, t.key, t.version, variant))
        if row is not None and row.status not in ("NOT_SUBMITTED", "ERROR"):
            continue
        name = _meta_name(t.segment, t.key, t.version, variant)
        body = _body_for(t.body, variant)
        params = list(dict.fromkeys(re.findall(r"{{\s*(\w+)\s*}}", body)))
        body = re.sub(r"{{\s*(\w+)\s*}}", r"{{\1}}", body)
        component = {"type": "BODY", "text": body}
        if params:
            component["example"] = {"body_text_named_params": [{"param_name": p, "example": EXAMPLES.get(p, "example")}
                                                                for p in params]}
        if row is None:
            row = WaTemplate(segment=t.segment, key=t.key, version=t.version, variant=variant, meta_name=name)
            db.add(row)
        try:
            res = _call(cfg, "POST", f"/{cfg['waba_id']}/message_templates", {
                "name": name, "language": cfg["language"], "category": "UTILITY", "parameter_format": "NAMED",
                "components": [component]})
            row.meta_id, row.status, row.reason = res.get("id"), (res.get("status") or "PENDING").upper(), None
            row.submitted_at = utcnow()
            submitted += 1
        except WhatsAppError as exc:
            row.status, row.reason = "ERROR", str(exc)[:500]
            errors.append(f"{name}: {exc}")
    db.flush()
    return {"submitted": submitted, "errors": errors}


def sync_templates(db: Session) -> dict:
    cfg = config(db)
    if cfg is None or not cfg.get("waba_id"):
        raise WhatsAppError("Save the WhatsApp Business account ID and access token first.")
    res = _call(cfg, "GET", f"/{cfg['waba_id']}/message_templates?fields=name,status,rejected_reason,id&limit=250")
    by_name = {t["name"]: t for t in res.get("data", [])}
    updated = 0
    for row in db.execute(select(WaTemplate)).scalars():
        t = by_name.get(row.meta_name)
        if t is None:
            continue
        status = (t.get("status") or "").upper()
        reason = t.get("rejected_reason") if t.get("rejected_reason") not in (None, "NONE") else None
        if status and (status != row.status or reason != row.reason):
            row.status, row.reason, row.meta_id = status, reason, t.get("id") or row.meta_id
            updated += 1
    db.flush()
    return {"updated": updated}


def approved_template(db: Session, segment: str, key: str, version: int, variant: str) -> WaTemplate | None:
    return db.execute(select(WaTemplate).where(
        WaTemplate.segment == segment, WaTemplate.key == key, WaTemplate.version == version,
        WaTemplate.variant == variant, WaTemplate.status == "APPROVED")).scalar_one_or_none()


def approved_set(db: Session) -> set[tuple]:
    return {(r.segment, r.key, r.version, r.variant) for r in db.execute(
        select(WaTemplate).where(WaTemplate.status == "APPROVED")).scalars()}


def variant_for(values: dict) -> str:
    return "nd" if "discount_line" in values and not (values.get("discount_line") or "").strip() else ""


# ---------------------------------------------------------------- sending


def _record(db: Session, *, to: str, kind: str, body: str, template_name: str | None, wamid: str | None,
            status: str, error: str | None = None, **links) -> WaMessage:
    row = WaMessage(to_phone=to, to_index=phone_index(to), kind=kind, body=body, template_name=template_name,
                    wamid=wamid, status=status, error=error, status_at=utcnow(), created_at=utcnow(), **links)
    db.add(row)
    db.flush()
    return row


def _send(db: Session, cfg: dict, to: str, payload: dict, *, kind: str, body: str, template_name: str | None,
          **links) -> WaMessage:
    to = to_e164(to)
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to.lstrip("+"), **payload}
    try:
        res = _call(cfg, "POST", f"/{cfg['phone_number_id']}/messages", payload)
    except WhatsAppError as exc:
        _record(db, to=to, kind=kind, body=body, template_name=template_name, wamid=None, status="failed",
                error=str(exc)[:500], **links)
        raise
    wamid = ((res.get("messages") or [{}])[0]).get("id")
    return _record(db, to=to, kind=kind, body=body, template_name=template_name, wamid=wamid, status="sent", **links)


def send_template(db: Session, to: str, tpl: WaTemplate, values: dict, *, body: str, **links) -> WaMessage:
    cfg = config(db)
    if cfg is None:
        raise WhatsAppError("WhatsApp isn't connected yet.")
    params = list(dict.fromkeys(re.findall(r"{{\s*(\w+)\s*}}", _template_text(db, tpl))))
    components = []
    if params:
        components = [{"type": "body", "parameters": [
            {"type": "text", "parameter_name": p, "text": (str(values.get(p) or "").strip() or "-")} for p in params]}]
    payload = {"type": "template", "template": {"name": tpl.meta_name, "language": {"code": cfg["language"]},
                                                "components": components}}
    return _send(db, cfg, to, payload, kind="template", body=body, template_name=tpl.meta_name, **links)


def _template_text(db: Session, tpl: WaTemplate) -> str:
    row = db.execute(select(MessageTemplate).where(
        MessageTemplate.segment == tpl.segment, MessageTemplate.key == tpl.key, MessageTemplate.version == tpl.version,
        MessageTemplate.language == "en")).scalars().first()
    return _body_for(row.body, tpl.variant) if row else ""


def send_text(db: Session, to: str, body: str, **links) -> WaMessage:
    cfg = config(db)
    if cfg is None:
        raise WhatsAppError("WhatsApp isn't connected yet.")
    payload = {"type": "text", "text": {"preview_url": False, "body": body}}
    return _send(db, cfg, to, payload, kind="text", body=body, template_name=None, **links)


def send_test(db: Session, to: str) -> WaMessage:
    cfg = config(db)
    if cfg is None:
        raise WhatsAppError("Save the phone number ID and access token first.")
    name, lang = TEST_TEMPLATE
    payload = {"type": "template", "template": {"name": name, "language": {"code": lang}}}
    return _send(db, cfg, to, payload, kind="template", body="WhatsApp test message (hello_world)", template_name=name)


def window_until(db: Session, phone: str) -> datetime | None:
    """When the free-text window closes for this number (None if closed)."""
    last = db.execute(select(func.max(InboundMessage.received_at)).where(
        InboundMessage.channel == "WhatsApp", InboundMessage.from_index == phone_index(phone))).scalar_one()
    if last is None:
        return None
    until = last + WINDOW
    return until if until > utcnow() else None


# ---------------------------------------------------------------- webhooks


def signature_ok(app_secret: str | None, raw: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:])


def handle_webhook(db: Session, payload: dict) -> dict:
    counts = {"messages": 0, "statuses": 0}
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts") or []}
            for msg in value.get("messages") or []:
                with db.begin_nested():
                    if _incoming(db, msg, names.get(msg.get("from"))) is not None:
                        counts["messages"] += 1
            for st in value.get("statuses") or []:
                if _status(db, st):
                    counts["statuses"] += 1
    note_webhook(db)
    db.flush()
    return counts


def _text_of(msg: dict) -> str:
    t = msg.get("type")
    if t == "text":
        return (msg.get("text") or {}).get("body") or ""
    if t == "button":
        return (msg.get("button") or {}).get("text") or ""
    if t == "interactive":
        i = msg.get("interactive") or {}
        return ((i.get("button_reply") or i.get("list_reply") or {}).get("title")) or "[interactive reply]"
    if t in ("image", "document", "audio", "video", "sticker"):
        media = msg.get(t) or {}
        caption = media.get("caption") or media.get("filename") or ""
        return f"[{t} received{': ' + caption if caption else ''}. Files aren't shown in the portal yet.]"
    if t == "location":
        return "[location shared]"
    return f"[{t or 'unknown'} message]"


def _match(db: Session, phone: str) -> tuple[str | None, str | None, str | None]:
    idx = phone_index(phone)
    last = db.execute(select(WaMessage).where(WaMessage.to_index == idx).order_by(WaMessage.created_at.desc()).limit(1)
                      ).scalar_one_or_none()
    if last is not None:
        if last.account_id:
            return "account", last.account_id, last.client_id
        if last.prospect_id:
            return "prospect", last.prospect_id, None
    account = db.execute(select(DebtorAccount).where(DebtorAccount.phone_index == idx)
                         .order_by(DebtorAccount.last_contacted_at.desc().nullslast(), DebtorAccount.created_at.desc())
                         .limit(1)).scalar_one_or_none()
    if account is not None:
        return "account", account.id, account.client_id
    prospect = db.execute(select(Prospect).where(Prospect.phone == phone).limit(1)).scalar_one_or_none()
    if prospect is not None:
        return "prospect", prospect.id, None
    return None, None, None


def _incoming(db: Session, msg: dict, name: str | None) -> InboundMessage | None:
    wamid = msg.get("id")
    if not wamid or db.execute(select(InboundMessage.id).where(InboundMessage.message_id == wamid)).first():
        return None
    phone = to_e164(msg.get("from") or "")
    if len(phone) < 8:
        return None
    try:
        received = datetime.fromtimestamp(int(msg.get("timestamp")), tz=timezone.utc)
    except (TypeError, ValueError):
        received = utcnow()
    party_type, party_id, client_id = _match(db, phone)
    reply_to = (msg.get("context") or {}).get("id")
    row = InboundMessage(channel="WhatsApp", from_address=phone, from_index=phone_index(phone), from_name=name,
                         subject=None, body=_text_of(msg)[:mailbox.MAX_BODY] or "(empty)", message_id=wamid[:255],
                         in_reply_to=(reply_to or None) and reply_to[:255], party_type=party_type, party_id=party_id,
                         client_id=client_id, received_at=received)
    db.add(row)
    if party_type == "account":
        mailbox._debtor_reply(db, row, party_id)
    elif party_type == "prospect":
        p = db.get(Prospect, party_id)
        if p is not None:
            db.add(ProspectTouch(prospect_id=p.id, channel="Reply", body=row.body[:4000], status="Logged"))
            if mailbox.is_no_thanks(row.body):
                p.status = "Do not contact"
                row.auto_action, row.handled_at = "do_not_contact", utcnow()
            elif p.status in ("New", "Contacted"):
                p.status = "Replied"
    db.flush()
    audit.record(db, SYSTEM, "inbox.received", "InboundMessage", row.id, client_id=client_id,
                 after={"channel": "WhatsApp", "matched": party_type, "auto": row.auto_action})
    return row


def _status(db: Session, st: dict) -> bool:
    row = db.execute(select(WaMessage).where(WaMessage.wamid == st.get("id"))).scalar_one_or_none()
    status = (st.get("status") or "").lower()
    if row is None or status not in STATUS_RANK:
        return False
    if status == "failed":
        err = (st.get("errors") or [{}])[0]
        row.status = "failed"
        row.error = _friendly(err.get("code"), (err.get("error_data") or {}).get("details") or err.get("title") or "Failed")[:500]
    elif STATUS_RANK[status] > STATUS_RANK.get(row.status, 0):
        row.status = status
    else:
        return False
    row.status_at = utcnow()
    return True
