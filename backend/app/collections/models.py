"""Collections data model. All money is integer kobo; all timestamps UTC
(displayed in Africa/Lagos). Tables are prefixed `cl_` to keep them apart
from the lender product's schema."""

import uuid
from datetime import date, datetime
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
    business_type: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(60), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    logo_url: Mapped[Optional[str]] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ClientStatus.PENDING)
    fee_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=20)
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
    totp_secret: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


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
    fee_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
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
    fee: Mapped[int] = mapped_column(BigInteger, nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class MessageTemplate(Base):
    """Versioned message templates. Only the active row per (key, language)
    is used; edits insert a new version rather than overwriting."""

    __tablename__ = "cl_message_template"
    __table_args__ = (UniqueConstraint("key", "language", "version", name="uq_cl_template_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
