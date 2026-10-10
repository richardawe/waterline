"""Builders for Collections tests: seed clients, users, accounts and sent
messages straight into the (rolled-back) test session."""

import json
from datetime import date, datetime, time, timedelta

from app.collections import crypto, totp
from app.collections.auth import create_session
from app.collections.models import (
    AccountStatus,
    Client,
    ClientStatus,
    ClientUser,
    DebtorAccount,
    OpsUser,
    OutreachItem,
    UploadBatch,
)
from app.collections.passwords import hash_password
from app.collections.timeutil import lagos_datetime, utcnow

PASSWORD = "correct-horse-9"


def wat(d: date, hour: int = 10, minute: int = 0) -> datetime:
    """A WAT wall-clock moment as aware UTC."""
    return lagos_datetime(d, time(hour, minute))


SEGMENT_TYPES = {"school": "Secondary school", "estate": "Estate management", "lender": "Microfinance bank",
                 "service": "Gym or club"}


def make_client(db, name="Greenfield School", status=ClientStatus.APPROVED, email=None, segment="school",
                **kw) -> tuple[Client, ClientUser]:
    now = utcnow()
    fee_schedule = kw.pop("fee_schedule", None) or {"type": "flat", "percent": kw.pop("fee_percent", 20)}
    client = Client(
        business_name=name, segment=segment, business_type=SEGMENT_TYPES[segment], state="Lagos", city="Ikeja",
        status=status, fee_schedule=json.dumps(fee_schedule), attribution_days=kw.pop("attribution_days", 60),
        bank_name="GTBank", account_number="0123456789", account_name=name,
        terms_accepted_at=now, dpa_accepted_at=now, privacy_accepted_at=now, **kw,
    )
    db.add(client)
    db.flush()
    email = email or f"bursar-{client.id[:8]}@example.com"
    user = ClientUser(
        client_id=client.id, name="Bola Bursar", email=email, email_index=crypto.blind_index(email),
        phone="+2348030000000", role="Owner", password_hash=hash_password(PASSWORD), email_verified_at=now,
    )
    db.add(user)
    db.flush()
    return client, user


def client_headers(db, user: ClientUser) -> dict:
    token = create_session(db, "client", user.id, None)
    db.flush()
    return {"Authorization": f"Bearer {token}"}


def make_ops(db, role="Admin") -> tuple[OpsUser, dict]:
    secret = totp.new_secret()
    ops = OpsUser(name="Olu Ops", email=f"ops-{secret[:6].lower()}@waterline.ng", password_hash=hash_password(PASSWORD),
                  role=role, totp_secret=secret)
    db.add(ops)
    db.flush()
    token = create_session(db, "ops", ops.id, None)
    db.flush()
    return ops, {"Authorization": f"Bearer {token}"}


def make_batch(db, client: Client, rules: dict | None = None) -> UploadBatch:
    batch = UploadBatch(client_id=client.id, file_name="balances.csv", status="Imported", row_count=1, valid_count=1,
                        imported_count=1, attested_at=utcnow(), rules=json.dumps(rules) if rules else None)
    db.add(batch)
    db.flush()
    return batch


_counter = [0]


def make_account(db, client: Client, batch: UploadBatch | None = None, *, balance=150_000_00, status=AccountStatus.ACTIVE,
                 email=None, phone="+2348031234567", name="Adaeze Okafor") -> DebtorAccount:
    batch = batch or make_batch(db, client)
    _counter[0] += 1
    account = DebtorAccount(
        client_id=client.id, batch_id=batch.id, reference_code=f"WL-T{_counter[0]:05d}",
        debtor_name=name, phone=phone, phone_index=crypto.blind_index(phone), email=email,
        description="School fees – 2nd term 2025/26", dependant_first_name="Tolu",
        original_amount=balance, balance=balance, status=status, access_token=crypto.new_token(),
    )
    db.add(account)
    db.flush()
    batch.imported_count = (batch.imported_count or 0) + 1
    return account


def add_sent(db, account: DebtorAccount, when: datetime, key="INTRO", channel="WhatsApp") -> OutreachItem:
    from app.collections.timeutil import lagos_date

    item = OutreachItem(
        account_id=account.id, client_id=account.client_id, template_key=key, rendered_text="hello",
        channel=channel, queue_date=lagos_date(when), scheduled_for=when, status="Sent", sent_at=when,
    )
    db.add(item)
    db.flush()
    return item


def a_weekday(after: date | None = None) -> date:
    """A Monday that's not a configured public holiday."""
    d = after or date(2026, 11, 2)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d
