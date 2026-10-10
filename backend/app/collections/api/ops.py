"""Waterline ops API: approvals, the manual outreach queue, replies,
invoices, templates, config and audit export. Separate auth (password +
TOTP) from clients; ops can never confirm a payment on a client's behalf —
there is deliberately no endpoint for it."""

import csv
import io
import json
from datetime import date, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.collections import (
    audit,
    billing,
    crypto,
    outreach,
    serialize,
    service,
    settings_store,
    templates,
)
from app.collections.auth import OpsContext, end_session, require_ops, require_ops_admin
from app.collections.models import (
    AuditLog,
    Client,
    ClientStatus,
    ClientUser,
    DebtorAccount,
    DebtorPayment,
    Invoice,
    MessageTemplate,
    OutreachItem,
    UploadBatch,
)
from app.collections.phone import normalise_phone
from app.collections.timeutil import (
    in_contact_window,
    lagos_date,
    lagos_datetime,
    utcnow,
)
from app.db import get_db

router = APIRouter(prefix="/collections/ops", tags=["collections-ops"])

CHECKLIST = ["phone_call_made", "business_exists", "bank_name_matches", "contact_confirmed"]


def _act(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (service.ActionError, outreach.OutreachRefused, templates.TemplateRejected) as exc:
        raise HTTPException(409, str(exc)) from exc


def _get(db: Session, model, entity_id: str):
    row = db.get(model, entity_id)
    if row is None:
        raise HTTPException(404, "Not found.")
    return row


@router.get("/me")
def me(ctx: OpsContext = Depends(require_ops)):
    return {"id": ctx.user.id, "name": ctx.user.name, "email": ctx.user.email, "role": ctx.user.role}


@router.post("/logout")
def logout(request: Request, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    end_session(db, request)
    db.commit()
    return {"ok": True}


@router.get("/overview")
def overview(ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    now = utcnow()
    today = lagos_date(now)
    pending = db.execute(select(func.count()).select_from(Client).where(Client.status == ClientStatus.PENDING)).scalar_one()
    flagged = db.execute(select(func.count()).select_from(Client).where(Client.flagged_at.is_not(None))).scalar_one()
    return {
        "today": today,
        "in_contact_window": in_contact_window(now, settings_store.holidays(db)),
        "pending_approvals": pending,
        "flagged_clients": flagged,
        "queue_remaining": outreach.queue_count(db, today),
        "overdue_confirmations": len(billing.overdue_confirmations(db, today)),
        "stale_disputes": len(billing.stale_disputes(db, today)),
        "draft_invoices": db.execute(select(func.count()).select_from(Invoice).where(Invoice.status == "Draft")).scalar_one(),
    }


# ---------------------------------------------------------------- clients + approvals


def _client_stats(db: Session, client_id: str) -> dict:
    accounts = db.execute(select(func.count()).select_from(DebtorAccount).where(DebtorAccount.client_id == client_id)).scalar_one()
    recovered = db.execute(
        select(func.coalesce(func.sum(DebtorPayment.amount_confirmed), 0)).where(DebtorPayment.client_id == client_id, DebtorPayment.status == "Confirmed")
    ).scalar_one()
    return {"accounts": accounts, "recovered": int(recovered)}


@router.get("/clients")
def clients(status: str | None = None, flagged: bool = False, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    stmt = select(Client).order_by(Client.created_at.desc())
    if status:
        stmt = stmt.where(Client.status == status)
    if flagged:
        stmt = stmt.where(Client.flagged_at.is_not(None))
    out = []
    for c in db.execute(stmt).scalars():
        row = serialize.client_for_ops(c)
        row.update(_client_stats(db, c.id))
        out.append(row)
    return out


@router.get("/clients/{client_id}")
def client_detail(client_id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    c = _get(db, Client, client_id)
    users = db.execute(select(ClientUser).where(ClientUser.client_id == c.id)).scalars()
    batches = db.execute(select(UploadBatch).where(UploadBatch.client_id == c.id).order_by(UploadBatch.created_at.desc())).scalars()
    row = serialize.client_for_ops(c)
    row.update(_client_stats(db, c.id))
    return {"client": row, "users": [serialize.user(u) for u in users], "batches": [serialize.batch(b) for b in batches],
            "checklist_items": CHECKLIST}


class VerificationIn(BaseModel):
    notes: str | None = Field(default=None, max_length=5000)
    checklist: dict[str, bool] = {}


@router.put("/clients/{client_id}/verification")
def record_verification(client_id: str, body: VerificationIn, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    c = _get(db, Client, client_id)
    unknown = set(body.checklist) - set(CHECKLIST)
    if unknown:
        raise HTTPException(422, f"Unknown checklist items: {', '.join(sorted(unknown))}")
    before = {"notes": c.verification_notes, "checklist": c.verification_checklist}
    c.verification_notes = body.notes
    c.verification_checklist = json.dumps(body.checklist)
    audit.record(db, ctx.actor, "client.verification_recorded", "Client", c.id, client_id=c.id,
                 before=before, after={"notes": body.notes, "checklist": body.checklist})
    db.commit()
    return serialize.client_for_ops(c)


class StatusIn(BaseModel):
    status: str  # Approved / Rejected / Suspended
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/clients/{client_id}/status")
def set_client_status(client_id: str, body: StatusIn, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    c = _get(db, Client, client_id)
    allowed = {
        ClientStatus.APPROVED: {ClientStatus.PENDING, ClientStatus.SUSPENDED, ClientStatus.REJECTED},
        ClientStatus.REJECTED: {ClientStatus.PENDING},
        ClientStatus.SUSPENDED: {ClientStatus.APPROVED},
    }
    if body.status not in allowed or c.status not in allowed[body.status]:
        raise HTTPException(409, f"Can't move a {c.status} client to {body.status}.")
    if body.status in (ClientStatus.REJECTED, ClientStatus.SUSPENDED) and not (body.reason or "").strip():
        raise HTTPException(422, "Give a reason.")
    if body.status == ClientStatus.APPROVED and c.status == ClientStatus.PENDING:
        checklist = json.loads(c.verification_checklist or "{}")
        missing = [k for k in CHECKLIST if not checklist.get(k)]
        if missing:
            raise HTTPException(409, f"Complete the verification checklist first ({', '.join(missing)}).")
    before = c.status
    c.status = body.status
    c.status_reason = (body.reason or "").strip() or None
    if body.status == ClientStatus.APPROVED:
        c.approved_by, c.approved_at = ctx.user.id, utcnow()
    withdrawn = 0
    if body.status == ClientStatus.SUSPENDED:
        # suspension stops outreach immediately, including today's queue
        for item in db.execute(select(OutreachItem).where(OutreachItem.client_id == c.id, OutreachItem.status == "Queued")).scalars():
            item.status, item.skip_reason = "Skipped", "Client suspended"
            withdrawn += 1
    audit.record(db, ctx.actor, f"client.{body.status.lower()}", "Client", c.id, client_id=c.id,
                 before={"status": before}, after={"status": c.status, "reason": c.status_reason, "queue_items_withdrawn": withdrawn})
    messages = {
        ClientStatus.APPROVED: ("Your business is verified", "Good news — we've verified your business. "
                                "Outreach to your uploaded balances will start on each batch's campaign start date."),
        ClientStatus.REJECTED: ("About your Waterline Collections application",
                                f"We weren't able to verify your business. Reason: {c.status_reason}\n\nReply to this email if you'd like to discuss it."),
        ClientStatus.SUSPENDED: ("Your Waterline Collections outreach is paused",
                                 f"We've paused all outreach for your account. Reason: {c.status_reason}\n\nReply to this email to sort it out."),
    }
    subject, text = messages[body.status]
    service.notify_client(db, c.id, subject, text, f"client_{body.status.lower()}")
    db.commit()
    return serialize.client_for_ops(c)


class LimitsIn(BaseModel):
    upload_row_cap: int | None = Field(default=None, ge=1, le=100000)
    fee_percent: float | None = Field(default=None, ge=0, le=50)
    attribution_days: int | None = Field(default=None, ge=1, le=365)
    clear_flag: bool = False


@router.put("/clients/{client_id}/limits")
def set_limits(client_id: str, body: LimitsIn, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    c = _get(db, Client, client_id)
    changes = body.model_dump(exclude_unset=True, exclude={"clear_flag"})
    if ("fee_percent" in changes or "attribution_days" in changes) and ctx.user.role != "Admin":
        raise HTTPException(403, "Only Waterline Admin can change fees.")
    before = {k: (float(getattr(c, k)) if k == "fee_percent" else getattr(c, k)) for k in changes}
    for k, v in changes.items():
        setattr(c, k, v)
    if body.clear_flag:
        before["flagged_reason"] = c.flagged_reason
        c.flagged_reason = c.flagged_at = None
    audit.record(db, ctx.actor, "client.limits_changed", "Client", c.id, client_id=c.id, before=before,
                 after={**changes, "flag_cleared": body.clear_flag})
    db.commit()
    return serialize.client_for_ops(c)


@router.post("/clients/{client_id}/anonymise")
def anonymise_client_data(client_id: str, ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    """Honour a client's deletion request: anonymise every debtor account."""
    c = _get(db, Client, client_id)
    rows = db.execute(select(DebtorAccount).where(DebtorAccount.client_id == c.id)).scalars().all()
    for account in rows:
        billing.anonymise_account(db, account, ctx.actor, "client deletion request")
    audit.record(db, ctx.actor, "client.data_deleted", "Client", c.id, client_id=c.id, after={"accounts": len(rows)})
    db.commit()
    return {"anonymised": len(rows)}


# ---------------------------------------------------------------- queue


def _queue_row(db: Session, item: OutreachItem, account: DebtorAccount, client: Client) -> dict:
    row = serialize.outreach_item(item)
    row.update({
        "debtor_name": account.debtor_name, "phone": account.phone, "has_email": bool(account.email),
        "reference_code": account.reference_code, "client_name": client.business_name, "account_status": account.status,
    })
    if account.phone and item.status == "Queued":
        row["links"] = outreach.links(account.phone, item.rendered_text)
    return row


@router.post("/queue/build")
def build_queue(ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    result = outreach.build_queue(db)
    db.commit()
    return result


@router.get("/queue")
def queue(day: date | None = None, status: str | None = None, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    now = utcnow()
    day = day or lagos_date(now)
    stmt = (
        select(OutreachItem, DebtorAccount, Client)
        .join(DebtorAccount, DebtorAccount.id == OutreachItem.account_id)
        .join(Client, Client.id == OutreachItem.client_id)
        .where(OutreachItem.queue_date == day)
        .order_by(Client.business_name, OutreachItem.created_at)
    )
    if status:
        stmt = stmt.where(OutreachItem.status == status)
    rows = db.execute(stmt).all()
    return {
        "date": day,
        "in_contact_window": in_contact_window(now, settings_store.holidays(db)),
        "items": [_queue_row(db, i, a, c) for i, a, c in rows],
        "skip_reasons": outreach.SKIP_REASONS,
        "reply_categories": outreach.REPLY_CATEGORIES,
    }


class SentIn(BaseModel):
    channel: str


def _commit_then_raise(db: Session, fn, *args, **kwargs):
    """Rule refusals may still have changed state (an ineligible item gets
    pulled from the queue), so keep that before reporting the refusal."""
    try:
        result = fn(*args, **kwargs)
    except outreach.OutreachRefused as exc:
        db.commit()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return result


@router.post("/queue/{item_id}/sent")
def mark_sent(item_id: str, body: SentIn, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    item = _get(db, OutreachItem, item_id)
    _commit_then_raise(db, outreach.mark_sent, db, item, body.channel, ctx.actor)
    return _queue_row(db, item, db.get(DebtorAccount, item.account_id), db.get(Client, item.client_id))


class SkipIn(BaseModel):
    reason: str
    note: str | None = None


@router.post("/queue/{item_id}/skip")
def skip(item_id: str, body: SkipIn, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    item = _get(db, OutreachItem, item_id)
    _commit_then_raise(db, outreach.skip, db, item, body.reason, body.note, ctx.actor)
    return _queue_row(db, item, db.get(DebtorAccount, item.account_id), db.get(Client, item.client_id))


class ReplyIn(BaseModel):
    category: str
    note: str | None = None


@router.post("/queue/{item_id}/reply")
def log_reply(item_id: str, body: ReplyIn, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    item = _get(db, OutreachItem, item_id)
    _act(outreach.log_reply, db, item, body.category, body.note, ctx.actor)
    db.commit()
    return _queue_row(db, item, db.get(DebtorAccount, item.account_id), db.get(Client, item.client_id))


@router.get("/replies")
def replies(days: int = 14, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=days)
    rows = db.execute(
        select(OutreachItem, DebtorAccount, Client)
        .join(DebtorAccount, DebtorAccount.id == OutreachItem.account_id)
        .join(Client, Client.id == OutreachItem.client_id)
        .where(OutreachItem.replied_at >= since)
        .order_by(OutreachItem.replied_at.desc())
    ).all()
    return [_queue_row(db, i, a, c) for i, a, c in rows]


@router.get("/accounts/search")
def search_accounts(q: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    """Find a debtor by reference code or phone (e.g. to log a reply that came
    in later). Reference codes and phone blind indexes are exact matches."""
    q = q.strip()
    conditions = [DebtorAccount.reference_code == q.upper()]
    try:
        conditions.append(DebtorAccount.phone_index == crypto.blind_index(normalise_phone(q)))
    except ValueError:
        pass
    rows = db.execute(select(DebtorAccount).where(or_(*conditions)).limit(50)).scalars().all()
    out = []
    for a in rows:
        row = serialize.account(a, ops=True)
        row["client_name"] = a.client.business_name
        last = db.execute(
            select(OutreachItem).where(OutreachItem.account_id == a.id, OutreachItem.status == "Sent")
            .order_by(OutreachItem.sent_at.desc())
        ).scalars().first()
        row["last_sent_item_id"] = last.id if last else None
        out.append(row)
    return out


@router.post("/accounts/{account_id}/reissue-link")
def reissue_link(account_id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    account = _get(db, DebtorAccount, account_id)
    if account.anonymised_at:
        raise HTTPException(409, "This account has been anonymised.")
    service.reissue_token(db, account, ctx.actor)
    db.commit()
    return {"link": outreach.debtor_link(account)}


# ---------------------------------------------------------------- follow-ups


@router.get("/follow-ups")
def follow_ups(ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    today = lagos_date(utcnow())

    def with_account(row: dict, account_id: str) -> dict:
        a = db.get(DebtorAccount, account_id)
        row["reference_code"], row["client_name"] = a.reference_code, a.client.business_name
        return row

    overdue = [with_account(serialize.payment(p, with_proof=False), p.account_id) for p in billing.overdue_confirmations(db, today)]
    stale = [with_account(serialize.dispute(d), d.account_id) for d in billing.stale_disputes(db, today)]
    rejected = db.execute(
        select(DebtorPayment).where(DebtorPayment.status == "Rejected", DebtorPayment.updated_at >= utcnow() - timedelta(days=14))
        .order_by(DebtorPayment.updated_at.desc())
    ).scalars().all()
    return {
        "overdue_confirmations": overdue,
        "stale_disputes": stale,
        "recent_rejections": [with_account(serialize.payment(p, with_proof=False), p.account_id) for p in rejected],
    }


# ---------------------------------------------------------------- invoices


@router.get("/invoices")
def invoices(status: str | None = None, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    stmt = select(Invoice).order_by(Invoice.period_start.desc(), Invoice.created_at.desc())
    if status:
        stmt = stmt.where(Invoice.status == status)
    out = []
    for inv in db.execute(stmt).scalars():
        row = serialize.invoice(inv)
        row["client_name"] = db.get(Client, inv.client_id).business_name
        out.append(row)
    return out


@router.get("/invoices/{invoice_id}")
def invoice_detail(invoice_id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    inv = _get(db, Invoice, invoice_id)
    row = serialize.invoice(inv, lines=True)
    row["client_name"] = db.get(Client, inv.client_id).business_name
    return row


class DraftIn(BaseModel):
    month: date  # any day in the month to invoice


@router.post("/invoices/draft")
def draft(body: DraftIn, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    drafted = billing.draft_invoices(db, body.month, ctx.actor)
    db.commit()
    return [serialize.invoice(i) for i in drafted]


@router.post("/invoices/{invoice_id}/issue")
def issue(invoice_id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    inv = _get(db, Invoice, invoice_id)
    _act(billing.issue_invoice, db, inv, ctx.actor, lagos_date(utcnow()))
    db.commit()
    return serialize.invoice(inv)


@router.post("/invoices/{invoice_id}/paid")
def paid(invoice_id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    inv = _get(db, Invoice, invoice_id)
    _act(billing.mark_invoice_paid, db, inv, ctx.actor)
    db.commit()
    return serialize.invoice(inv)


# ---------------------------------------------------------------- templates + config (Admin)


@router.get("/templates")
def list_templates(ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    templates.ensure_defaults(db)
    db.commit()
    rows = db.execute(select(MessageTemplate).order_by(MessageTemplate.key, MessageTemplate.language, MessageTemplate.version.desc())).scalars()
    return [{"id": t.id, "key": t.key, "language": t.language, "version": t.version, "body": t.body,
             "is_active": t.is_active, "created_by": t.created_by, "created_at": t.created_at} for t in rows]


class TemplateIn(BaseModel):
    body: str = Field(min_length=20, max_length=1000)
    language: str = "en"


@router.post("/templates/{key}")
def save_template(key: str, body: TemplateIn, ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    tpl = _act(templates.new_version, db, key, body.body.strip(), body.language, ctx.user.id)
    audit.record(db, ctx.actor, "template.versioned", "MessageTemplate", tpl.id, after={"key": key, "version": tpl.version})
    db.commit()
    return {"id": tpl.id, "key": tpl.key, "version": tpl.version}


@router.get("/config")
def get_config(ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    return settings_store.all_values(db)


class ConfigIn(BaseModel):
    value: object


@router.put("/config/{key}")
def set_config(key: str, body: ConfigIn, ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    before = settings_store.get(db, key)
    try:
        settings_store.set_value(db, key, body.value, ctx.user.id)
    except KeyError as exc:
        raise HTTPException(404, "Unknown setting.") from exc
    audit.record(db, ctx.actor, "config.changed", "Config", key, before=before, after=body.value)
    db.commit()
    return {"key": key, "value": body.value}


# ---------------------------------------------------------------- audit + jobs


@router.get("/audit")
def audit_log(client_id: str | None = None, action: str | None = None, limit: int = 200,
              ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))
    if client_id:
        stmt = stmt.where(AuditLog.client_id == client_id)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    return [_audit_row(r) for r in db.execute(stmt).scalars()]


def _audit_row(r: AuditLog) -> dict:
    return {"id": r.id, "created_at": r.created_at, "actor_type": r.actor_type, "actor_id": r.actor_id,
            "client_id": r.client_id, "action": r.action, "entity": r.entity, "entity_id": r.entity_id,
            "before": r.before, "after": r.after, "ip": r.ip}


@router.get("/audit.csv")
def audit_export(client_id: str | None = None, start: date | None = None, end: date | None = None,
                 ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    stmt = select(AuditLog).order_by(AuditLog.id)
    if client_id:
        stmt = stmt.where(AuditLog.client_id == client_id)
    if start:
        stmt = stmt.where(AuditLog.created_at >= lagos_datetime(start, time(0, 0)))
    if end:
        stmt = stmt.where(AuditLog.created_at < lagos_datetime(end + timedelta(days=1), time(0, 0)))
    out = io.StringIO()
    writer = csv.writer(out)
    cols = ["id", "created_at", "actor_type", "actor_id", "client_id", "action", "entity", "entity_id", "before", "after", "ip"]
    writer.writerow(cols)
    for r in db.execute(stmt).scalars():
        row = _audit_row(r)
        writer.writerow([row[c] for c in cols])
    audit.record(db, ctx.actor, "audit.exported", "AuditLog", None, client_id=client_id,
                 after={"start": start, "end": end})
    db.commit()
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="collections-audit.csv"'})


@router.post("/jobs/daily")
def run_daily(ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    result = billing.run_daily(db)
    db.commit()
    return result
