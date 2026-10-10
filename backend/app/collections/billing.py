"""Monthly success-fee invoices and the scheduled jobs (daily queue,
reminders, overdue checks, data retention)."""

from datetime import date, datetime, timedelta
from datetime import time as dtime

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collections import audit, emailer, outreach, service, settings_store, storage
from app.collections.audit import Actor
from app.collections.models import (
    Client,
    DebtorAccount,
    DebtorPayment,
    Dispute,
    Invoice,
    InvoiceLine,
    OutreachItem,
    UploadBatch,
)
from app.collections.money import format_naira
from app.collections.timeutil import (
    lagos_date,
    lagos_datetime,
    utcnow,
    working_days_between,
)


def month_bounds(any_day: date) -> tuple[date, date]:
    start = any_day.replace(day=1)
    return start, start + relativedelta(months=1) - timedelta(days=1)


def _invoice_number(client: Client, period_start: date) -> str:
    return f"WLC-{period_start:%Y%m}-{client.id[:6].upper()}"


def draft_invoices(db: Session, period_start: date, actor: Actor = audit.SYSTEM) -> list[Invoice]:
    """Draft (or top up an existing draft) for every client with fee-eligible
    confirmations whose confirmation date (WAT) falls in the period."""
    period_start, period_end = month_bounds(period_start)
    window_start = lagos_datetime(period_start, dtime(0, 0))
    window_end = lagos_datetime(period_end + timedelta(days=1), dtime(0, 0))
    invoiced = select(InvoiceLine.payment_id)
    payments = db.execute(
        select(DebtorPayment)
        .where(
            DebtorPayment.status == "Confirmed",
            DebtorPayment.fee_eligible.is_(True),
            DebtorPayment.confirmed_at >= window_start,
            DebtorPayment.confirmed_at < window_end,
            DebtorPayment.id.not_in(invoiced),
        )
        .order_by(DebtorPayment.confirmed_at)
    ).scalars().all()
    by_client: dict[str, list[DebtorPayment]] = {}
    for p in payments:
        by_client.setdefault(p.client_id, []).append(p)

    out = []
    for client_id, rows in by_client.items():
        client = db.get(Client, client_id)
        invoice = db.execute(
            select(Invoice).where(Invoice.client_id == client_id, Invoice.period_start == period_start)
        ).scalar_one_or_none()
        if invoice is not None and invoice.status != "Draft":
            continue  # already issued; late confirmations roll into ops' manual handling
        if invoice is None:
            invoice = Invoice(
                number=_invoice_number(client, period_start), client_id=client_id, period_start=period_start,
                period_end=period_end, fee_percent=client.fee_percent, status="Draft",
            )
            db.add(invoice)
        for p in rows:
            account = db.get(DebtorAccount, p.account_id)
            invoice.lines.append(InvoiceLine(
                payment_id=p.id, reference_code=account.reference_code, debtor_name=account.debtor_name,
                amount=p.amount_confirmed, paid_on=p.paid_on, fee=p.fee_amount,
            ))
        service._retotal(invoice)
        db.flush()
        audit.record(db, actor, "invoice.drafted", "Invoice", invoice.id, client_id=client_id,
                     after={"lines": len(invoice.lines), "fee_amount": invoice.fee_amount})
        out.append(invoice)
    return out


def issue_invoice(db: Session, invoice: Invoice, actor: Actor, today: date) -> Invoice:
    if invoice.status != "Draft":
        raise service.ActionError("Only draft invoices can be issued.")
    if not invoice.lines:
        raise service.ActionError("This invoice has no lines.")
    terms = int(settings_store.get(db, "invoice_payment_terms_days"))
    invoice.status, invoice.issued_at, invoice.due_at = "Issued", utcnow(), today + timedelta(days=terms)
    audit.record(db, actor, "invoice.issued", "Invoice", invoice.id, client_id=invoice.client_id,
                 after={"fee_amount": invoice.fee_amount, "due_at": invoice.due_at})
    bank = settings_store.get(db, "waterline_bank_details")
    service.notify_client(
        db, invoice.client_id, f"Waterline Collections invoice {invoice.number}",
        f"Your success-fee invoice for {invoice.period_start:%B %Y} is ready.\n\n"
        f"Recovered and confirmed: {format_naira(invoice.total_confirmed)}\n"
        f"Fee ({invoice.fee_percent}%): {format_naira(invoice.fee_amount)}\n"
        f"Due by: {invoice.due_at:%d/%m/%Y}\n\n"
        f"Pay to: {bank['account_name']}, {bank['bank_name']}, {bank['account_number']}. "
        f"Use {invoice.number} as the narration.\n\n"
        f"See the line items: {emailer.site_url('/collections/app/invoices/')}",
        "client_invoice_issued",
    )
    return invoice


def mark_invoice_paid(db: Session, invoice: Invoice, actor: Actor) -> Invoice:
    if invoice.status not in ("Issued", "Overdue"):
        raise service.ActionError("Only issued or overdue invoices can be marked paid.")
    before = invoice.status
    invoice.status, invoice.paid_at = "Paid", utcnow()
    audit.record(db, actor, "invoice.paid", "Invoice", invoice.id, client_id=invoice.client_id,
                 before={"status": before}, after={"status": "Paid"})
    return invoice


def check_overdue_invoices(db: Session, today: date) -> dict:
    flag_days = int(settings_store.get(db, "invoice_overdue_flag_days"))
    overdue = flagged = 0
    for invoice in db.execute(select(Invoice).where(Invoice.status.in_(("Issued", "Overdue")))).scalars():
        if invoice.due_at and today > invoice.due_at and invoice.status == "Issued":
            invoice.status = "Overdue"
            audit.record(db, audit.SYSTEM, "invoice.overdue", "Invoice", invoice.id, client_id=invoice.client_id)
            overdue += 1
        if invoice.status == "Overdue" and not invoice.flagged_at and (today - invoice.due_at).days > flag_days:
            invoice.flagged_at = utcnow()
            client = db.get(Client, invoice.client_id)
            client.flagged_reason = f"Invoice {invoice.number} is more than {flag_days} days overdue."
            client.flagged_at = utcnow()
            audit.record(db, audit.SYSTEM, "client.flagged", "Client", client.id, client_id=client.id,
                         after={"reason": client.flagged_reason})
            flagged += 1
    return {"overdue": overdue, "flagged": flagged}


# ---------------------------------------------------------------- reminders + retention


def overdue_confirmations(db: Session, today: date, client_id: str | None = None) -> list[DebtorPayment]:
    holidays = settings_store.holidays(db)
    limit = int(settings_store.get(db, "proof_reminder_working_days"))
    stmt = select(DebtorPayment).where(DebtorPayment.status == "Proof submitted", DebtorPayment.source == "Debtor proof")
    if client_id:
        stmt = stmt.where(DebtorPayment.client_id == client_id)
    return [p for p in db.execute(stmt).scalars() if working_days_between(lagos_date(p.created_at), today, holidays) >= limit]


def stale_disputes(db: Session, today: date) -> list[Dispute]:
    holidays = settings_store.holidays(db)
    limit = int(settings_store.get(db, "dispute_flag_working_days"))
    rows = db.execute(select(Dispute).where(Dispute.status == "Open")).scalars()
    return [d for d in rows if working_days_between(lagos_date(d.created_at), today, holidays) > limit]


def remind_overdue_confirmations(db: Session, today: date) -> int:
    sent = 0
    by_client: dict[str, list[DebtorPayment]] = {}
    for p in overdue_confirmations(db, today):
        if p.reminder_sent_at is None:
            by_client.setdefault(p.client_id, []).append(p)
    for client_id, rows in by_client.items():
        service.notify_client(
            db, client_id, f"{len(rows)} payment(s) still waiting for your confirmation",
            "These proofs of payment have waited more than 5 working days. Please check your bank and confirm or "
            f"reject them:\n\n{emailer.site_url('/collections/app/confirmations/')}",
            "client_confirmation_reminder",
        )
        for p in rows:
            p.reminder_sent_at = utcnow()
        audit.record(db, audit.SYSTEM, "payment.confirmation_reminder", "Client", client_id, client_id=client_id,
                     after={"payments": [p.id for p in rows]})
        sent += 1
    return sent


def anonymise_account(db: Session, account: DebtorAccount, actor: Actor, reason: str) -> None:
    if account.anonymised_at:
        return
    for payment in db.execute(select(DebtorPayment).where(DebtorPayment.account_id == account.id)).scalars():
        storage.delete(payment.proof_file_url)
        payment.proof_file_url = None
        payment.sender_name = None
    for dispute in db.execute(select(Dispute).where(Dispute.account_id == account.id)).scalars():
        storage.delete(dispute.evidence_url)
        dispute.evidence_url = None
        dispute.details = None
    for item in db.execute(select(OutreachItem).where(OutreachItem.account_id == account.id)).scalars():
        item.rendered_text = "[removed]"
        item.reply_note = None
    account.debtor_name = "Anonymised"
    account.phone = account.phone_index = account.email = account.dependant_first_name = None
    account.dedupe_key = None
    account.access_token = None
    account.anonymised_at = utcnow()
    audit.record(db, actor, "account.anonymised", "DebtorAccount", account.id, client_id=account.client_id,
                 after={"reason": reason})


def apply_retention(db: Session, now: datetime) -> int:
    months = int(settings_store.get(db, "retention_months"))
    cutoff = now - relativedelta(months=months)
    rows = db.execute(
        select(DebtorAccount).where(
            DebtorAccount.anonymised_at.is_(None),
            DebtorAccount.closed_at.is_not(None),
            DebtorAccount.closed_at < cutoff,
        )
    ).scalars().all()
    for account in rows:
        anonymise_account(db, account, audit.SYSTEM, f"retention: closed more than {months} months ago")
    return len(rows)


def discard_stale_previews(db: Session, now: datetime) -> int:
    rows = db.execute(
        select(UploadBatch).where(UploadBatch.status == "Preview", UploadBatch.created_at < now - timedelta(days=7))
    ).scalars().all()
    for batch in rows:
        batch.status, batch.staged_rows = "Discarded", None
    return len(rows)


def run_daily(db: Session, now: datetime | None = None) -> dict:
    now = now or utcnow()
    today = lagos_date(now)
    result = {"queue": outreach.build_queue(db, now)}
    result["confirmation_reminders"] = remind_overdue_confirmations(db, today)
    result["invoices"] = check_overdue_invoices(db, today)
    if today.day == 1:
        prev = today - relativedelta(months=1)
        result["drafted"] = [i.number for i in draft_invoices(db, prev)]
    result["anonymised"] = apply_retention(db, now)
    result["previews_discarded"] = discard_stale_previews(db, now)
    db.flush()
    return result
