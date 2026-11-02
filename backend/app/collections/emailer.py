"""Transactional email over SMTP. Every message is logged to cl_email first;
with no mail server configured (local dev, tests) it's logged only.

Where the SMTP login comes from, in order:
1. ops → Channels (an Admin enters the collections mailbox login; stored
   encrypted in cl_channel). Mail then goes out From that mailbox, so
   replies land where the portal's Inbox reads them.
2. The server's SMTP_* environment variables (the original setup).

Each message gets its own Message-ID so a reply can be threaded back to
the debtor, prospect or client it was sent to (see mailbox.py)."""

import logging
import smtplib
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, make_msgid

from sqlalchemy.orm import Session

from app.collections import crypto, mailbox
from app.collections.models import EmailMessage
from app.collections.timeutil import utcnow

logger = logging.getLogger(__name__)


def _deliver(cfg: dict, to: str, subject: str, body: str, *, message_id: str, reply_to: str | None = None,
             in_reply_to: str | None = None) -> None:
    msg = MimeMessage()
    msg["From"] = cfg["from"]
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    smtp_cls = smtplib.SMTP_SSL if cfg["smtp_ssl"] else smtplib.SMTP
    with smtp_cls(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=20) as smtp:
        if not cfg["smtp_ssl"]:
            smtp.starttls()
        if cfg.get("username"):
            smtp.login(cfg["username"], cfg.get("password") or "")
        smtp.send_message(msg)


def send(db: Session, to: str | None, subject: str, body: str, *, kind: str, client_id: str | None = None,
         reply_to: str | None = None, account_id: str | None = None, prospect_id: str | None = None,
         in_reply_to: str | None = None, sent_by: str | None = None) -> EmailMessage | None:
    if not to:
        return None
    to = to.strip()
    message_id = make_msgid(domain="waterline.ng")
    row = EmailMessage(to_address=to, subject=subject, body=body, kind=kind, client_id=client_id,
                       message_id=message_id, to_index=crypto.blind_index(to), account_id=account_id,
                       prospect_id=prospect_id, sent_by=sent_by)
    db.add(row)
    cfg = mailbox.sending_config(db)
    if cfg is None:
        row.status = "Logged"
        logger.info("collections email (not sent, SMTP unconfigured): %s — %s", kind, subject)
        return row
    try:
        _deliver(cfg, to, subject, body, message_id=message_id, reply_to=reply_to, in_reply_to=in_reply_to)
        row.status, row.sent_at = "Sent", utcnow()
    except Exception as exc:  # a mail outage must not roll back the business action
        row.status, row.error = "Failed", str(exc)[:500]
        logger.warning("collections email failed: %s", exc)
    return row


def site_url(path: str) -> str:
    from app.config import get_settings

    return get_settings().collections_site_base_url.rstrip("/") + path


def display_from(name: str, address: str) -> str:
    return formataddr((name, address)) if name else address
