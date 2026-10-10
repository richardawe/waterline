"""Transactional email over SMTP (cPanel mail or any provider's SMTP relay,
e.g. Postmark/Resend, on a separate sending subdomain). Every message is
logged to cl_email first; with no SMTP configured (local dev, tests) it's
logged only."""

import logging
import smtplib
from email.message import EmailMessage as MimeMessage

from sqlalchemy.orm import Session

from app.collections.models import EmailMessage
from app.collections.timeutil import utcnow
from app.config import get_settings

logger = logging.getLogger(__name__)


def _deliver(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    msg = MimeMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    smtp_cls = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
    with smtp_cls(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if not settings.smtp_use_ssl:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(msg)


def send(db: Session, to: str | None, subject: str, body: str, *, kind: str, client_id: str | None = None) -> EmailMessage | None:
    if not to:
        return None
    row = EmailMessage(to_address=to, subject=subject, body=body, kind=kind, client_id=client_id)
    db.add(row)
    if not get_settings().smtp_host:
        row.status = "Logged"
        logger.info("collections email (not sent, SMTP unconfigured): %s — %s", kind, subject)
        return row
    try:
        _deliver(to, subject, body)
        row.status, row.sent_at = "Sent", utcnow()
    except Exception as exc:  # a mail outage must not roll back the business action
        row.status, row.error = "Failed", str(exc)[:500]
        logger.warning("collections email failed: %s", exc)
    return row


def site_url(path: str) -> str:
    return get_settings().collections_site_base_url.rstrip("/") + path
