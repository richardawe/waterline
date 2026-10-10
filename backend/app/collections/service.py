"""State changes on debtor accounts, payments and disputes. Every function
here that changes data writes an audit entry; API handlers stay thin and
call into these so the rules live in one place (and in tests)."""

import json
import secrets
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.collections import audit, crypto, emailer, settings_store, storage
from app.collections.audit import Actor
from app.collections.models import (
    AccountStatus,
    Client,
    ClientStatus,
    ClientUser,
    DebtorAccount,
    DebtorPayment,
    Dispute,
    OutreachItem,
    PaymentPlan,
    UploadBatch,
)
from app.collections.money import format_naira
from app.collections.rules import active_discount, effective_rules, plan_options
from app.collections.timeutil import lagos_date, utcnow

DISPUTE_TYPES = ["Already paid", "Wrong amount", "Not my debt", "Other"]
REJECT_REASONS = ["Not received", "Wrong amount", "Other"]
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


class ActionError(ValueError):
    """A request that's understood but not allowed in the current state."""


# ---------------------------------------------------------------- helpers


def new_reference_code(db: Session) -> str:
    while True:
        code = "WL-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        if not db.execute(select(DebtorAccount.id).where(DebtorAccount.reference_code == code)).first():
            return code


def account_snapshot(account: DebtorAccount) -> dict:
    return {"status": account.status, "balance": account.balance, "opted_out": account.opted_out_at is not None}


def set_status(db: Session, account: DebtorAccount, status: str, actor: Actor, reason: str) -> None:
    if account.status == status:
        return
    before = account.status
    account.status = status
    if status in AccountStatus.CLOSED:
        account.closed_at = account.closed_at or utcnow()
    else:
        account.closed_at = None
    audit.record(
        db, actor, "account.status_changed", "DebtorAccount", account.id,
        client_id=account.client_id, before={"status": before}, after={"status": status, "reason": reason},
    )


def active_plan(db: Session, account: DebtorAccount) -> PaymentPlan | None:
    return db.execute(
        select(PaymentPlan).where(PaymentPlan.account_id == account.id, PaymentPlan.status == "Active")
    ).scalars().first()


def open_dispute(db: Session, account: DebtorAccount) -> Dispute | None:
    return db.execute(
        select(Dispute).where(Dispute.account_id == account.id, Dispute.status == "Open")
    ).scalars().first()


def pending_proofs(db: Session, account: DebtorAccount) -> int:
    return db.execute(
        select(func.count()).select_from(DebtorPayment).where(
            DebtorPayment.account_id == account.id, DebtorPayment.status == "Proof submitted"
        )
    ).scalar_one()


def settle_status(db: Session, account: DebtorAccount, actor: Actor, reason: str) -> None:
    """Work out where an account belongs after a payment/dispute event."""
    if account.status == AccountStatus.WITHDRAWN:
        return
    if account.balance <= 0:
        account.balance = 0
        plan = active_plan(db, account)
        if plan:
            plan.status = "Completed"
        set_status(db, account, AccountStatus.PAID, actor, reason)
        return
    if open_dispute(db, account):
        set_status(db, account, AccountStatus.DISPUTED, actor, reason)
    elif pending_proofs(db, account):
        set_status(db, account, AccountStatus.PROOF_SUBMITTED, actor, reason)
    elif account.status == AccountStatus.PAUSED:
        return
    elif active_plan(db, account):
        set_status(db, account, AccountStatus.ON_PLAN, actor, reason)
    else:
        set_status(db, account, AccountStatus.ACTIVE, actor, reason)


def client_contacts(db: Session, client_id: str) -> list[str]:
    users = db.execute(
        select(ClientUser).where(ClientUser.client_id == client_id, ClientUser.is_active.is_(True))
    ).scalars().all()
    return [u.email for u in users if u.email_verified_at]


def notify_client(db: Session, client_id: str, subject: str, body: str, kind: str) -> None:
    for to in client_contacts(db, client_id):
        emailer.send(db, to, subject, body, kind=kind, client_id=client_id)


def notify_ops(db: Session, subject: str, body: str, kind: str, client_id: str | None = None) -> None:
    emailer.send(db, _ops_address(), subject, body, kind=kind, client_id=client_id)


def _ops_address() -> str | None:
    from app.config import get_settings

    return get_settings().collections_ops_alert_email


def mark_response(account: DebtorAccount, now: datetime | None = None) -> None:
    account.last_response_at = now or utcnow()
    if account.status == AccountStatus.UNRESPONSIVE:
        account.status = AccountStatus.ACTIVE


# ---------------------------------------------------------------- abuse


def check_batch_abuse(db: Session, batch_id: str, actor: Actor) -> None:
    """Opt-out or dispute rate above the threshold on a batch flags the client."""
    batch = db.get(UploadBatch, batch_id)
    if batch is None or batch.flagged_at or not batch.imported_count:
        return
    total = batch.imported_count
    opted = db.execute(
        select(func.count()).select_from(DebtorAccount).where(
            DebtorAccount.batch_id == batch_id, DebtorAccount.opted_out_at.is_not(None)
        )
    ).scalar_one()
    disputed = db.execute(
        select(func.count(func.distinct(Dispute.account_id))).join(DebtorAccount, DebtorAccount.id == Dispute.account_id).where(
            DebtorAccount.batch_id == batch_id
        )
    ).scalar_one()
    threshold = float(settings_store.get(db, "abuse_rate_threshold"))
    rate = max(opted, disputed) / total
    if rate > threshold:
        now = utcnow()
        batch.flagged_at = now
        client = db.get(Client, batch.client_id)
        client.flagged_reason = (
            f"Batch {batch.file_name}: {opted} opt-outs and {disputed} disputes out of {total} accounts "
            f"(above {threshold:.0%})."
        )
        client.flagged_at = now
        audit.record(db, actor, "client.flagged", "Client", client.id, client_id=client.id, after={"reason": client.flagged_reason})
        notify_ops(db, f"Client flagged: {client.business_name}", client.flagged_reason, "ops_client_flagged", client.id)


# ---------------------------------------------------------------- debtor actions


def opt_out(db: Session, account: DebtorAccount, actor: Actor, via: str) -> None:
    if account.opted_out_at:
        return
    account.opted_out_at = utcnow()
    mark_response(account)
    # leaves the queue for good, today included
    for item in db.execute(
        select(OutreachItem).where(OutreachItem.account_id == account.id, OutreachItem.status == "Queued")
    ).scalars():
        item.status, item.skip_reason = "Skipped", "Opted out"
    audit.record(db, actor, "account.opted_out", "DebtorAccount", account.id, client_id=account.client_id, after={"via": via})
    check_batch_abuse(db, account.batch_id, actor)


def choose_plan(db: Session, account: DebtorAccount, instalments: int, actor: Actor, today: date) -> PaymentPlan:
    if account.status in AccountStatus.CLOSED or account.balance <= 0:
        raise ActionError("This balance is already closed.")
    if account.status == AccountStatus.DISPUTED:
        raise ActionError("This balance is under review. You'll be able to choose a plan once it's resolved.")
    if active_plan(db, account):
        raise ActionError("You already have a payment plan.")
    client = db.get(Client, account.client_id)
    rules = effective_rules(account, db.get(UploadBatch, account.batch_id), client)
    option = next((o for o in plan_options(rules, account.balance, today) if o["instalments"] == instalments), None)
    if option is None:
        raise ActionError("That plan isn't available for this balance.")
    plan = PaymentPlan(
        account_id=account.id,
        instalment_count=instalments,
        instalment_amount=option["instalment_amount"],
        total_amount=account.balance,
        schedule=json.dumps(option["schedule"]),
        status="Active",
    )
    db.add(plan)
    db.flush()
    mark_response(account)
    audit.record(db, actor, "plan.created", "PaymentPlan", plan.id, client_id=account.client_id, after=option)
    if account.status in (AccountStatus.NEW, AccountStatus.ACTIVE, AccountStatus.UNRESPONSIVE):
        set_status(db, account, AccountStatus.ON_PLAN, actor, "plan chosen")
    return plan


def _possible_duplicate(db: Session, account: DebtorAccount, file_hash: str | None, amount: int, paid_on: date) -> bool:
    conditions = [(DebtorPayment.amount_claimed == amount) & (DebtorPayment.paid_on == paid_on)]
    if file_hash:
        conditions.append(DebtorPayment.proof_file_hash == file_hash)
    return db.execute(
        select(DebtorPayment.id).where(
            DebtorPayment.account_id == account.id, DebtorPayment.status != "Rejected", or_(*conditions)
        )
    ).first() is not None


def submit_proof(
    db: Session,
    account: DebtorAccount,
    *,
    file_bytes: bytes,
    amount: int,
    paid_on: date,
    sender_name: str | None,
    actor: Actor,
    today: date,
) -> DebtorPayment:
    if account.status in AccountStatus.CLOSED:
        raise ActionError("This balance is already closed.")
    if amount <= 0:
        raise ActionError("Enter the amount you paid.")
    if paid_on > today:
        raise ActionError("The payment date can't be in the future.")
    file_hash = crypto.file_sha256(file_bytes)
    duplicate = _possible_duplicate(db, account, file_hash, amount, paid_on)
    key = storage.save(file_bytes, f"proofs/{account.client_id}")
    payment = DebtorPayment(
        account_id=account.id,
        client_id=account.client_id,
        amount_claimed=amount,
        paid_on=paid_on,
        sender_name=(sender_name or "").strip()[:120] or None,
        proof_file_url=key,
        proof_file_hash=file_hash,
        possible_duplicate=duplicate,
        source="Debtor proof",
        status="Proof submitted",
    )
    db.add(payment)
    db.flush()
    mark_response(account)
    audit.record(
        db, actor, "payment.proof_submitted", "Payment", payment.id, client_id=account.client_id,
        after={"amount_claimed": amount, "paid_on": paid_on, "possible_duplicate": duplicate},
    )
    if account.status != AccountStatus.DISPUTED:
        set_status(db, account, AccountStatus.PROOF_SUBMITTED, actor, "proof submitted")
    notify_client(
        db, account.client_id,
        f"Proof of payment to confirm: {account.reference_code}",
        f"{account.debtor_name} says they paid {format_naira(amount)} on {paid_on:%d/%m/%Y} "
        f"(reference {account.reference_code}). Please check your bank account and confirm or reject it:\n\n"
        f"{emailer.site_url('/collections/app/confirmations/')}",
        "client_proof_submitted",
    )
    return payment


def record_client_payment(
    db: Session, account: DebtorAccount, *, amount: int, paid_on: date, note: str | None, actor: Actor, today: date
) -> DebtorPayment:
    """A payment the client received directly (e.g. cash at the bursary).
    It's confirmed by definition — the client is the one recording it."""
    if account.status == AccountStatus.WITHDRAWN:
        raise ActionError("This account is withdrawn.")
    if amount <= 0:
        raise ActionError("Amount must be more than zero.")
    if paid_on > today:
        raise ActionError("The payment date can't be in the future.")
    payment = DebtorPayment(
        account_id=account.id,
        client_id=account.client_id,
        amount_claimed=amount,
        paid_on=paid_on,
        sender_name=(note or "").strip()[:120] or None,
        source="Client recorded",
        status="Proof submitted",
    )
    db.add(payment)
    db.flush()
    audit.record(db, actor, "payment.client_recorded", "Payment", payment.id, client_id=account.client_id,
                 after={"amount": amount, "paid_on": paid_on})
    confirm_payment(db, payment, amount, actor)
    return payment


# ---------------------------------------------------------------- confirmation


def fee_eligibility(db: Session, payment: DebtorPayment, account: DebtorAccount, client: Client) -> bool:
    """Confirmed + at least one Sent message on or before the payment date +
    payment within `attribution_days` of the most recent such message.
    Nothing on a Withdrawn account counts."""
    if payment.status != "Confirmed" or account.status == AccountStatus.WITHDRAWN:
        return False
    sent_times = db.execute(
        select(OutreachItem.sent_at).where(OutreachItem.account_id == account.id, OutreachItem.status == "Sent")
    ).scalars().all()
    sent_days = [lagos_date(t) for t in sent_times if t is not None and lagos_date(t) <= payment.paid_on]
    if not sent_days:
        return False
    return (payment.paid_on - max(sent_days)).days <= client.attribution_days


def confirm_payment(db: Session, payment: DebtorPayment, amount_received: int | None, actor: Actor) -> DebtorPayment:
    if payment.status != "Proof submitted":
        raise ActionError("Only payments awaiting confirmation can be confirmed.")
    amount = payment.amount_claimed if amount_received is None else int(amount_received)
    if amount <= 0:
        raise ActionError("Amount received must be more than zero. Use Reject if nothing arrived.")
    account = db.get(DebtorAccount, payment.account_id)
    client = db.get(Client, payment.client_id)
    before = account_snapshot(account)

    payment.amount_confirmed = amount
    payment.status = "Confirmed"
    payment.confirmed_by = actor.id
    payment.confirmed_at = utcnow()

    rules = effective_rules(account, db.get(UploadBatch, account.batch_id), client)
    discount = active_discount(rules, account.balance, payment.paid_on)
    if discount and amount >= discount["pay_amount"] and not active_plan(db, account):
        account.balance = 0  # paid in full by the deadline: the discount is written off
        discount_applied = discount["discount_amount"]
    else:
        account.balance = max(0, account.balance - amount)
        discount_applied = 0

    payment.fee_eligible = fee_eligibility(db, payment, account, client)
    if payment.fee_eligible:
        from app.collections import fees  # fees imports this module

        payment.fee_percent, payment.fee_amount = fees.fee_for(client, account, amount, payment.paid_on)
    else:
        payment.fee_percent, payment.fee_amount = None, 0
    audit.record(
        db, actor, "payment.confirmed", "Payment", payment.id, client_id=client.id,
        before={**before, "claimed": payment.amount_claimed},
        after={"amount_confirmed": amount, "balance": account.balance, "fee_eligible": payment.fee_eligible,
               "fee_amount": payment.fee_amount, "fee_percent": payment.fee_percent, "discount_applied": discount_applied},
    )
    mark_response(account)
    settle_status(db, account, actor, "payment confirmed")
    return payment


def reject_payment(db: Session, payment: DebtorPayment, reason: str, note: str | None, actor: Actor) -> DebtorPayment:
    if payment.status != "Proof submitted":
        raise ActionError("Only payments awaiting confirmation can be rejected.")
    if reason not in REJECT_REASONS:
        raise ActionError(f"Reason must be one of: {', '.join(REJECT_REASONS)}.")
    account = db.get(DebtorAccount, payment.account_id)
    payment.status = "Rejected"
    payment.reject_reason = reason if not note else f"{reason}: {note.strip()[:150]}"
    payment.confirmed_by = actor.id
    payment.confirmed_at = utcnow()
    audit.record(db, actor, "payment.rejected", "Payment", payment.id, client_id=account.client_id,
                 after={"reason": payment.reject_reason})
    settle_status(db, account, actor, "proof rejected")
    client = db.get(Client, account.client_id)
    notify_ops(
        db, f"Proof rejected: {account.reference_code} ({client.business_name})",
        f"{client.business_name} rejected a proof of payment of {format_naira(payment.amount_claimed)} "
        f"on {account.reference_code}. Reason: {payment.reject_reason}. The account is back in outreach.",
        "ops_proof_rejected", client.id,
    )
    return payment


def reverse_payment(db: Session, payment: DebtorPayment, reason: str, actor: Actor) -> DebtorPayment:
    """Undo a confirmation made in error. Not allowed once the payment is on
    an issued invoice — ops must credit that by hand."""
    from app.collections.models import Invoice, InvoiceLine

    if payment.status != "Confirmed":
        raise ActionError("Only confirmed payments can be reversed.")
    invoiced = db.execute(
        select(Invoice.status).join(InvoiceLine, InvoiceLine.invoice_id == Invoice.id).where(InvoiceLine.payment_id == payment.id)
    ).scalar_one_or_none()
    if invoiced and invoiced != "Draft":
        raise ActionError("This payment is on an issued invoice. Contact Waterline to correct it.")
    if invoiced == "Draft":
        line = db.execute(select(InvoiceLine).where(InvoiceLine.payment_id == payment.id)).scalar_one()
        invoice = line.invoice
        invoice.lines.remove(line)
        db.flush()
        _retotal(invoice)
    account = db.get(DebtorAccount, payment.account_id)
    before = account_snapshot(account)
    account.balance = min(account.original_amount, account.balance + (payment.amount_confirmed or 0))
    payment.status = "Reversed"
    payment.fee_eligible = False
    payment.fee_amount = 0
    payment.reject_reason = reason.strip()[:200] if reason else "Reversed"
    if account.status == AccountStatus.PAID:
        account.status = AccountStatus.ACTIVE  # reopen; settle_status refines below
        account.closed_at = None
    audit.record(db, actor, "payment.reversed", "Payment", payment.id, client_id=account.client_id,
                 before=before, after={"balance": account.balance, "reason": payment.reject_reason})
    settle_status(db, account, actor, "confirmation reversed")
    return payment


def _retotal(invoice) -> None:
    from decimal import ROUND_HALF_UP, Decimal

    invoice.total_confirmed = sum(l.amount for l in invoice.lines)
    invoice.fee_amount = sum(l.fee for l in invoice.lines)
    # effective rate: lines may carry different rates under a tiered schedule
    invoice.fee_percent = (
        (Decimal(invoice.fee_amount) * 100 / Decimal(invoice.total_confirmed)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if invoice.total_confirmed else 0
    )


# ---------------------------------------------------------------- disputes


def raise_dispute(
    db: Session,
    account: DebtorAccount,
    *,
    type: str,
    details: str | None,
    evidence_bytes: bytes | None,
    raised_by: str,
    actor: Actor,
) -> Dispute:
    if type not in DISPUTE_TYPES:
        raise ActionError(f"Type must be one of: {', '.join(DISPUTE_TYPES)}.")
    if account.status in AccountStatus.CLOSED:
        raise ActionError("This balance is already closed.")
    existing = open_dispute(db, account)
    if existing:
        return existing
    evidence_key = storage.save(evidence_bytes, f"evidence/{account.client_id}") if evidence_bytes else None
    dispute = Dispute(
        account_id=account.id,
        client_id=account.client_id,
        type=type,
        details=(details or "").strip()[:2000] or None,
        evidence_url=evidence_key,
        raised_by=raised_by,
        status="Open",
    )
    db.add(dispute)
    db.flush()
    # disputes pause everything, within this same request
    for item in db.execute(
        select(OutreachItem).where(OutreachItem.account_id == account.id, OutreachItem.status == "Queued")
    ).scalars():
        item.status, item.skip_reason = "Skipped", "Dispute opened"
    mark_response(account)
    audit.record(db, actor, "dispute.opened", "Dispute", dispute.id, client_id=account.client_id,
                 after={"type": type, "raised_by": raised_by})
    set_status(db, account, AccountStatus.DISPUTED, actor, "dispute opened")
    notify_client(
        db, account.client_id, f"Query raised on {account.reference_code}",
        f"A query ({type}) was raised on {account.reference_code}. Messages to this person are paused until you "
        f"resolve it:\n\n{emailer.site_url('/collections/app/disputes/')}",
        "client_dispute_opened",
    )
    check_batch_abuse(db, account.batch_id, actor)
    return dispute


def resolve_dispute(
    db: Session, dispute: Dispute, *, outcome: str, new_balance: int | None, note: str | None, actor: Actor
) -> Dispute:
    if dispute.status != "Open":
        raise ActionError("This dispute is already resolved.")
    account = db.get(DebtorAccount, dispute.account_id)
    before = account_snapshot(account)
    if outcome == "Uphold":
        dispute.status = "Upheld"
    elif outcome == "Adjust":
        if new_balance is None or new_balance < 0:
            raise ActionError("Enter the corrected balance.")
        if new_balance > account.original_amount:
            raise ActionError("The corrected balance can't be more than the original amount.")
        dispute.status = "Adjusted"
        account.balance = new_balance
        plan = active_plan(db, account)
        if plan:
            plan.status = "Broken"  # amounts changed; debtor picks a new plan
    elif outcome == "Withdraw":
        dispute.status = "Withdrawn"
    else:
        raise ActionError("Outcome must be Uphold, Adjust or Withdraw.")
    dispute.resolution_note = (note or "").strip()[:2000] or None
    dispute.resolved_by = actor.id
    dispute.resolved_at = utcnow()
    audit.record(db, actor, "dispute.resolved", "Dispute", dispute.id, client_id=account.client_id,
                 before=before, after={"outcome": dispute.status, "balance": account.balance})

    if dispute.status == "Withdrawn":
        set_status(db, account, AccountStatus.WITHDRAWN, actor, "dispute withdrawn by client")
        plan = active_plan(db, account)
        if plan:
            plan.status = "Broken"
        # nothing on a withdrawn account is fee-eligible
        for payment in db.execute(select(DebtorPayment).where(DebtorPayment.account_id == account.id)).scalars():
            if payment.status == "Proof submitted":
                payment.status, payment.reject_reason = "Rejected", "Account withdrawn"
            if payment.fee_eligible:
                payment.fee_eligible, payment.fee_amount = False, 0
        return dispute

    settle_status(db, account, actor, f"dispute {dispute.status.lower()}")
    if account.status not in AccountStatus.CLOSED and not account.opted_out_at:
        account.pending_notice = "DISPUTE_RESOLVED"  # debtor is told on the next queue
    return dispute


# ---------------------------------------------------------------- client account actions


def pause_account(db: Session, account: DebtorAccount, actor: Actor) -> None:
    if account.status in AccountStatus.CLOSED or account.status == AccountStatus.DISPUTED:
        raise ActionError("This account can't be paused in its current state.")
    for item in db.execute(
        select(OutreachItem).where(OutreachItem.account_id == account.id, OutreachItem.status == "Queued")
    ).scalars():
        item.status, item.skip_reason = "Skipped", "Paused by client"
    set_status(db, account, AccountStatus.PAUSED, actor, "paused by client")


def resume_account(db: Session, account: DebtorAccount, actor: Actor) -> None:
    if account.status != AccountStatus.PAUSED:
        raise ActionError("Only paused accounts can be resumed.")
    account.status = AccountStatus.ACTIVE
    settle_status(db, account, actor, "resumed by client")
    audit.record(db, actor, "account.resumed", "DebtorAccount", account.id, client_id=account.client_id)


def withdraw_account(db: Session, account: DebtorAccount, actor: Actor) -> None:
    if account.status in AccountStatus.CLOSED:
        raise ActionError("This account is already closed.")
    dispute = open_dispute(db, account)
    if dispute:
        resolve_dispute(db, dispute, outcome="Withdraw", new_balance=None, note="Account withdrawn by client", actor=actor)
        return
    for item in db.execute(
        select(OutreachItem).where(OutreachItem.account_id == account.id, OutreachItem.status == "Queued")
    ).scalars():
        item.status, item.skip_reason = "Skipped", "Withdrawn"
    set_status(db, account, AccountStatus.WITHDRAWN, actor, "withdrawn by client")
    for payment in db.execute(select(DebtorPayment).where(DebtorPayment.account_id == account.id)).scalars():
        if payment.status == "Proof submitted":
            payment.status, payment.reject_reason = "Rejected", "Account withdrawn"
        if payment.fee_eligible:
            payment.fee_eligible, payment.fee_amount = False, 0


def reissue_token(db: Session, account: DebtorAccount, actor: Actor) -> str:
    account.access_token = crypto.new_token()
    audit.record(db, actor, "account.token_reissued", "DebtorAccount", account.id, client_id=account.client_id)
    return account.access_token


def token_valid(account: DebtorAccount | None, now: datetime) -> bool:
    """Debtor links die 30 days after an account closes, or when anonymised."""
    if account is None or account.anonymised_at:
        return False
    if account.closed_at and now - account.closed_at > timedelta(days=30):
        return False
    client = account.client
    return client is not None and client.status in (ClientStatus.APPROVED, ClientStatus.SUSPENDED)


def apply_balance_update(
    db: Session, account: DebtorAccount, *, new_balance: int, due_date: date | None, reason: str | None,
    actor: Actor, today: date,
) -> None:
    """One row of an 'update balances' upload.

    - Higher: new charges or interest — balance (and the original amount) go up.
    - Lower, reason 'payment' (the default): the debtor paid the client directly.
      Recorded as a client-recorded, confirmed payment, so the usual fee rules
      apply — a balance update can't quietly skip a recovery.
    - Lower, reason 'correction': the amount was wrong. Balance changes, no
      payment; corrected to zero closes the account as Withdrawn (no fee).
    """
    if account.status in AccountStatus.CLOSED:
        raise ActionError("That account is already closed.")
    if account.status == AccountStatus.DISPUTED:
        raise ActionError("Resolve the open query before changing the balance.")
    before = account_snapshot(account)
    change = new_balance - account.balance
    if due_date:
        account.due_date = due_date
    if change > 0:
        account.balance = new_balance
        account.original_amount += change
        plan = active_plan(db, account)
        if plan:
            plan.status = "Broken"  # amounts changed; the debtor picks a new plan
        settle_status(db, account, actor, "balance increased by client")
    elif change < 0 and (reason or "payment") == "payment":
        record_client_payment(db, account, amount=-change, paid_on=today,
                              note="Balance update upload", actor=actor, today=today)
    elif change < 0:
        account.balance = new_balance
        plan = active_plan(db, account)
        if plan:
            plan.status = "Broken"
        if new_balance == 0:
            withdraw_account(db, account, actor)
        else:
            settle_status(db, account, actor, "balance corrected by client")
    audit.record(db, actor, "account.balance_updated", "DebtorAccount", account.id, client_id=account.client_id,
                 before=before, after={"balance": account.balance, "change": change, "reason": reason,
                                       "due_date": due_date})
