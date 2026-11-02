"""The collections mailbox: connection settings (ops → Channels) and pulling
replies into the portal's Inbox.

Settings live in cl_channel["email"], encrypted. The same login is used for
sending (SMTP) and reading (IMAP), which is how cPanel mailboxes work.

Reading is read-only: the mailbox is opened with EXAMINE and messages are
fetched with BODY.PEEK, so nothing is marked read, moved or deleted, and
webmail keeps working as a backup. The first check only pulls the last 14
days; after that it remembers the last IMAP UID it saw.

Matching a message to a person, in order:
1. In-Reply-To / References naming a Message-ID we sent (cl_email.message_id).
2. The sender's address matching someone we emailed (cl_email.to_index).
3. A prospect's published email, or a client user's login email.
Anything else is "unmatched" and shows under its own filter in the Inbox.

Automatic actions on a matched message:
- A debtor whose reply is just "STOP" (or "unsubscribe") is opted out on
  every channel, exactly as if ops had logged "Asks to stop".
- Any other debtor reply counts as a response (resets Unresponsive).
- A prospect reply is logged as a Reply touch; "no thanks" marks the
  organisation Do not contact, as the prospect footer promises.
- Auto-replies (out-of-office) are stored but marked handled."""

import email
import html
import imaplib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import parseaddr, parsedate_to_datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.collections import audit, crypto
from app.collections.audit import SYSTEM
from app.collections.models import (
    Channel,
    ClientUser,
    DebtorAccount,
    EmailMessage,
    InboundMessage,
    Prospect,
    ProspectTouch,
)
from app.collections.timeutil import utcnow

logger = logging.getLogger(__name__)

KEY = "email"
DEFAULTS = {
    "address": "collections@waterline.ng",
    "from_name": "Waterline Collections",
    "smtp_host": "mail.waterline.ng",
    "smtp_port": 465,
    "smtp_ssl": True,
    "imap_host": "mail.waterline.ng",
    "imap_port": 993,
}
FIRST_CHECK_DAYS = 14
MAX_PER_CHECK = 100
MAX_BODY = 20000
AUTO_CHECK_SECONDS = 120
_LOCK_ID = 7_302_211  # pg advisory lock: one mailbox check at a time across workers

STOP_WORDS = {"stop", "unsubscribe", "stop messages", "please stop", "stop please", "stop all messages"}
NO_THANKS = {"no thanks", "no thank you", "not interested", "no thanks.", "unsubscribe", "stop"}


# ---------------------------------------------------------------- settings


def _row(db: Session) -> Channel | None:
    return db.get(Channel, KEY)


def stored_settings(db: Session) -> dict:
    row = _row(db)
    return json.loads(row.settings) if row and row.settings else {}


def state(db: Session) -> dict:
    row = _row(db)
    return json.loads(row.state) if row and row.state else {}


def _set_state(db: Session, **changes) -> None:
    row = _row(db)
    if row is None:
        row = Channel(key=KEY)
        db.add(row)
    current = json.loads(row.state) if row.state else {}
    current.update(changes)
    row.state = json.dumps(current, default=str)


def public_settings(db: Session) -> dict:
    """What the Channels page shows: everything but the password."""
    s = {**DEFAULTS, **stored_settings(db)}
    has_password = bool(s.pop("password", None))
    return {**s, "has_password": has_password, "source": source(db)}


def source(db: Session) -> str:
    if stored_settings(db).get("password"):
        return "portal"
    from app.config import get_settings

    return "server" if get_settings().smtp_host else "none"


def save_settings(db: Session, data: dict, actor_id: str) -> None:
    current = stored_settings(db)
    new = {**DEFAULTS, **current}
    for key in ("address", "from_name", "smtp_host", "imap_host"):
        if key in data and data[key] is not None:
            new[key] = str(data[key]).strip()[:200]
    for key in ("smtp_port", "imap_port"):
        if data.get(key):
            new[key] = int(data[key])
    if "smtp_ssl" in data:
        new["smtp_ssl"] = bool(data["smtp_ssl"])
    if data.get("password"):  # blank keeps the saved one
        new["password"] = str(data["password"])
    new["address"] = new["address"].lower()
    row = _row(db)
    if row is None:
        row = Channel(key=KEY)
        db.add(row)
    row.settings = json.dumps(new)
    row.updated_by = actor_id


def sending_config(db: Session) -> dict | None:
    """SMTP settings to send with, or None when nothing is configured."""
    s = stored_settings(db)
    if s.get("password"):
        s = {**DEFAULTS, **s}
        from app.collections.emailer import display_from

        return {"smtp_host": s["smtp_host"], "smtp_port": s["smtp_port"], "smtp_ssl": s["smtp_ssl"],
                "username": s["address"], "password": s["password"], "from": display_from(s["from_name"], s["address"])}
    from app.config import get_settings

    env = get_settings()
    if not env.smtp_host:
        return None
    return {"smtp_host": env.smtp_host, "smtp_port": env.smtp_port, "smtp_ssl": env.smtp_use_ssl,
            "username": env.smtp_username, "password": env.smtp_password, "from": env.smtp_from}


def reading_config(db: Session) -> dict | None:
    s = stored_settings(db)
    if not s.get("password"):
        return None
    s = {**DEFAULTS, **s}
    return {"host": s["imap_host"], "port": s["imap_port"], "username": s["address"], "password": s["password"]}


# ---------------------------------------------------------------- parsing


def _plain_body(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except Exception:  # undecodable charset: fall back to raw bytes
        content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        content = re.sub(r"(?is)<(script|style).*?</\1>", "", content)
        content = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", content)
        content = html.unescape(re.sub(r"<[^>]+>", "", content))
    content = re.sub(r"\r\n?", "\n", content)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return content[:MAX_BODY]


_QUOTE_START = re.compile(r"^(On .{0,200}wrote:|-{2,}\s*Original Message\s*-{2,}|From: .+|Sent from my .+)$", re.I)


def reply_text(body: str) -> str:
    """The new part of a reply: everything above the quoted original."""
    lines = []
    for line in (body or "").split("\n"):
        stripped = line.strip()
        if stripped.startswith(">") or _QUOTE_START.match(stripped):
            break
        lines.append(line)
    return "\n".join(lines).strip() or (body or "").strip()


def _first_line(body: str) -> str:
    for line in reply_text(body).split("\n"):
        if line.strip():
            return re.sub(r"[^\w\s]", "", line).strip().lower()
    return ""


def is_stop(body: str) -> bool:
    return _first_line(body) in STOP_WORDS


def is_no_thanks(body: str) -> bool:
    return _first_line(body) in {re.sub(r"[^\w\s]", "", w) for w in NO_THANKS}


def _ids(value: str | None) -> list[str]:
    return re.findall(r"<[^<>\s]+>", value or "")


# ---------------------------------------------------------------- matching


def _match(db: Session, from_addr: str, refs: list[str]) -> tuple[str | None, str | None, str | None]:
    """→ (party_type, party_id, client_id)"""
    sent = None
    if refs:
        sent = db.execute(
            select(EmailMessage).where(EmailMessage.message_id.in_(refs)).order_by(EmailMessage.created_at.desc()).limit(1)
        ).scalar_one_or_none()
    if sent is None:
        sent = db.execute(
            select(EmailMessage).where(EmailMessage.to_index == crypto.blind_index(from_addr))
            .order_by(EmailMessage.created_at.desc()).limit(1)
        ).scalar_one_or_none()
    if sent is not None:
        if sent.account_id:
            return "account", sent.account_id, sent.client_id
        if sent.prospect_id:
            return "prospect", sent.prospect_id, None
        if sent.client_id:
            return "client", sent.client_id, sent.client_id
    prospect = db.execute(select(Prospect).where(func.lower(Prospect.email) == from_addr).limit(1)).scalar_one_or_none()
    if prospect is not None:
        return "prospect", prospect.id, None
    user = db.execute(
        select(ClientUser).where(ClientUser.email_index == crypto.blind_index(from_addr))
    ).scalar_one_or_none()
    if user is not None:
        return "client", user.client_id, user.client_id
    return None, None, None


def process_raw(db: Session, raw: bytes, own_address: str | None = None) -> InboundMessage | None:
    """Store one raw email, match it and apply automatic actions. Returns
    None for a duplicate or for our own mail."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    message_id = (msg.get("Message-ID") or "").strip()[:255] or None
    if message_id and db.execute(select(InboundMessage.id).where(InboundMessage.message_id == message_id)).first():
        return None
    from_name, from_addr = parseaddr(str(msg.get("From") or ""))
    from_addr = from_addr.strip().lower()
    if not from_addr or (own_address and from_addr == own_address.lower()):
        return None
    try:
        received = parsedate_to_datetime(str(msg.get("Date")))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
    except Exception:
        received = utcnow()
    body = _plain_body(msg) or "(no text)"
    attachments = [p.get_filename() or "attachment" for p in msg.iter_attachments()]
    refs = _ids(msg.get("In-Reply-To")) + _ids(msg.get("References"))
    party_type, party_id, client_id = _match(db, from_addr, refs)

    row = InboundMessage(
        channel="Email", from_address=from_addr, from_index=crypto.blind_index(from_addr),
        from_name=(from_name or None), subject=str(msg.get("Subject") or "")[:300] or None, body=body,
        attachments=json.dumps(attachments) if attachments else None, message_id=message_id,
        in_reply_to=refs[0] if refs else None, party_type=party_type, party_id=party_id, client_id=client_id,
        received_at=received,
    )
    db.add(row)

    auto = str(msg.get("Auto-Submitted") or "no").lower()
    if auto != "no" or str(msg.get("X-Autoreply") or "") or "out of office" in (row.subject or "").lower():
        row.auto_action, row.handled_at = "auto_reply", utcnow()
    elif party_type == "account":
        _debtor_reply(db, row, party_id)
    elif party_type == "prospect":
        _prospect_reply(db, row, party_id)
    db.flush()
    audit.record(db, SYSTEM, "inbox.received", "InboundMessage", row.id, client_id=client_id,
                 after={"channel": "Email", "matched": party_type, "auto": row.auto_action})
    return row


def _debtor_reply(db: Session, row: InboundMessage, account_id: str) -> None:
    from app.collections import service

    account = db.get(DebtorAccount, account_id)
    if account is None:
        return
    if is_stop(row.body):
        service.opt_out(db, account, SYSTEM, via="email reply")
        row.auto_action, row.handled_at = "opted_out", utcnow()
    else:
        service.mark_response(account)


def _prospect_reply(db: Session, row: InboundMessage, prospect_id: str) -> None:
    p = db.get(Prospect, prospect_id)
    if p is None:
        return
    db.add(ProspectTouch(prospect_id=p.id, channel="Reply", body=reply_text(row.body)[:4000], status="Logged"))
    if is_no_thanks(row.body):
        p.status = "Do not contact"
        row.auto_action, row.handled_at = "do_not_contact", utcnow()
    elif p.status in ("New", "Contacted"):
        p.status = "Replied"


# ---------------------------------------------------------------- IMAP


def _imap(cfg: dict):
    conn = imaplib.IMAP4_SSL(cfg["host"], int(cfg["port"]), timeout=20)
    conn.login(cfg["username"], cfg["password"])
    return conn


def test_reading(db: Session) -> dict:
    cfg = reading_config(db)
    if cfg is None:
        return {"ok": False, "error": "Save the mailbox password first."}
    try:
        conn = _imap(cfg)
        try:
            typ, data = conn.select("INBOX", readonly=True)
            count = int(data[0]) if typ == "OK" and data and data[0] else 0
        finally:
            conn.logout()
        return {"ok": True, "messages": count}
    except Exception as exc:
        return {"ok": False, "error": _friendly(exc)}


def _friendly(exc: Exception) -> str:
    msg = str(exc)
    if "AUTHENTICATIONFAILED" in msg.upper() or "authentication failed" in msg.lower() or "Invalid credentials" in msg:
        return "The mailbox rejected the email address or password."
    return msg[:300] or exc.__class__.__name__


def check_mailbox(db: Session, *, if_older_than: int | None = None) -> dict:
    """Pull new mail into cl_inbound. The caller commits."""
    cfg = reading_config(db)
    if cfg is None:
        return {"ok": False, "checked": False, "error": "The mailbox isn't connected yet."}
    st = state(db)
    if if_older_than and st.get("last_check_at"):
        last = datetime.fromisoformat(st["last_check_at"])
        if utcnow() - last < timedelta(seconds=if_older_than):
            return {"ok": True, "checked": False, "new": 0}
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        if not db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _LOCK_ID}).scalar():
            return {"ok": True, "checked": False, "new": 0}
    new = 0
    try:
        conn = _imap(cfg)
        try:
            typ, data = conn.select("INBOX", readonly=True)
            if typ != "OK":
                raise RuntimeError("Couldn't open the inbox.")
            validity = (conn.untagged_responses.get("UIDVALIDITY") or [b"0"])[0]
            validity = validity.decode() if isinstance(validity, bytes) else str(validity)
            last_uid = int(st.get("last_uid") or 0) if st.get("uidvalidity") == validity else 0
            if last_uid:
                typ, data = conn.uid("SEARCH", None, f"UID {last_uid + 1}:*")
            else:
                since = (utcnow() - timedelta(days=FIRST_CHECK_DAYS)).strftime("%d-%b-%Y")
                typ, data = conn.uid("SEARCH", None, f"SINCE {since}")
            uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > last_uid)[:MAX_PER_CHECK]
            for uid in uids:
                typ, parts = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
                if raw:
                    try:
                        with db.begin_nested():
                            if process_raw(db, raw, own_address=cfg["username"]) is not None:
                                new += 1
                    except Exception as exc:  # one bad message must not block the rest
                        logger.warning("collections inbox: couldn't store UID %s: %s", uid, exc)
                last_uid = uid
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        _set_state(db, last_check_at=utcnow().isoformat(), last_ok_at=utcnow().isoformat(), last_error=None,
                   last_uid=last_uid, uidvalidity=validity)
        return {"ok": True, "checked": True, "new": new}
    except Exception as exc:
        err = _friendly(exc)
        logger.warning("collections inbox check failed: %s", err)
        _set_state(db, last_check_at=utcnow().isoformat(), last_error=err)
        return {"ok": False, "checked": True, "new": new, "error": err}
