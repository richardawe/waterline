"""Team expenses: any ops user records money they spent for Waterline (with
an optional receipt); Admins approve or reject each claim and mark it paid
once they've transferred the money to the person's saved bank details.

- Claims are Submitted → Approved → Paid, or Rejected. An Admin can mark a
  Submitted claim paid straight away (approve and pay in one step).
- Only the person who made a claim can delete it, and only while it's still
  Submitted. Nobody else sees another person's claims except Admins.
- Receipts (JPG/PNG/PDF, ≤5 MB, checked by content) live in private storage
  and are only served through 15-minute signed links.
- Admins get an email when a claim comes in; the person gets one when it's
  rejected or paid (through the collections mailbox, if connected)."""

import json
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collections import audit, emailer, storage
from app.collections.auth import OpsContext, require_ops, require_ops_admin
from app.collections.models import Expense, OpsUser
from app.collections.money import format_naira, naira_to_kobo
from app.collections.timeutil import lagos_date, utcnow
from app.db import get_db

router = APIRouter(prefix="/collections/ops", tags=["collections-ops"])

CATEGORIES = ("Transport", "Airtime & data", "Printing & stationery", "Meals", "Postage & courier", "Office supplies", "Other")
STATUSES = ("Submitted", "Approved", "Rejected", "Paid")
MAX_AMOUNT = 10_000_000_00  # ₦10m: anything bigger is a typo


def _payout(u: OpsUser | None) -> dict | None:
    return json.loads(u.payout_details) if u and u.payout_details else None


def _out(e: Expense, users: dict, *, admin: bool) -> dict:
    u = users.get(e.ops_user_id)
    row = {"id": e.id, "ops_user_id": e.ops_user_id, "name": u.name if u else "Former team member",
           "spent_on": e.spent_on, "amount": e.amount, "category": e.category, "description": e.description,
           "receipt_url": storage.signed_url(e.receipt_key), "status": e.status, "reject_reason": e.reject_reason,
           "decided_by": users[e.decided_by].name if e.decided_by in users else None, "decided_at": e.decided_at,
           "paid_on": e.paid_on, "paid_reference": e.paid_reference, "created_at": e.created_at}
    if admin:
        row["payout"] = _payout(u)
    return row


def _users(db: Session) -> dict:
    return {u.id: u for u in db.execute(select(OpsUser)).scalars()}


def _notify_admins(db: Session, e: Expense, who: OpsUser) -> None:
    for admin in db.execute(select(OpsUser).where(OpsUser.role == "Admin", OpsUser.is_active.is_(True))).scalars():
        if admin.id == who.id:
            continue
        emailer.send(db, admin.email, f"Expense claim from {who.name}: {format_naira(e.amount)}",
                     f"{who.name} added an expense claim.\n\n{e.category}, {e.spent_on:%d %b %Y}: {format_naira(e.amount)}\n"
                     f"{e.description}\n\nReview it in the ops console under Expenses:\n"
                     f"{emailer.site_url('/collections/ops/#expenses')}", kind="expense_submitted")


# ---------------------------------------------------------------- claims


@router.get("/expenses")
def list_expenses(scope: str = "mine", status: str = "", ctx: OpsContext = Depends(require_ops),
                  db: Session = Depends(get_db)):
    admin = ctx.user.role == "Admin"
    if scope == "all" and not admin:
        raise HTTPException(403, "Only Waterline Admin can see everyone's expenses.")
    stmt = select(Expense).order_by(Expense.spent_on.desc(), Expense.created_at.desc()).limit(500)
    if scope != "all":
        stmt = stmt.where(Expense.ops_user_id == ctx.user.id)
    if status in STATUSES:
        stmt = stmt.where(Expense.status == status)
    users = _users(db)
    rows = [_out(e, users, admin=admin and scope == "all") for e in db.execute(stmt).scalars()]
    return {"expenses": rows, "categories": CATEGORIES, "has_payout": bool(ctx.user.payout_details)}


@router.get("/expenses/summary")
def expenses_summary(ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    """What's owed to each person (Submitted + Approved) and what was paid this month."""
    users = _users(db)
    month_start = lagos_date(utcnow()).replace(day=1)
    people: dict = {}
    for e in db.execute(select(Expense).where(Expense.status.in_(("Submitted", "Approved", "Paid")))).scalars():
        p = people.setdefault(e.ops_user_id, {"ops_user_id": e.ops_user_id, "submitted": 0, "approved": 0,
                                              "paid_this_month": 0, "count_open": 0})
        if e.status == "Submitted":
            p["submitted"] += e.amount
            p["count_open"] += 1
        elif e.status == "Approved":
            p["approved"] += e.amount
            p["count_open"] += 1
        elif e.paid_on and e.paid_on >= month_start:
            p["paid_this_month"] += e.amount
    out = []
    for uid, p in people.items():
        u = users.get(uid)
        out.append({**p, "name": u.name if u else "Former team member", "payout": _payout(u),
                    "owed": p["submitted"] + p["approved"]})
    out.sort(key=lambda r: -r["owed"])
    return {"people": out, "total_owed": sum(p["owed"] for p in out),
            "total_paid_this_month": sum(p["paid_this_month"] for p in out)}


async def _read(file: UploadFile | None) -> bytes | None:
    if file is None or not file.filename:
        return None
    data = await file.read(storage.MAX_BYTES + 1)
    if len(data) > storage.MAX_BYTES:
        raise HTTPException(413, "The receipt is larger than 5 MB.")
    return data


@router.post("/expenses")
async def add_expense(
    spent_on: date = Form(...),
    amount: str = Form(...),
    category: str = Form(...),
    description: str = Form(..., min_length=3, max_length=1000),
    receipt: UploadFile | None = File(None),
    ctx: OpsContext = Depends(require_ops),
    db: Session = Depends(get_db),
):
    try:
        kobo = naira_to_kobo(amount)
    except ValueError as exc:
        raise HTTPException(422, "Enter the amount in naira, like 12,500 or 12500.50.") from exc
    if kobo <= 0 or kobo > MAX_AMOUNT:
        raise HTTPException(422, "Enter an amount between ₦0.01 and ₦10,000,000.")
    if spent_on > lagos_date(utcnow()):
        raise HTTPException(422, "The date can't be in the future.")
    if category not in CATEGORIES:
        raise HTTPException(422, "Pick a category from the list.")
    data = await _read(receipt)
    key = None
    if data:
        try:
            key = storage.save(data, "expenses")
        except storage.FileRejected as exc:
            raise HTTPException(422, str(exc)) from exc
    e = Expense(ops_user_id=ctx.user.id, spent_on=spent_on, amount=kobo, category=category,
                description=description.strip(), receipt_key=key, status="Submitted")
    db.add(e)
    db.flush()
    audit.record(db, ctx.actor, "expense.submitted", "Expense", e.id, after={"amount": kobo, "category": category})
    _notify_admins(db, e, ctx.user)
    db.commit()
    return _out(e, _users(db), admin=False)


def _get(db: Session, expense_id: str) -> Expense:
    e = db.get(Expense, expense_id)
    if e is None:
        raise HTTPException(404, "Not found.")
    return e


@router.delete("/expenses/{expense_id}")
def delete_expense(expense_id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    e = _get(db, expense_id)
    if e.ops_user_id != ctx.user.id:
        raise HTTPException(404, "Not found.")
    if e.status != "Submitted":
        raise HTTPException(409, f"This claim is already {e.status.lower()}, so it can't be deleted.")
    storage.delete(e.receipt_key)
    audit.record(db, ctx.actor, "expense.deleted", "Expense", e.id, after={"amount": e.amount})
    db.delete(e)
    db.commit()
    return {"ok": True}


@router.post("/expenses/{expense_id}/approve")
def approve_expense(expense_id: str, ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    e = _get(db, expense_id)
    if e.status != "Submitted":
        raise HTTPException(409, f"This claim is already {e.status.lower()}.")
    e.status, e.decided_by, e.decided_at = "Approved", ctx.user.id, utcnow()
    audit.record(db, ctx.actor, "expense.approved", "Expense", e.id, after={"amount": e.amount})
    db.commit()
    return _out(e, _users(db), admin=True)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/expenses/{expense_id}/reject")
def reject_expense(expense_id: str, body: RejectIn, ctx: OpsContext = Depends(require_ops_admin),
                   db: Session = Depends(get_db)):
    e = _get(db, expense_id)
    if e.status not in ("Submitted", "Approved"):
        raise HTTPException(409, f"This claim is already {e.status.lower()}.")
    e.status, e.decided_by, e.decided_at, e.reject_reason = "Rejected", ctx.user.id, utcnow(), body.reason.strip()
    audit.record(db, ctx.actor, "expense.rejected", "Expense", e.id, after={"reason": e.reject_reason})
    owner = db.get(OpsUser, e.ops_user_id)
    if owner and owner.id != ctx.user.id:
        emailer.send(db, owner.email, f"Expense claim not approved: {format_naira(e.amount)}",
                     f"Your {e.category.lower()} claim of {format_naira(e.amount)} from {e.spent_on:%d %b %Y} wasn't approved.\n\n"
                     f"Reason: {e.reject_reason}", kind="expense_rejected")
    db.commit()
    return _out(e, _users(db), admin=True)


class PaidIn(BaseModel):
    reference: str | None = Field(default=None, max_length=120)
    paid_on: date | None = None


@router.post("/expenses/{expense_id}/paid")
def mark_expense_paid(expense_id: str, body: PaidIn, ctx: OpsContext = Depends(require_ops_admin),
                      db: Session = Depends(get_db)):
    e = _get(db, expense_id)
    if e.status not in ("Submitted", "Approved"):
        raise HTTPException(409, f"This claim is already {e.status.lower()}.")
    today = lagos_date(utcnow())
    paid_on = body.paid_on or today
    if paid_on > today:
        raise HTTPException(422, "The payment date can't be in the future.")
    if e.status == "Submitted":
        e.decided_by, e.decided_at = ctx.user.id, utcnow()
    e.status, e.paid_by, e.paid_on = "Paid", ctx.user.id, paid_on
    e.paid_reference = (body.reference or "").strip() or None
    audit.record(db, ctx.actor, "expense.paid", "Expense", e.id, after={"amount": e.amount, "reference": e.paid_reference})
    owner = db.get(OpsUser, e.ops_user_id)
    if owner and owner.id != ctx.user.id:
        emailer.send(db, owner.email, f"Expense paid: {format_naira(e.amount)}",
                     f"Your {e.category.lower()} claim of {format_naira(e.amount)} from {e.spent_on:%d %b %Y} was paid on "
                     f"{paid_on:%d %b %Y}" + (f" (reference {e.paid_reference})" if e.paid_reference else "") + ".",
                     kind="expense_paid")
    db.commit()
    return _out(e, _users(db), admin=True)


# ---------------------------------------------------------------- payout details


class PayoutIn(BaseModel):
    bank_name: str = Field(min_length=2, max_length=80)
    account_number: str = Field(pattern=r"^\d{10}$")
    account_name: str = Field(min_length=2, max_length=120)


@router.get("/me/payout")
def get_payout(ctx: OpsContext = Depends(require_ops)):
    return _payout(ctx.user) or {}


@router.put("/me/payout")
def set_payout(body: PayoutIn, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    ctx.user.payout_details = json.dumps({"bank_name": body.bank_name.strip(), "account_number": body.account_number,
                                          "account_name": body.account_name.strip()})
    audit.record(db, ctx.actor, "ops_user.payout_details_changed", "OpsUser", ctx.user.id)
    db.commit()
    return _payout(ctx.user)
