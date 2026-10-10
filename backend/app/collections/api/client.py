"""Client (business) API. Every query here is scoped to the caller's
client_id — `_owned` is the only way handlers load a tenant-owned row."""

import csv
import io
import json
from datetime import date, time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collections import (
    audit,
    crypto,
    emailer,
    serialize,
    service,
    settings_store,
    upload,
)
from app.collections.api.public import (
    BUSINESS_TYPES,
    INVITE_TTL,
    NIGERIAN_STATES,
    issue_link,
)
from app.collections.auth import (
    ClientContext,
    end_all_sessions,
    require_client,
    require_owner,
)
from app.collections.models import (
    AccountStatus,
    AuditLog,
    ClientStatus,
    ClientUser,
    DebtorAccount,
    DebtorPayment,
    Dispute,
    Invoice,
    OutreachItem,
    PaymentPlan,
    UploadBatch,
)
from app.collections.money import naira_to_kobo
from app.collections.rules import RulesInvalid, validate_rules
from app.collections.timeutil import lagos_date, lagos_datetime, utcnow
from app.db import get_db

router = APIRouter(prefix="/collections/client", tags=["collections-client"])

ATTESTATION = (
    "I confirm these balances are accurate, genuinely owed, and that I am entitled to contact these people about them."
)
PAGE_SIZE = 50


def _owned(db: Session, model, entity_id: str, ctx: ClientContext):
    row = db.get(model, entity_id)
    if row is None or row.client_id != ctx.client.id:
        raise HTTPException(404, "Not found.")
    return row


def _action(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (service.ActionError, RulesInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


def _today():
    return lagos_date(utcnow())


# ---------------------------------------------------------------- me + dashboard


@router.get("/me")
def me(ctx: ClientContext = Depends(require_client)):
    return {"user": serialize.user(ctx.user), "client": serialize.client(ctx.client)}


@router.get("/dashboard")
def dashboard(ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    cid = ctx.client.id
    uploaded = db.execute(select(func.coalesce(func.sum(DebtorAccount.original_amount), 0)).where(DebtorAccount.client_id == cid)).scalar_one()
    recovered = db.execute(
        select(func.coalesce(func.sum(DebtorPayment.amount_confirmed), 0)).where(DebtorPayment.client_id == cid, DebtorPayment.status == "Confirmed")
    ).scalar_one()
    awaiting = db.execute(
        select(func.coalesce(func.sum(DebtorPayment.amount_claimed), 0), func.count())
        .where(DebtorPayment.client_id == cid, DebtorPayment.status == "Proof submitted")
    ).one()
    open_disputes = db.execute(select(func.count()).select_from(Dispute).where(Dispute.client_id == cid, Dispute.status == "Open")).scalar_one()
    active_plans = db.execute(
        select(func.count()).select_from(PaymentPlan).join(DebtorAccount, DebtorAccount.id == PaymentPlan.account_id)
        .where(DebtorAccount.client_id == cid, PaymentPlan.status == "Active")
    ).scalar_one()
    today = _today()
    month_start = lagos_datetime(today.replace(day=1), time(0, 0))
    fees = db.execute(
        select(func.coalesce(func.sum(DebtorPayment.fee_amount), 0)).where(
            DebtorPayment.client_id == cid, DebtorPayment.status == "Confirmed", DebtorPayment.fee_eligible.is_(True),
            DebtorPayment.confirmed_at >= month_start,
        )
    ).scalar_one()
    by_status = dict(db.execute(
        select(DebtorAccount.status, func.count()).where(DebtorAccount.client_id == cid).group_by(DebtorAccount.status)
    ).all())
    return {
        "client": serialize.client(ctx.client, full=False),
        "total_uploaded": int(uploaded),
        "total_recovered": int(recovered),
        "recovery_rate": (int(recovered) / int(uploaded)) if uploaded else 0,
        "awaiting_confirmation": int(awaiting[0]),
        "awaiting_count": int(awaiting[1]),
        "open_disputes": open_disputes,
        "active_plans": active_plans,
        "fees_this_month": int(fees),
        "accounts_by_status": by_status,
    }


# ---------------------------------------------------------------- uploads


@router.get("/uploads")
def list_uploads(ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    rows = db.execute(
        select(UploadBatch).where(UploadBatch.client_id == ctx.client.id, UploadBatch.status != "Discarded")
        .order_by(UploadBatch.created_at.desc())
    ).scalars()
    return [serialize.batch(b) for b in rows]


def _existing_keys(db: Session, client_id: str) -> set[str]:
    return set(db.execute(
        select(DebtorAccount.dedupe_key).where(
            DebtorAccount.client_id == client_id, DebtorAccount.dedupe_key.is_not(None),
            DebtorAccount.status.not_in(tuple(AccountStatus.CLOSED)),
        )
    ).scalars())


def _store_preview(batch: UploadBatch, preview: list[dict]) -> None:
    batch.staged_rows = json.dumps(preview)
    batch.row_count = len(preview)
    batch.valid_count = sum(1 for r in preview if r["status"] == "valid")
    batch.error_count = sum(1 for r in preview if r["status"] == "error")
    batch.duplicate_count = sum(1 for r in preview if r["status"] == "duplicate")


def _preview_response(batch: UploadBatch) -> dict:
    preview = json.loads(batch.staged_rows) if batch.staged_rows else []
    return {"batch": serialize.batch(batch), "rows": preview, "attestation": ATTESTATION}


@router.post("/uploads", status_code=201)
async def create_upload(
    file: UploadFile = File(...), ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)
):
    if ctx.client.status in (ClientStatus.REJECTED, ClientStatus.SUSPENDED):
        raise HTTPException(403, f"Uploads are unavailable while your account is {ctx.client.status.lower()}.")
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "The file is larger than 10 MB.")
    try:
        rows = upload.read_rows(file.filename or "upload", data)
    except upload.UploadRejected as exc:
        raise HTTPException(422, str(exc)) from exc
    cap = ctx.client.upload_row_cap
    if len(rows) > cap:
        raise HTTPException(
            422, f"This file has {len(rows)} rows. Your account can upload up to {cap} rows at a time for now — "
            "split the file, or ask us to raise your limit."
        )
    preview = upload.build_preview(rows, _existing_keys(db, ctx.client.id))
    batch = UploadBatch(client_id=ctx.client.id, file_name=(file.filename or "upload")[:255], uploaded_by=ctx.user.id)
    _store_preview(batch, preview)
    db.add(batch)
    db.flush()
    audit.record(db, ctx.actor, "upload.previewed", "UploadBatch", batch.id,
                 after={"file_name": batch.file_name, "rows": batch.row_count, "valid": batch.valid_count,
                        "errors": batch.error_count, "duplicates": batch.duplicate_count})
    db.commit()
    return _preview_response(batch)


@router.get("/uploads/{batch_id}")
def get_upload(batch_id: str, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    batch = _owned(db, UploadBatch, batch_id, ctx)
    out = _preview_response(batch)
    if batch.status == "Imported":
        out["accounts"] = db.execute(
            select(func.count()).select_from(DebtorAccount).where(DebtorAccount.batch_id == batch.id)
        ).scalar_one()
    return out


class FixesIn(BaseModel):
    fixes: dict[str, dict[str, str]] = {}  # row number -> corrected raw values


def _apply_fixes(db: Session, batch: UploadBatch, fixes: dict[str, dict[str, str]], client_id: str) -> list[dict]:
    preview = json.loads(batch.staged_rows or "[]")
    by_row = {str(r["row"]): r for r in preview}
    for row_no, raw in fixes.items():
        if row_no not in by_row:
            raise HTTPException(422, f"Row {row_no} isn't in this upload.")
        extra = set(raw) - set(upload.COLUMNS)
        if extra:
            raise HTTPException(422, f"Unexpected column(s): {', '.join(sorted(extra))}.")
        by_row[row_no]["raw"] = {**by_row[row_no]["raw"], **{k: str(v) for k, v in raw.items()}}
    rebuilt = upload.build_preview([r["raw"] for r in preview], _existing_keys(db, client_id))
    for old, new in zip(preview, rebuilt):
        new["row"] = old["row"]
    return rebuilt


@router.post("/uploads/{batch_id}/revalidate")
def revalidate(batch_id: str, body: FixesIn, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    batch = _owned(db, UploadBatch, batch_id, ctx)
    if batch.status != "Preview":
        raise HTTPException(409, "This upload has already been processed.")
    _store_preview(batch, _apply_fixes(db, batch, body.fixes, ctx.client.id))
    db.commit()
    return _preview_response(batch)


class ImportIn(BaseModel):
    attest: bool
    skip_rows: list[int] = []
    rules: dict | None = None  # None → client default rules


@router.post("/uploads/{batch_id}/import")
def import_upload(batch_id: str, body: ImportIn, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    batch = _owned(db, UploadBatch, batch_id, ctx)
    if batch.status != "Preview":
        raise HTTPException(409, "This upload has already been processed.")
    if not body.attest:
        raise HTTPException(422, "Please confirm the attestation before importing.")
    rules = None
    if body.rules is not None:
        try:
            rules = validate_rules(body.rules)
        except RulesInvalid as exc:
            raise HTTPException(422, str(exc)) from exc
    preview = json.loads(batch.staged_rows or "[]")
    skip = set(body.skip_rows)
    existing = _existing_keys(db, ctx.client.id)
    imported = 0
    for row in preview:
        if row["status"] != "valid" or row["row"] in skip:
            continue
        c = row["clean"]
        key = upload.duplicate_key(c["phone"], c["description"], c["amount_kobo"])
        if key in existing:  # another upload landed since this preview was built
            continue
        existing.add(key)
        db.add(DebtorAccount(
            client_id=ctx.client.id, batch_id=batch.id, reference_code=service.new_reference_code(db),
            debtor_name=c["debtor_name"], phone=c["phone"], phone_index=crypto.blind_index(c["phone"]),
            dedupe_key=key, email=c["email"], description=c["description"],
            dependant_first_name=c["dependant_first_name"], original_amount=c["amount_kobo"],
            balance=c["amount_kobo"], due_date=date.fromisoformat(c["due_date"]) if c["due_date"] else None,
            status=AccountStatus.NEW, access_token=crypto.new_token(),
        ))
        db.flush()
        imported += 1
    if imported == 0:
        raise HTTPException(422, "There are no valid rows to import.")
    now = utcnow()
    batch.status, batch.imported_count, batch.staged_rows = "Imported", imported, None
    batch.attested_by, batch.attested_at, batch.attestation_text = ctx.user.id, now, ATTESTATION
    batch.rules = json.dumps(rules) if rules else None
    audit.record(db, ctx.actor, "upload.attested", "UploadBatch", batch.id,
                 after={"attestation": ATTESTATION, "attested_at": now})
    audit.record(db, ctx.actor, "upload.imported", "UploadBatch", batch.id,
                 after={"imported": imported, "skipped_rows": sorted(skip), "rules": rules})
    db.commit()
    return {"batch": serialize.batch(batch)}


@router.post("/uploads/{batch_id}/discard")
def discard_upload(batch_id: str, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    batch = _owned(db, UploadBatch, batch_id, ctx)
    if batch.status != "Preview":
        raise HTTPException(409, "Only an unimported upload can be discarded.")
    batch.status, batch.staged_rows = "Discarded", None
    audit.record(db, ctx.actor, "upload.discarded", "UploadBatch", batch.id)
    db.commit()
    return {"ok": True}


class BatchRulesIn(BaseModel):
    rules: dict | None


@router.put("/uploads/{batch_id}/rules")
def set_batch_rules(batch_id: str, body: BatchRulesIn, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    batch = _owned(db, UploadBatch, batch_id, ctx)
    if batch.status != "Imported":
        raise HTTPException(409, "Rules can be changed once the upload is imported.")
    try:
        rules = validate_rules(body.rules) if body.rules is not None else None
    except RulesInvalid as exc:
        raise HTTPException(422, str(exc)) from exc
    before = json.loads(batch.rules) if batch.rules else None
    batch.rules = json.dumps(rules) if rules else None
    audit.record(db, ctx.actor, "upload.rules_changed", "UploadBatch", batch.id, before=before, after=rules)
    db.commit()
    return serialize.batch(batch)


# ---------------------------------------------------------------- accounts


@router.get("/accounts")
def list_accounts(
    status: str | None = None, q: str | None = None, batch_id: str | None = None, page: int = 1,
    ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db),
):
    stmt = select(DebtorAccount).where(DebtorAccount.client_id == ctx.client.id)
    if status == "Opted out":
        stmt = stmt.where(DebtorAccount.opted_out_at.is_not(None))
    elif status:
        stmt = stmt.where(DebtorAccount.status == status)
    if batch_id:
        stmt = stmt.where(DebtorAccount.batch_id == batch_id)
    rows = db.execute(stmt.order_by(DebtorAccount.created_at.desc(), DebtorAccount.reference_code)).scalars().all()
    if q:
        # names are encrypted at rest, so search happens after decryption
        needle = q.strip().lower()
        rows = [a for a in rows if needle in a.debtor_name.lower() or needle in a.reference_code.lower()
                or needle in (a.phone or "")]
    total = len(rows)
    page = max(1, page)
    rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return {"total": total, "page": page, "page_size": PAGE_SIZE, "accounts": [serialize.account(a) for a in rows],
            "statuses": AccountStatus.ALL + ["Opted out"]}


@router.get("/accounts/{account_id}")
def get_account(account_id: str, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    account = _owned(db, DebtorAccount, account_id, ctx)
    payments = db.execute(select(DebtorPayment).where(DebtorPayment.account_id == account.id).order_by(DebtorPayment.created_at)).scalars().all()
    disputes = db.execute(select(Dispute).where(Dispute.account_id == account.id).order_by(Dispute.created_at)).scalars().all()
    items = db.execute(
        select(OutreachItem).where(OutreachItem.account_id == account.id, OutreachItem.status != "Queued")
        .order_by(OutreachItem.created_at)
    ).scalars().all()
    plans = db.execute(select(PaymentPlan).where(PaymentPlan.account_id == account.id).order_by(PaymentPlan.created_at)).scalars().all()
    events = db.execute(
        select(AuditLog).where(AuditLog.client_id == ctx.client.id, AuditLog.entity == "DebtorAccount", AuditLog.entity_id == account.id)
        .order_by(AuditLog.created_at)
    ).scalars().all()
    timeline = [{"at": account.created_at, "kind": "uploaded", "text": "Added from upload"}]
    timeline += [{"at": i.sent_at or i.updated_at, "kind": "message",
                  "text": f"{i.template_key.replace('_', ' ').title()} — {i.status.lower()}"
                          + (f" by {i.channel}" if i.channel else "") + (f" ({i.skip_reason})" if i.status == "Skipped" else "")
                          + (f"; replied: {i.reply_category}" if i.reply_category else "")} for i in items]
    timeline += [{"at": p.created_at, "kind": "payment",
                  "text": f"{p.source}: ₦{p.amount_claimed / 100:,.2f} — {p.status.lower()}"} for p in payments]
    timeline += [{"at": d.created_at, "kind": "dispute", "text": f"Query raised ({d.type}) — {d.status.lower()}"} for d in disputes]
    timeline += [{"at": p.created_at, "kind": "plan", "text": f"Chose {p.instalment_count}-instalment plan — {p.status.lower()}"} for p in plans]
    timeline += [{"at": e.created_at, "kind": "status", "text": _event_text(e)} for e in events if _event_text(e)]
    timeline.sort(key=lambda t: t["at"])
    return {
        "account": serialize.account(account),
        "payments": [serialize.payment(p) for p in payments],
        "disputes": [serialize.dispute(d) for d in disputes],
        "plans": [serialize.plan(p) for p in plans],
        "messages": [serialize.outreach_item(i) for i in items],
        "timeline": timeline,
    }


def _event_text(e) -> str | None:
    after = json.loads(e.after) if e.after else {}
    if e.action == "account.status_changed":
        return f"Status → {after.get('status')} ({after.get('reason')})"
    if e.action == "account.opted_out":
        return "Opted out of messages"
    return None


def _account_action(account_id: str, ctx: ClientContext, db: Session, fn):
    account = _owned(db, DebtorAccount, account_id, ctx)
    _action(fn, db, account, ctx.actor)
    db.commit()
    return serialize.account(account)


@router.post("/accounts/{account_id}/pause")
def pause(account_id: str, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    return _account_action(account_id, ctx, db, service.pause_account)


@router.post("/accounts/{account_id}/resume")
def resume(account_id: str, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    return _account_action(account_id, ctx, db, service.resume_account)


@router.post("/accounts/{account_id}/withdraw")
def withdraw(account_id: str, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    return _account_action(account_id, ctx, db, service.withdraw_account)


class RecordPaymentIn(BaseModel):
    amount: str  # naira, as typed
    paid_on: date
    note: str | None = Field(default=None, max_length=120)


@router.post("/accounts/{account_id}/payments", status_code=201)
def record_payment(account_id: str, body: RecordPaymentIn, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    account = _owned(db, DebtorAccount, account_id, ctx)
    try:
        amount = naira_to_kobo(body.amount)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    payment = _action(service.record_client_payment, db, account, amount=amount, paid_on=body.paid_on, note=body.note,
                      actor=ctx.actor, today=_today())
    db.commit()
    return serialize.payment(payment)


# ---------------------------------------------------------------- confirmations


def _payment_row(db: Session, p: DebtorPayment) -> dict:
    out = serialize.payment(p)
    account = db.get(DebtorAccount, p.account_id)
    out["account"] = {"id": account.id, "reference_code": account.reference_code, "debtor_name": account.debtor_name,
                      "balance": account.balance, "description": account.description}
    return out


@router.get("/confirmations")
def confirmations(status: str = "Proof submitted", ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    rows = db.execute(
        select(DebtorPayment).where(DebtorPayment.client_id == ctx.client.id, DebtorPayment.status == status).order_by(DebtorPayment.created_at)
    ).scalars().all()
    return [_payment_row(db, p) for p in rows]


class ConfirmIn(BaseModel):
    amount_received: str | None = None  # naira; defaults to the amount claimed


@router.post("/payments/{payment_id}/confirm")
def confirm(payment_id: str, body: ConfirmIn, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    payment = _owned(db, DebtorPayment, payment_id, ctx)
    amount = None
    if body.amount_received not in (None, ""):
        try:
            amount = naira_to_kobo(body.amount_received)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    _action(service.confirm_payment, db, payment, amount, ctx.actor)
    db.commit()
    return _payment_row(db, payment)


class RejectIn(BaseModel):
    reason: str
    note: str | None = None


@router.post("/payments/{payment_id}/reject")
def reject(payment_id: str, body: RejectIn, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    payment = _owned(db, DebtorPayment, payment_id, ctx)
    _action(service.reject_payment, db, payment, body.reason, body.note, ctx.actor)
    db.commit()
    return _payment_row(db, payment)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@router.post("/payments/{payment_id}/reverse")
def reverse(payment_id: str, body: ReverseIn, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    payment = _owned(db, DebtorPayment, payment_id, ctx)
    _action(service.reverse_payment, db, payment, body.reason, ctx.actor)
    db.commit()
    return _payment_row(db, payment)


# ---------------------------------------------------------------- disputes


@router.get("/disputes")
def disputes(status: str = "Open", ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    stmt = select(Dispute).where(Dispute.client_id == ctx.client.id)
    if status != "All":
        stmt = stmt.where(Dispute.status == status)
    out = []
    for d in db.execute(stmt.order_by(Dispute.created_at)).scalars():
        row = serialize.dispute(d)
        account = db.get(DebtorAccount, d.account_id)
        row["account"] = {"id": account.id, "reference_code": account.reference_code, "debtor_name": account.debtor_name,
                          "balance": account.balance, "original_amount": account.original_amount,
                          "description": account.description}
        out.append(row)
    return out


class ResolveIn(BaseModel):
    outcome: str  # Uphold / Adjust / Withdraw
    new_balance: str | None = None  # naira, for Adjust
    note: str | None = None


@router.post("/disputes/{dispute_id}/resolve")
def resolve(dispute_id: str, body: ResolveIn, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    dispute = _owned(db, Dispute, dispute_id, ctx)
    new_balance = None
    if body.outcome == "Adjust":
        try:
            new_balance = naira_to_kobo(body.new_balance)
        except ValueError as exc:
            raise HTTPException(422, f"Corrected balance: {exc}") from exc
    _action(service.resolve_dispute, db, dispute, outcome=body.outcome, new_balance=new_balance, note=body.note, actor=ctx.actor)
    db.commit()
    return serialize.dispute(dispute)


# ---------------------------------------------------------------- invoices


@router.get("/invoices")
def invoices(ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Invoice).where(Invoice.client_id == ctx.client.id, Invoice.status != "Draft").order_by(Invoice.period_start.desc())
    ).scalars()
    return {"invoices": [serialize.invoice(i) for i in rows], "pay_to": settings_store.get(db, "waterline_bank_details")}


@router.get("/invoices/{invoice_id}")
def invoice_detail(invoice_id: str, ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    inv = _owned(db, Invoice, invoice_id, ctx)
    if inv.status == "Draft":
        raise HTTPException(404, "Not found.")
    return {"invoice": serialize.invoice(inv, lines=True), "pay_to": settings_store.get(db, "waterline_bank_details")}


# ---------------------------------------------------------------- settings + team


class SettingsIn(BaseModel):
    business_name: str | None = Field(default=None, min_length=2, max_length=200)
    business_type: str | None = None
    state: str | None = None
    city: str | None = Field(default=None, min_length=2, max_length=80)
    bank_name: str | None = Field(default=None, min_length=2, max_length=120)
    account_number: str | None = None
    account_name: str | None = Field(default=None, min_length=2, max_length=200)
    default_rules: dict | None = None


@router.put("/settings")
def update_settings(body: SettingsIn, ctx: ClientContext = Depends(require_owner), db: Session = Depends(get_db)):
    client = ctx.client
    changes = body.model_dump(exclude_unset=True)
    if "business_type" in changes and changes["business_type"] not in BUSINESS_TYPES:
        raise HTTPException(422, "Choose a business type.")
    if "state" in changes and changes["state"] not in NIGERIAN_STATES:
        raise HTTPException(422, "Choose a state.")
    if "account_number" in changes and not (changes["account_number"].isdigit() and len(changes["account_number"]) == 10):
        raise HTTPException(422, "Account number must be 10 digits (NUBAN).")
    if "default_rules" in changes:
        try:
            changes["default_rules"] = json.dumps(validate_rules(changes["default_rules"])) if changes["default_rules"] else None
        except RulesInvalid as exc:
            raise HTTPException(422, str(exc)) from exc
    bank_fields = {"bank_name", "account_number", "account_name"}
    before = {k: getattr(client, k) for k in changes if k not in bank_fields}
    for key, value in changes.items():
        setattr(client, key, value.strip() if isinstance(value, str) and key != "default_rules" else value)
    audit.record(db, ctx.actor, "client.settings_changed", "Client", client.id,
                 before=before, after={k: (v if k not in bank_fields else "[changed]") for k, v in changes.items()})
    if bank_fields & set(changes):
        # debtors pay straight into this account, so a change is worth a human look
        service.notify_ops(db, f"Bank details changed: {client.business_name}",
                           f"{client.business_name} changed the bank account debtors pay into. Please re-check it.",
                           "ops_bank_changed", client.id)
        service.notify_client(db, client.id, "Your payout bank details were changed",
                              "The bank account your customers pay into was just changed on Waterline Collections. "
                              "If this wasn't you, contact us immediately.", "client_bank_changed")
    db.commit()
    return serialize.client(client)


@router.get("/team")
def team(ctx: ClientContext = Depends(require_client), db: Session = Depends(get_db)):
    users = db.execute(select(ClientUser).where(ClientUser.client_id == ctx.client.id).order_by(ClientUser.created_at)).scalars()
    return {"users": [serialize.user(u) for u in users], "max_teammates": settings_store.get(db, "max_teammates")}


class InviteIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    email: str = Field(min_length=5, max_length=254)


@router.post("/team", status_code=201)
def invite(body: InviteIn, ctx: ClientContext = Depends(require_owner), db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if not upload.EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    members = db.execute(
        select(func.count()).select_from(ClientUser).where(
            ClientUser.client_id == ctx.client.id, ClientUser.role == "Member", ClientUser.is_active.is_(True)
        )
    ).scalar_one()
    if members >= int(settings_store.get(db, "max_teammates")):
        raise HTTPException(409, f"You can add up to {settings_store.get(db, 'max_teammates')} teammates.")
    if db.execute(select(ClientUser.id).where(ClientUser.email_index == crypto.blind_index(email))).first():
        raise HTTPException(409, "That email already has a Waterline Collections account.")
    user = ClientUser(client_id=ctx.client.id, name=body.name.strip(), email=email, email_index=crypto.blind_index(email), role="Member")
    db.add(user)
    db.flush()
    token = issue_link(db, user, "invite", INVITE_TTL)
    emailer.send(db, email, f"{ctx.user.name} invited you to Waterline Collections",
                 f"{ctx.user.name} added you to {ctx.client.business_name} on Waterline Collections. "
                 f"Set your password here (valid for 7 days):\n\n{emailer.site_url('/collections/reset/?invite=1&token=' + token)}",
                 kind="client_invite", client_id=ctx.client.id)
    audit.record(db, ctx.actor, "team.invited", "ClientUser", user.id, after={"name": user.name})
    db.commit()
    return serialize.user(user)


@router.delete("/team/{user_id}")
def remove_teammate(user_id: str, ctx: ClientContext = Depends(require_owner), db: Session = Depends(get_db)):
    user = _owned(db, ClientUser, user_id, ctx)
    if user.role == "Owner":
        raise HTTPException(409, "The owner can't be removed.")
    user.is_active = False
    end_all_sessions(db, "client", user.id)
    audit.record(db, ctx.actor, "team.removed", "ClientUser", user.id)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- data export (NDPA)


@router.get("/export.csv")
def export(ctx: ClientContext = Depends(require_owner), db: Session = Depends(get_db)):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["reference_code", "debtor_name", "phone", "email", "description", "dependant_first_name",
                     "original_amount_naira", "balance_naira", "due_date", "status", "opted_out", "confirmed_paid_naira"])
    accounts = db.execute(select(DebtorAccount).where(DebtorAccount.client_id == ctx.client.id).order_by(DebtorAccount.created_at)).scalars()
    for a in accounts:
        paid = db.execute(
            select(func.coalesce(func.sum(DebtorPayment.amount_confirmed), 0)).where(DebtorPayment.account_id == a.id, DebtorPayment.status == "Confirmed")
        ).scalar_one()
        writer.writerow([a.reference_code, a.debtor_name, a.phone or "", a.email or "", a.description,
                         a.dependant_first_name or "", f"{a.original_amount / 100:.2f}", f"{a.balance / 100:.2f}",
                         a.due_date.strftime("%d/%m/%Y") if a.due_date else "", a.status,
                         "yes" if a.opted_out_at else "no", f"{paid / 100:.2f}"])
    audit.record(db, ctx.actor, "client.data_exported", "Client", ctx.client.id)
    db.commit()
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="waterline-collections-export.csv"'})


class DeletionIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@router.post("/deletion-request")
def deletion_request(body: DeletionIn, ctx: ClientContext = Depends(require_owner), db: Session = Depends(get_db)):
    audit.record(db, ctx.actor, "client.deletion_requested", "Client", ctx.client.id, after={"note": body.note})
    service.notify_ops(db, f"Data deletion request: {ctx.client.business_name}",
                       f"{ctx.user.name} asked for {ctx.client.business_name}'s data to be deleted. Note: {body.note or '-'}",
                       "ops_deletion_request", ctx.client.id)
    db.commit()
    return {"message": "We've received your request and will confirm by email within 30 days."}
