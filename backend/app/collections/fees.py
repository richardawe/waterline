"""Success-fee schedules and client fee proposals.

Every client has a fee schedule (flat, or tiered by how long the debt has
been overdue). New clients start on their segment's default. A client may
propose a different schedule (at sign-up or later from Settings); a Waterline
Admin approves or rejects it. Approval applies to payments confirmed from
that moment on — fees already calculated never change.
"""

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collections import audit, emailer, segments, service
from app.collections.audit import Actor
from app.collections.models import Client, DebtorAccount, FeeProposal
from app.collections.money import percent_of
from app.collections.timeutil import lagos_date, utcnow


class ProposalError(ValueError):
    pass


def schedule(client: Client) -> dict:
    return json.loads(client.fee_schedule)


def debt_age_days(account: DebtorAccount, on: date) -> int:
    """How overdue the debt was on `on`: from its due date if known; else
    the days-past-due given at upload plus time since; else time since upload."""
    if account.due_date:
        return max(0, (on - account.due_date).days)
    uploaded = lagos_date(account.created_at) if account.created_at else on
    extra = json.loads(account.extra) if account.extra else {}
    try:
        dpd = int(extra.get("days_past_due") or 0)
    except (TypeError, ValueError):
        dpd = 0
    return max(0, dpd + (on - uploaded).days)


def fee_for(client: Client, account: DebtorAccount, amount: int, paid_on: date) -> tuple[float, int]:
    pct = segments.fee_percent_for(schedule(client), debt_age_days(account, paid_on))
    return pct, percent_of(amount, pct)


def pending(db: Session, client_id: str) -> FeeProposal | None:
    return db.execute(
        select(FeeProposal).where(FeeProposal.client_id == client_id, FeeProposal.status == "Pending")
    ).scalars().first()


def propose(db: Session, client: Client, raw_schedule: dict, note: str | None, actor: Actor) -> FeeProposal:
    try:
        clean = segments.validate_fee_schedule(raw_schedule)
    except segments.FeeInvalid as exc:
        raise ProposalError(str(exc)) from exc
    if clean == schedule(client):
        raise ProposalError("That's already your fee.")
    previous = pending(db, client.id)
    if previous:  # one open proposal at a time; the newest replaces it
        previous.status, previous.decided_at, previous.decision_note = "Withdrawn", utcnow(), "Replaced by a newer proposal"
    proposal = FeeProposal(client_id=client.id, schedule=json.dumps(clean), note=(note or "").strip()[:1000] or None,
                           status="Pending", proposed_by=actor.id)
    db.add(proposal)
    db.flush()
    audit.record(db, actor, "fee.proposed", "FeeProposal", proposal.id, client_id=client.id,
                 before=schedule(client), after={"schedule": clean, "note": proposal.note})
    service.notify_ops(db, f"Fee proposal: {client.business_name}",
                       f"{client.business_name} proposed: {segments.describe_fee(clean)} "
                       f"(current: {segments.describe_fee(schedule(client))}).\nNote: {proposal.note or '-'}",
                       "ops_fee_proposal", client.id)
    return proposal


def withdraw(db: Session, proposal: FeeProposal, actor: Actor) -> None:
    if proposal.status != "Pending":
        raise ProposalError("Only a pending proposal can be withdrawn.")
    proposal.status, proposal.decided_at = "Withdrawn", utcnow()
    audit.record(db, actor, "fee.proposal_withdrawn", "FeeProposal", proposal.id, client_id=proposal.client_id)


def decide(db: Session, proposal: FeeProposal, approve: bool, note: str | None, actor: Actor) -> FeeProposal:
    if proposal.status != "Pending":
        raise ProposalError("This proposal has already been decided.")
    client = db.get(Client, proposal.client_id)
    new = json.loads(proposal.schedule)
    proposal.status = "Approved" if approve else "Rejected"
    proposal.decided_by, proposal.decided_at = actor.id, utcnow()
    proposal.decision_note = (note or "").strip()[:1000] or None
    before = schedule(client)
    if approve:
        client.fee_schedule = json.dumps(new)
    audit.record(db, actor, "fee.approved" if approve else "fee.rejected", "FeeProposal", proposal.id,
                 client_id=client.id, before=before, after={"schedule": new, "note": proposal.decision_note})
    if approve:
        body = (f"We've approved your proposed fee: {segments.describe_fee(new)}. It applies to payments you confirm "
                "from today.")
    else:
        body = (f"We weren't able to accept your proposed fee ({segments.describe_fee(new)}). Your fee stays at "
                f"{segments.describe_fee(before)}.")
    if proposal.decision_note:
        body += f"\n\nNote from Waterline: {proposal.decision_note}"
    body += f"\n\n{emailer.site_url('/collections/app/settings/')}"
    service.notify_client(db, client.id, "Your fee proposal", body, "client_fee_decision")
    return proposal


def proposal_view(p: FeeProposal) -> dict:
    sched = json.loads(p.schedule)
    return {"id": p.id, "client_id": p.client_id, "schedule": sched, "text": segments.describe_fee(sched),
            "note": p.note, "status": p.status, "created_at": p.created_at, "decided_at": p.decided_at,
            "decision_note": p.decision_note}
