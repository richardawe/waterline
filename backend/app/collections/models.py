"""Collections data model. All money is integer kobo; all timestamps UTC
(displayed in Africa/Lagos). Tables are prefixed `cl_` to keep them apart
from the lender product's schema."""

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.collections.crypto import EncryptedString
from app.db import Base


# Same as app.models.base — redefined here because importing that module
# runs app/models/__init__.py, which itself imports this module.
def new_uuid() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ClientStatus:
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    SUSPENDED = "Suspended"


class AccountStatus:
    NEW = "New"
    ACTIVE = "Active"
    ON_PLAN = "On plan"
    PROOF_SUBMITTED = "Proof submitted"
    DISPUTED = "Disputed"
    PAID = "Paid"
    UNRESPONSIVE = "Unresponsive"
    PAUSED = "Paused"
    WITHDRAWN = "Withdrawn"

    ALL = [NEW, ACTIVE, ON_PLAN, PROOF_SUBMITTED, DISPUTED, PAID, UNRESPONSIVE, PAUSED, WITHDRAWN]
    CLOSED = {PAID, WITHDRAWN}


class Client(Base, TimestampMixin):
    __tablename__ = "cl_client"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    business_name: Mapped[str] = mapped_column(String(200), nullable=False)
    segment: Mapped[str] = mapped_column(String(20), nullable=False, default="school", index=True)  # see segments.py
    business_type: Mapped[str] = mapped_column(String(40), nullable=False)  # one of the segment's client_types
    state: Mapped[str] = mapped_column(String(60), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    logo_url: Mapped[Optional[str]] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ClientStatus.PENDING)
    fee_schedule: Mapped[str] = mapped_column(Text, nullable=False)  # JSON, see segments.validate_fee_schedule
    attribution_days: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    bank_name: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    account_number: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    account_name: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dpa_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    privacy_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verification_notes: Mapped[Optional[str]] = mapped_column(Text)
    verification_checklist: Mapped[Optional[str]] = mapped_column(Text)  # JSON {check: bool}
    status_reason: Mapped[Optional[str]] = mapped_column(Text)
    approved_by: Mapped[Optional[str]] = mapped_column(String(64))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    upload_row_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    default_rules: Mapped[Optional[str]] = mapped_column(Text)  # JSON, same shape as UploadBatch.rules
    flagged_reason: Mapped[Optional[str]] = mapped_column(Text)
    flagged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    profile_extra: Mapped[Optional[str]] = mapped_column(EncryptedString)  # JSON of segment client_fields (e.g. licence)

    users: Mapped[list["ClientUser"]] = relationship(back_populates="client")


class ClientUser(Base, TimestampMixin):
    __tablename__ = "cl_client_user"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("cl_client.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    email_index: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    phone: Mapped[Optional[str]] = mapped_column(EncryptedString)
    job_title: Mapped[Optional[str]] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="Owner")  # Owner / Member
    password_hash: Mapped[Optional[str]] = mapped_column(String(200))
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    client: Mapped[Client] = relationship(back_populates="users")


class OpsUser(Base, TimestampMixin):
    __tablename__ = "cl_ops_user"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="Ops")  # Ops / Admin
    totp_secret: Mapped[str] = mapped_column(EncryptedString, nullable=False)  # "" = no 2FA for this user
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    payout_details: Mapped[Optional[str]] = mapped_column(EncryptedString)  # JSON: bank_name, account_number, account_name


class AuthSession(Base):
    """Bearer session for a client or ops user. Only the token's hash is stored."""

    __tablename__ = "cl_session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    actor_type: Mapped[str] = mapped_column(String(10), nullable=False)  # client / ops
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ip: Mapped[Optional[str]] = mapped_column(String(64))


class AuthToken(Base):
    """One-time email link token: verify_email, reset_password, invite."""

    __tablename__ = "cl_auth_token"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("cl_client_user.id"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UploadBatch(Base, TimestampMixin):
    __tablename__ = "cl_upload_batch"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("cl_client.id"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="new")  # new accounts / update balances
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Preview")  # Preview / Imported / Discarded
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    staged_rows: Mapped[Optional[str]] = mapped_column(EncryptedString)  # JSON preview until import; cleared after
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(64))
    attested_by: Mapped[Optional[str]] = mapped_column(String(64))
    attested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attestation_text: Mapped[Optional[str]] = mapped_column(Text)
    rules: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    flagged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class DebtorAccount(Base, TimestampMixin):
    __tablename__ = "cl_debtor_account"
    __table_args__ = (Index("ix_cl_debtor_account_client_status", "client_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("cl_client.id"), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("cl_upload_batch.id"), nullable=False, index=True)
    reference_code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    debtor_name: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(EncryptedString)  # E.164
    phone_index: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # blind index of phone+description+amount
    external_reference: Mapped[Optional[str]] = mapped_column(EncryptedString)  # the client's own ID (admission no., loan ref…)
    external_ref_index: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # blind index, scoped by client
    extra: Mapped[Optional[str]] = mapped_column(EncryptedString)  # JSON of segment-specific columns
    email: Mapped[Optional[str]] = mapped_column(EncryptedString)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    dependant_first_name: Mapped[Optional[str]] = mapped_column(EncryptedString)
    original_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AccountStatus.NEW)
    opted_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    access_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    last_contacted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    contact_count_7d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    pending_notice: Mapped[Optional[str]] = mapped_column(String(40))  # e.g. DISPUTE_RESOLVED
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    anonymised_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    client: Mapped[Client] = relationship()
    batch: Mapped[UploadBatch] = relationship()


class PaymentPlan(Base, TimestampMixin):
    __tablename__ = "cl_payment_plan"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("cl_debtor_account.id"), nullable=False, index=True)
    instalment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    instalment_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schedule: Mapped[str] = mapped_column(Text, nullable=False)  # JSON [{"due": "YYYY-MM-DD", "amount": kobo}]
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Active")  # Active / Completed / Broken


class DebtorPayment(Base, TimestampMixin):
    __tablename__ = "cl_payment"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("cl_debtor_account.id"), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("cl_client.id"), nullable=False, index=True)
    amount_claimed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_confirmed: Mapped[Optional[int]] = mapped_column(BigInteger)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    sender_name: Mapped[Optional[str]] = mapped_column(EncryptedString)
    proof_file_url: Mapped[Optional[str]] = mapped_column(String(300))  # storage key, never a public URL
    proof_file_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    possible_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # Debtor proof / Client recorded
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # Proof submitted / Confirmed / Rejected / Reversed
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(64))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[Optional[str]] = mapped_column(String(200))
    fee_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fee_percent: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))  # rate applied, from the client's schedule
    fee_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reminder_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Dispute(Base, TimestampMixin):
    __tablename__ = "cl_dispute"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("cl_debtor_account.id"), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("cl_client.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # Already paid / Wrong amount / Not my debt / Other
    details: Mapped[Optional[str]] = mapped_column(Text)
    evidence_url: Mapped[Optional[str]] = mapped_column(String(300))
    raised_by: Mapped[str] = mapped_column(String(10), nullable=False)  # Debtor / Ops
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Open")  # Open / Upheld / Adjusted / Withdrawn
    resolution_note: Mapped[Optional[str]] = mapped_column(Text)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(64))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class OutreachItem(Base, TimestampMixin):
    __tablename__ = "cl_outreach_item"
    __table_args__ = (UniqueConstraint("account_id", "queue_date", name="uq_cl_outreach_account_day"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("cl_debtor_account.id"), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("cl_client.id"), nullable=False, index=True)
    template_key: Mapped[str] = mapped_column(String(40), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    context_key: Mapped[Optional[str]] = mapped_column(String(60))  # e.g. "inst:2:before" for plan reminders
    rendered_text: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    channel: Mapped[Optional[str]] = mapped_column(String(10))  # WhatsApp / SMS / Email, set when sent
    queue_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="Queued")  # Queued / Sent / Skipped
    sent_by: Mapped[Optional[str]] = mapped_column(String(64))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    skip_reason: Mapped[Optional[str]] = mapped_column(String(120))
    reply_category: Mapped[Optional[str]] = mapped_column(String(40))
    reply_note: Mapped[Optional[str]] = mapped_column(Text)
    render_values: Mapped[Optional[str]] = mapped_column(EncryptedString)  # JSON; WhatsApp template parameters
    replied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Invoice(Base, TimestampMixin):
    __tablename__ = "cl_invoice"
    __table_args__ = (UniqueConstraint("client_id", "period_start", name="uq_cl_invoice_client_period"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("cl_client.id"), nullable=False, index=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    total_confirmed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    fee_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)  # effective rate (fee ÷ recovered)
    fee_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="Draft")  # Draft / Issued / Paid / Overdue
    issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[Optional[date]] = mapped_column(Date)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    flagged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class InvoiceLine(Base):
    """A snapshot of one fee-eligible payment at drafting time, so an issued
    invoice never changes if the underlying account is later anonymised."""

    __tablename__ = "cl_invoice_line"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("cl_invoice.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("cl_payment.id"), nullable=False, unique=True)
    reference_code: Mapped[str] = mapped_column(String(16), nullable=False)
    debtor_name: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    fee_percent: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    fee: Mapped[int] = mapped_column(BigInteger, nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class MessageTemplate(Base):
    """Versioned message templates. Only the active row per (key, language)
    is used; edits insert a new version rather than overwriting."""

    __tablename__ = "cl_message_template"
    __table_args__ = (UniqueConstraint("segment", "key", "language", "version", name="uq_cl_template_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    segment: Mapped[str] = mapped_column(String(20), nullable=False, default="school")
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SegmentSetting(Base):
    """Ops-controlled state of a client segment. Rows are optional: a segment
    with no row uses its code default (see segments.py)."""

    __tablename__ = "cl_segment"

    key: Mapped[str] = mapped_column(String(20), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # hidden / waitlist / live
    policy_overrides: Mapped[Optional[str]] = mapped_column(Text)  # JSON, stricter-only
    default_fee_schedule: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    policy_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    policy_reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    updated_by: Mapped[Optional[str]] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FeeProposal(Base, TimestampMixin):
    """A client's proposed success-fee schedule, approved or rejected by a
    Waterline Admin. Approval replaces the client's schedule for payments
    confirmed from then on."""

    __tablename__ = "cl_fee_proposal"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("cl_client.id"), nullable=False, index=True)
    schedule: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    note: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="Pending")  # Pending / Approved / Rejected / Withdrawn
    proposed_by: Mapped[Optional[str]] = mapped_column(String(64))
    decided_by: Mapped[Optional[str]] = mapped_column(String(64))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[Optional[str]] = mapped_column(Text)


class WaitlistEntry(Base, TimestampMixin):
    """Interest from a segment that isn't live yet (lenders, gyms …)."""

    __tablename__ = "cl_waitlist"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    segment: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    business_name: Mapped[str] = mapped_column(String(200), nullable=False)
    business_type: Mapped[Optional[str]] = mapped_column(String(60))
    contact_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(EncryptedString)
    note: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="New")  # New / Contacted / Invited


class ConfigEntry(Base):
    __tablename__ = "cl_config"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    updated_by: Mapped[Optional[str]] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EmailMessage(Base):
    """Outbox/log of every email the system sends (notifications and debtor
    email outreach). Bodies may contain personal data, so they're encrypted."""

    __tablename__ = "cl_email"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    to_address: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    client_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="Queued")  # Queued / Sent / Failed / Logged
    error: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Threading: replies quote our Message-ID, and to_index (blind index of the
    # recipient) lets a reply without one still be matched to who we wrote to.
    message_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    to_index: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    account_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    prospect_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    sent_by: Mapped[Optional[str]] = mapped_column(String(64))  # ops user id when a person sent it


class Channel(Base):
    """Connection settings for a messaging channel ("email" now, "whatsapp"
    later), entered by an Admin in ops → Channels. Secrets live encrypted in
    `settings`; `state` is non-secret bookkeeping (last check, last error)."""

    __tablename__ = "cl_channel"

    key: Mapped[str] = mapped_column(String(20), primary_key=True)
    settings: Mapped[Optional[str]] = mapped_column(EncryptedString)  # JSON
    state: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    updated_by: Mapped[Optional[str]] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class InboundMessage(Base):
    """A message someone sent us, pulled from the collections mailbox (or,
    later, WhatsApp). Matched to a party where possible: party_type is
    "account" (a debtor), "prospect" or "client"; None means unmatched."""

    __tablename__ = "cl_inbound"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)  # Email / WhatsApp
    from_address: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    from_index: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_name: Mapped[Optional[str]] = mapped_column(EncryptedString)
    subject: Mapped[Optional[str]] = mapped_column(EncryptedString)
    body: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    attachments: Mapped[Optional[str]] = mapped_column(Text)  # JSON list of file names (files stay in the mailbox)
    message_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    in_reply_to: Mapped[Optional[str]] = mapped_column(String(255))
    party_type: Mapped[Optional[str]] = mapped_column(String(10))
    party_id: Mapped[Optional[str]] = mapped_column(String(64))
    client_id: Mapped[Optional[str]] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    handled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    handled_by: Mapped[Optional[str]] = mapped_column(String(64))
    auto_action: Mapped[Optional[str]] = mapped_column(String(40))  # e.g. "opted_out"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Append-only. Nothing in the app updates or deletes these rows."""

    __tablename__ = "cl_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(String(10), nullable=False)  # client / ops / debtor / system
    actor_id: Mapped[Optional[str]] = mapped_column(String(64))
    client_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    entity: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64))
    before: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    after: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# ---------------------------------------------------------------- prospecting (Waterline's own sales outreach)


class ProspectList(Base, TimestampMixin):
    """A named batch of organisations we plan to approach, e.g. "First 20 Prospects"."""

    __tablename__ = "cl_prospect_list"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)


class Prospect(Base, TimestampMixin):
    """An organisation Waterline might sign up. Contact details are the
    organisation's own published business contacts (official addresses and
    lines, never personal ones), so they're stored in plain text and can be
    seeded by migration. A named head is only used in a greeting once ops
    have confirmed it (`contact_confirmed`)."""

    __tablename__ = "cl_prospect"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    list_id: Mapped[Optional[str]] = mapped_column(ForeignKey("cl_prospect_list.id"), index=True)
    segment: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    org_name: Mapped[str] = mapped_column(String(200), nullable=False)
    org_type: Mapped[Optional[str]] = mapped_column(String(300))
    city: Mapped[Optional[str]] = mapped_column(String(80))
    address: Mapped[Optional[str]] = mapped_column(String(300))
    contact_name: Mapped[Optional[str]] = mapped_column(String(160))
    contact_role: Mapped[Optional[str]] = mapped_column(String(120))
    contact_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email: Mapped[Optional[str]] = mapped_column(String(254))
    phone: Mapped[Optional[str]] = mapped_column(String(20))  # E.164
    phone_note: Mapped[Optional[str]] = mapped_column(String(120))
    fit: Mapped[Optional[str]] = mapped_column(String(10))  # High / Medium / Low
    fit_note: Mapped[Optional[str]] = mapped_column(String(300))
    source: Mapped[Optional[str]] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="New", index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    next_follow_up: Mapped[Optional[date]] = mapped_column(Date)
    last_contacted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ProspectTouch(Base):
    """Every message, call, reply or note on a prospect."""

    __tablename__ = "cl_prospect_touch"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("cl_prospect.id", ondelete="CASCADE"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)  # Email / WhatsApp / Call / Note / Reply
    template_key: Mapped[Optional[str]] = mapped_column(String(20))  # intro / follow_up / custom
    subject: Mapped[Optional[str]] = mapped_column(String(300))
    body: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="Sent")  # Sent / Failed / Logged
    error: Mapped[Optional[str]] = mapped_column(Text)
    ops_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    # set in Python, not by now(): several touches in one transaction must still be ordered
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=lambda: datetime.now(timezone.utc))


class WaTemplate(Base):
    """One of our message templates as submitted to WhatsApp (Meta) for
    approval. variant "nd" is the same text without {{discount_line}}, because
    WhatsApp won't accept an empty parameter."""

    __tablename__ = "cl_wa_template"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    segment: Mapped[str] = mapped_column(String(20), nullable=False)
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    variant: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    meta_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    meta_id: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="NOT_SUBMITTED")
    reason: Mapped[Optional[str]] = mapped_column(Text)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WaMessage(Base):
    """A WhatsApp message the portal sent. status follows Meta's webhooks:
    sent → delivered → read, or failed."""

    __tablename__ = "cl_wa_message"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    wamid: Mapped[Optional[str]] = mapped_column(String(200), unique=True)
    to_phone: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    to_index: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    prospect_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(String(64))
    outreach_item_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # template / text
    template_name: Mapped[Optional[str]] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # sent / delivered / read / failed
    error: Mapped[Optional[str]] = mapped_column(Text)
    sent_by: Mapped[Optional[str]] = mapped_column(String(64))
    status_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Expense(Base):
    """Money a team member spent for Waterline and wants paid back.
    Submitted → Approved → Paid, or Rejected. Amounts in kobo."""

    __tablename__ = "cl_expense"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    ops_user_id: Mapped[str] = mapped_column(ForeignKey("cl_ops_user.id"), nullable=False, index=True)
    spent_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    receipt_key: Mapped[Optional[str]] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="Submitted", index=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(64))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)
    paid_by: Mapped[Optional[str]] = mapped_column(String(64))
    paid_on: Mapped[Optional[date]] = mapped_column(Date)
    paid_reference: Mapped[Optional[str]] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FbPost(Base):
    """A post to the Waterline Collections Facebook Page, written in the ops
    console. Draft → (Pending approval → ) Scheduled / Published, or
    Rejected / Failed / Cancelled. Scheduling is done by Facebook itself
    (published=false + scheduled_publish_time), so no cron is needed."""

    __tablename__ = "cl_fb_post"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[Optional[str]] = mapped_column(String(500))
    image_key: Mapped[Optional[str]] = mapped_column(String(200))
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Draft", index=True)
    fb_post_id: Mapped[Optional[str]] = mapped_column(String(100))
    permalink: Mapped[Optional[str]] = mapped_column(String(500))
    error: Mapped[Optional[str]] = mapped_column(Text)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    approved_by: Mapped[Optional[str]] = mapped_column(String(64))
    published_by: Mapped[Optional[str]] = mapped_column(String(64))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
