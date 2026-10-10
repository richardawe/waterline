"""Debtor payment page API. Access is only by the unguessable token in the
link each message carries; anything wrong with the token is a plain 404,
indistinguishable from a token that never existed."""

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collections import serialize, service, storage
from app.collections.audit import Actor
from app.collections.auth import client_ip, rate_limit
from app.collections.models import (
    AccountStatus,
    Client,
    DebtorAccount,
    DebtorPayment,
    UploadBatch,
)
from app.collections.money import naira_to_kobo
from app.collections.rules import active_discount, effective_rules, plan_options
from app.collections.timeutil import lagos_date, utcnow
from app.db import get_db

router = APIRouter(
    prefix="/collections/d", tags=["collections-debtor"], dependencies=[Depends(rate_limit("debtor", 60, 300))]
)

NOT_FOUND = "Not found."


def _account(db: Session, token: str) -> DebtorAccount:
    if not token or len(token) < 32 or len(token) > 64:
        raise HTTPException(404, NOT_FOUND)
    account = db.execute(select(DebtorAccount).where(DebtorAccount.access_token == token)).scalar_one_or_none()
    if not service.token_valid(account, utcnow()):
        raise HTTPException(404, NOT_FOUND)
    return account


def _actor(request: Request, account: DebtorAccount) -> Actor:
    return Actor(type="debtor", id=account.id, ip=client_ip(request), client_id=account.client_id)


def _view(db: Session, account: DebtorAccount) -> dict:
    """Only this debtor's own account and the client's payment details."""
    client: Client = account.client
    today = lagos_date(utcnow())
    rules = effective_rules(account, db.get(UploadBatch, account.batch_id), client)
    plan = service.active_plan(db, account)
    pending = db.execute(
        select(DebtorPayment).where(DebtorPayment.account_id == account.id, DebtorPayment.status == "Proof submitted")
    ).scalars().all()
    dispute = service.open_dispute(db, account)
    open_status = account.status not in AccountStatus.CLOSED
    return {
        "client": {"name": client.business_name, "logo_url": client.logo_url, "bank_name": client.bank_name,
                   "account_number": client.account_number, "account_name": client.account_name},
        "debtor_first_name": account.debtor_name.split()[0],
        "description": account.description,
        "dependant_first_name": account.dependant_first_name,
        "reference_code": account.reference_code,
        "original_amount": account.original_amount,
        "balance": account.balance,
        "status": account.status,
        "is_closed": not open_status,
        "opted_out": account.opted_out_at is not None,
        "discount": active_discount(rules, account.balance, today) if open_status and not plan else None,
        "plan": serialize.plan(plan) if plan else None,
        "plan_options": plan_options(rules, account.balance, today) if open_status and not plan and not dispute else [],
        "pending_proofs": [{"amount": p.amount_claimed, "paid_on": p.paid_on, "submitted_at": p.created_at} for p in pending],
        "open_dispute": {"type": dispute.type, "created_at": dispute.created_at} if dispute else None,
    }


@router.get("/{token}")
def view(token: str, db: Session = Depends(get_db)):
    return _view(db, _account(db, token))


def _act(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.ActionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except storage.FileRejected as exc:
        raise HTTPException(422, str(exc)) from exc


class PlanIn(BaseModel):
    instalments: int


@router.post("/{token}/plan")
def choose_plan(token: str, body: PlanIn, request: Request, db: Session = Depends(get_db)):
    account = _account(db, token)
    _act(service.choose_plan, db, account, body.instalments, _actor(request, account), lagos_date(utcnow()))
    db.commit()
    return _view(db, account)


async def _read_file(file: UploadFile | None) -> bytes | None:
    if file is None or not file.filename:
        return None
    data = await file.read(storage.MAX_BYTES + 1)
    if len(data) > storage.MAX_BYTES:
        raise HTTPException(413, "The file is larger than 5 MB.")
    return data


@router.post("/{token}/proof", dependencies=[Depends(rate_limit("debtor-upload", 10, 3600))])
async def submit_proof(
    token: str,
    request: Request,
    amount: str = Form(...),
    paid_on: date = Form(...),
    sender_name: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    account = _account(db, token)
    data = await _read_file(file)
    if not data:
        raise HTTPException(422, "Attach a photo or PDF of your receipt.")
    try:
        kobo = naira_to_kobo(amount)
    except ValueError as exc:
        raise HTTPException(422, "Enter the amount you paid.") from exc
    _act(service.submit_proof, db, account, file_bytes=data, amount=kobo, paid_on=paid_on, sender_name=sender_name,
         actor=_actor(request, account), today=lagos_date(utcnow()))
    db.commit()
    return _view(db, account)


@router.post("/{token}/dispute", dependencies=[Depends(rate_limit("debtor-upload", 10, 3600))])
async def dispute(
    token: str,
    request: Request,
    type: str = Form(...),
    details: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    account = _account(db, token)
    data = await _read_file(file)
    _act(service.raise_dispute, db, account, type=type, details=details, evidence_bytes=data, raised_by="Debtor",
         actor=_actor(request, account))
    db.commit()
    return _view(db, account)


@router.post("/{token}/opt-out")
def opt_out(token: str, request: Request, db: Session = Depends(get_db)):
    account = _account(db, token)
    service.opt_out(db, account, _actor(request, account), via="debtor page")
    db.commit()
    return _view(db, account)
