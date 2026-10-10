"""Admin-editable configuration (fee default, holidays, payment terms ...),
stored as JSON in cl_config with code defaults for anything unset."""

import json
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
    "contact_whatsapp": "+234 000 000 0000",
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


def set_value(db: Session, key: str, value, actor_id: str | None = None) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
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
