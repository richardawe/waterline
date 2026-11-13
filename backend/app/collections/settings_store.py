"""Admin-editable configuration (fee default, holidays, payment terms ...),
stored as JSON in cl_config with code defaults for anything unset."""

import json
import re
from datetime import date

from sqlalchemy.orm import Session

from app.collections.models import ConfigEntry

DEFAULTS: dict = {
    "default_attribution_days": 60,
    "first_upload_row_cap": 500,
    "max_teammates": 3,
    "invoice_payment_terms_days": 14,
    "invoice_overdue_flag_days": 30,
    "proof_reminder_working_days": 5,
    "dispute_flag_working_days": 10,
    "abuse_rate_threshold": 0.15,
    "retention_months": 12,
    "email_fallback_enabled": True,
    "contact_email": "collections@waterline.ng",
    "contact_whatsapp": "+234 911 310 4482",
    # When true, messages can only be marked sent inside each segment's contact
    # window (baseline 08:00–19:00 WAT, Mon–Sat, no public holidays) and no queue
    # is built on Sundays/holidays. Off by owner decision (2026-10-10); the public
    # pages therefore make no fixed-hours promise. Turn on before lenders go live.
    "enforce_contact_window": False,
    # Internal team numbers that get the daily leads digest on WhatsApp
    # (template wl_internal_leads_digest_v1). The digest is never sent to any
    # other number. E.164, e.g. ["+2348031234567"]; Nigerian local formats are
    # accepted and normalised on save.
    "leads_digest_recipients": [],
    # Facebook Page posts (ops → Facebook): when true, Ops send posts to an
    # Admin for approval; when false, Ops publish directly. Admins always can.
    "facebook_posts_need_approval": True,
    "waterline_bank_details": {
        "bank_name": "To be configured",
        "account_number": "0000000000",
        "account_name": "Waterline",
    },
    # Nigerian public holidays — no outreach on these days. Moveable feasts
    # (Eid, Mawlid) are estimates; ops must confirm each year when the
    # Federal Government announces the dates.
    "public_holidays": [
        "2026-01-01", "2026-03-20", "2026-03-21", "2026-04-03", "2026-04-06", "2026-05-01",
        "2026-05-27", "2026-05-28", "2026-06-12", "2026-08-26", "2026-10-01", "2026-12-25", "2026-12-26",
        "2027-01-01", "2027-03-10", "2027-03-11", "2027-03-26", "2027-03-29", "2027-05-01",
        "2027-05-17", "2027-05-18", "2027-06-12", "2027-08-15", "2027-10-01", "2027-12-25", "2027-12-27",
    ],
}

PUBLIC_KEYS = ("contact_email", "contact_whatsapp")


def get(db: Session, key: str):
    row = db.get(ConfigEntry, key)
    if row is None:
        return DEFAULTS.get(key)
    return json.loads(row.value)


MAX_DIGEST_RECIPIENTS = 10


def _recipient(raw) -> str:
    from app.collections.phone import normalise_phone

    try:
        return normalise_phone(raw)
    except ValueError:
        digits = re.sub(r"[\s\-().]", "", str(raw or "").strip())
        if digits.startswith("+") and digits[1:].isdigit() and 8 <= len(digits) - 1 <= 15:
            return digits  # a non-Nigerian number in international format
        raise ValueError(f"'{raw}' isn't a phone number. Use the full international format, e.g. +2348031234567.")


def clean_value(key: str, value):
    """Checks and normalises values that have a fixed shape. Raises ValueError."""
    if key == "leads_digest_recipients":
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise ValueError("Enter a list of phone numbers, e.g. [\"+2348031234567\"].")
        out = list(dict.fromkeys(_recipient(v) for v in value))
        if len(out) > MAX_DIGEST_RECIPIENTS:
            raise ValueError(f"At most {MAX_DIGEST_RECIPIENTS} recipients.")
        return out
    if key == "facebook_posts_need_approval":
        if not isinstance(value, bool):
            raise ValueError("Enter true or false.")
    return value


def set_value(db: Session, key: str, value, actor_id: str | None = None) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    value = clean_value(key, value)
    row = db.get(ConfigEntry, key)
    if row is None:
        db.add(ConfigEntry(key=key, value=json.dumps(value), updated_by=actor_id))
    else:
        row.value = json.dumps(value)
        row.updated_by = actor_id


def all_values(db: Session) -> dict:
    return {key: get(db, key) for key in DEFAULTS}


def holidays(db: Session) -> set[date]:
    out = set()
    for value in get(db, "public_holidays") or []:
        try:
            out.add(date.fromisoformat(value))
        except ValueError:
            continue
    return out
