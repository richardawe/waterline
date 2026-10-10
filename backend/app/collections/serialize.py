import json

from app.collections import storage
from app.collections.models import (
    Client,
    ClientUser,
    DebtorAccount,
    DebtorPayment,
    Dispute,
    Invoice,
    OutreachItem,
    PaymentPlan,
    UploadBatch,
)


def client(c: Client, *, full: bool = True) -> dict:
    out = {
        "id": c.id,
        "business_name": c.business_name,
        "business_type": c.business_type,
        "state": c.state,
        "city": c.city,
        "logo_url": c.logo_url,
        "status": c.status,
        "status_reason": c.status_reason,
        "fee_percent": float(c.fee_percent),
        "attribution_days": c.attribution_days,
        "upload_row_cap": c.upload_row_cap,
        "created_at": c.created_at,
    }
    if full:
        out.update({
            "bank_name": c.bank_name,
            "account_number": c.account_number,
            "account_name": c.account_name,
            "terms_accepted_at": c.terms_accepted_at,
            "dpa_accepted_at": c.dpa_accepted_at,
            "privacy_accepted_at": c.privacy_accepted_at,
            "default_rules": json.loads(c.default_rules) if c.default_rules else None,
        })
    return out


def client_for_ops(c: Client) -> dict:
    out = client(c)
    out.update({
        "verification_notes": c.verification_notes,
        "verification_checklist": json.loads(c.verification_checklist) if c.verification_checklist else {},
        "approved_by": c.approved_by,
        "approved_at": c.approved_at,
        "flagged_reason": c.flagged_reason,
        "flagged_at": c.flagged_at,
    })
    return out


def user(u: ClientUser) -> dict:
    return {
        "id": u.id, "name": u.name, "email": u.email, "phone": u.phone, "job_title": u.job_title,
        "role": u.role, "email_verified": u.email_verified_at is not None, "is_active": u.is_active,
    }


def batch(b: UploadBatch) -> dict:
    return {
        "id": b.id, "file_name": b.file_name, "status": b.status, "row_count": b.row_count,
        "valid_count": b.valid_count, "error_count": b.error_count, "duplicate_count": b.duplicate_count,
        "imported_count": b.imported_count, "attested_at": b.attested_at, "created_at": b.created_at,
        "rules": json.loads(b.rules) if b.rules else None, "flagged": b.flagged_at is not None,
    }


def account(a: DebtorAccount, *, ops: bool = False) -> dict:
    out = {
        "id": a.id, "reference_code": a.reference_code, "debtor_name": a.debtor_name, "phone": a.phone,
        "email": a.email, "description": a.description, "dependant_first_name": a.dependant_first_name,
        "original_amount": a.original_amount, "balance": a.balance, "due_date": a.due_date, "status": a.status,
        "opted_out": a.opted_out_at is not None, "opted_out_at": a.opted_out_at,
        "last_contacted_at": a.last_contacted_at, "contact_count_7d": a.contact_count_7d,
        "batch_id": a.batch_id, "created_at": a.created_at, "closed_at": a.closed_at,
        "anonymised": a.anonymised_at is not None,
    }
    if ops:
        out["client_id"] = a.client_id
    return out


def payment(p: DebtorPayment, *, with_proof: bool = True) -> dict:
    return {
        "id": p.id, "account_id": p.account_id, "amount_claimed": p.amount_claimed,
        "amount_confirmed": p.amount_confirmed, "paid_on": p.paid_on, "sender_name": p.sender_name,
        "source": p.source, "status": p.status, "possible_duplicate": p.possible_duplicate,
        "confirmed_at": p.confirmed_at, "reject_reason": p.reject_reason, "fee_eligible": p.fee_eligible,
        "fee_amount": p.fee_amount, "created_at": p.created_at,
        "proof_url": storage.signed_url(p.proof_file_url) if with_proof else None,
        "has_proof": bool(p.proof_file_url),
    }


def dispute(d: Dispute) -> dict:
    return {
        "id": d.id, "account_id": d.account_id, "type": d.type, "details": d.details,
        "evidence_url": storage.signed_url(d.evidence_url), "raised_by": d.raised_by, "status": d.status,
        "resolution_note": d.resolution_note, "resolved_at": d.resolved_at, "created_at": d.created_at,
    }


def plan(p: PaymentPlan) -> dict:
    return {
        "id": p.id, "instalment_count": p.instalment_count, "instalment_amount": p.instalment_amount,
        "total_amount": p.total_amount, "schedule": json.loads(p.schedule), "status": p.status,
        "created_at": p.created_at,
    }


def outreach_item(i: OutreachItem, *, text: bool = True) -> dict:
    return {
        "id": i.id, "account_id": i.account_id, "client_id": i.client_id, "template_key": i.template_key,
        "template_version": i.template_version, "rendered_text": i.rendered_text if text else None,
        "channel": i.channel, "queue_date": i.queue_date, "scheduled_for": i.scheduled_for, "status": i.status,
        "sent_at": i.sent_at, "skip_reason": i.skip_reason, "reply_category": i.reply_category,
        "reply_note": i.reply_note, "replied_at": i.replied_at,
    }


def invoice(inv: Invoice, *, lines: bool = False) -> dict:
    out = {
        "id": inv.id, "number": inv.number, "client_id": inv.client_id, "period_start": inv.period_start,
        "period_end": inv.period_end, "total_confirmed": inv.total_confirmed, "fee_percent": float(inv.fee_percent),
        "fee_amount": inv.fee_amount, "status": inv.status, "issued_at": inv.issued_at, "due_at": inv.due_at,
        "paid_at": inv.paid_at, "line_count": len(inv.lines),
    }
    if lines:
        out["lines"] = [
            {"payment_id": l.payment_id, "reference_code": l.reference_code, "debtor_name": l.debtor_name,
             "amount": l.amount, "paid_on": l.paid_on, "fee": l.fee}
            for l in sorted(inv.lines, key=lambda l: (l.paid_on, l.reference_code))
        ]
    return out
