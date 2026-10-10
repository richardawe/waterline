"""The daily outreach queue and its conduct rules.

Ops send every message by hand from a Waterline phone; this module decides
who may be contacted today and with what text, and refuses to record a
send that would break a rule. Ops cannot override any of these from the
queue screen:

- 08:00–19:00 WAT, Monday–Saturday, never on a public holiday
- at most 1 message per debtor per day and 2 per rolling 7 days, all channels
- sequence: Day 0 intro, Day 3 reminder, Day 7 plan offer, then weekly;
  6 messages with no response → Unresponsive
- plan holders only get instalment reminders (2 days before, 1 day after a miss)
- never: Disputed / Opted out / Paid / Withdrawn / Paused accounts, or any
  account whose client isn't Approved
"""

import json
from datetime import date, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collections import audit, emailer, service, settings_store, templates
from app.collections.audit import Actor
from app.collections.models import (
    AccountStatus,
    Client,
    ClientStatus,
    DebtorAccount,
    OutreachItem,
    PaymentPlan,
    UploadBatch,
)
from app.collections.money import format_naira
from app.collections.rules import active_discount, effective_rules
from app.collections.timeutil import (
    CONTACT_START,
    in_contact_window,
    is_contact_day,
    lagos_date,
    lagos_datetime,
    utcnow,
)

SEQUENCE_KEYS = ("INTRO", "REMINDER", "PLAN_OFFER")
MAX_UNANSWERED = 6
MAX_PER_7_DAYS = 2
QUEUEABLE = (AccountStatus.NEW, AccountStatus.ACTIVE, AccountStatus.ON_PLAN)
SKIP_REASONS = ["Wrong number", "Not on WhatsApp", "Other"]
REPLY_CATEGORIES = ["Promised to pay", "Already paid", "Disputes amount", "Asks for plan", "Asks to stop", "Other"]


class OutreachRefused(ValueError):
    pass


# ---------------------------------------------------------------- eligibility


def contactable(account: DebtorAccount, client: Client) -> str | None:
    """Return why an account may not be contacted at all, or None."""
    if client.status != ClientStatus.APPROVED:
        return f"client is {client.status}"
    if account.opted_out_at:
        return "opted out"
    if account.anonymised_at or not account.access_token:
        return "no active link"
    if account.status not in QUEUEABLE:
        return f"account is {account.status}"
    if account.balance <= 0:
        return "nothing owed"
    if not account.phone:
        return "no phone number"
    return None


def _sent_times(db: Session, account_id: str) -> list[datetime]:
    return list(
        db.execute(
            select(OutreachItem.sent_at).where(OutreachItem.account_id == account_id, OutreachItem.status == "Sent")
        ).scalars()
    )


def sent_in_last_7_days(sent_times: list[datetime], today: date) -> int:
    """The rolling window is 7 WAT calendar days: today and the 6 before.
    (Counting 168 hours instead would block the spec's own Day 7 plan offer
    whenever it goes out earlier in the day than Day 0's intro did.)"""
    return sum(1 for t in sent_times if lagos_date(t) > today - timedelta(days=7))


def frequency_block(sent_times: list[datetime], now: datetime) -> str | None:
    today = lagos_date(now)
    if any(lagos_date(t) == today for t in sent_times):
        return "already messaged today"
    if sent_in_last_7_days(sent_times, today) >= MAX_PER_7_DAYS:
        return "2 messages in the last 7 days"
    return None


# ---------------------------------------------------------------- what to send


def _sequence_items(db: Session, account_id: str) -> list[OutreachItem]:
    return list(
        db.execute(
            select(OutreachItem)
            .where(
                OutreachItem.account_id == account_id,
                OutreachItem.status == "Sent",
                OutreachItem.template_key.in_(SEQUENCE_KEYS),
            )
            .order_by(OutreachItem.sent_at)
        ).scalars()
    )


def next_sequence_message(
    db: Session, account: DebtorAccount, rules: dict, today: date, now: datetime
) -> tuple[str | None, str | None]:
    """(template_key, None) when a sequence message is due today, or
    (None, reason) when it isn't. May mark the account Unresponsive."""
    sent = _sequence_items(db, account.id)
    since = account.last_response_at
    unanswered = [i for i in _all_sent(db, account.id) if since is None or i.sent_at > since]
    if len(unanswered) >= MAX_UNANSWERED:
        service.set_status(db, account, AccountStatus.UNRESPONSIVE, audit.SYSTEM, f"{MAX_UNANSWERED} messages with no response")
        return None, "unresponsive"
    k = len(sent)
    if k == 0:
        start = rules.get("start_date")
        if start and date.fromisoformat(start) > today:
            return None, "campaign not started"
        return "INTRO", None
    first, last = lagos_date(sent[0].sent_at), lagos_date(sent[-1].sent_at)
    due = {1: first + timedelta(days=3), 2: first + timedelta(days=7)}.get(k, last + timedelta(days=7))
    if today < due:
        return None, f"next message due {due.isoformat()}"
    key = "PLAN_OFFER" if k == 2 and rules["max_instalments"] > 1 else "REMINDER"
    return key, None


def _all_sent(db: Session, account_id: str) -> list[OutreachItem]:
    return list(
        db.execute(
            select(OutreachItem).where(OutreachItem.account_id == account_id, OutreachItem.status == "Sent")
        ).scalars()
    )


def plan_reminder(
    db: Session, account: DebtorAccount, plan: PaymentPlan, today: date
) -> tuple[str | None, dict | None, str | None]:
    """(context_key, instalment, reason). Reminds 2 days before each due date
    (catching up to the due date if that day was Sunday/holiday) and once 1+
    day after a missed one. A plan more than `plan_broken_after_days` behind
    is Broken and the account goes back to the normal sequence."""
    schedule = json.loads(plan.schedule)
    covered = plan.total_amount - account.balance
    cumulative = 0
    for i, inst in enumerate(schedule):
        cumulative += inst["amount"]
        if cumulative <= covered:
            continue  # this instalment is paid
        due = date.fromisoformat(inst["due"])
        broken_after = int(settings_store.get(db, "plan_broken_after_days"))
        if today > due + timedelta(days=broken_after):
            plan.status = "Broken"
            audit.record(db, audit.SYSTEM, "plan.broken", "PaymentPlan", plan.id, client_id=account.client_id,
                         after={"instalment": i + 1, "due": inst["due"]})
            service.set_status(db, account, AccountStatus.ACTIVE, audit.SYSTEM, "payment plan broken")
            return None, None, "plan broken"
        sent_keys = {i.context_key for i in _all_sent(db, account.id)}
        if due - timedelta(days=2) <= today <= due and f"inst:{i}:before" not in sent_keys:
            return f"inst:{i}:before", {**inst, "remaining": cumulative - covered}, None
        if today >= due + timedelta(days=1) and f"inst:{i}:missed" not in sent_keys:
            return f"inst:{i}:missed", {**inst, "remaining": cumulative - covered}, None
        return None, None, "no instalment reminder due"
    return None, None, "plan fully covered"


def render_for(db: Session, account: DebtorAccount, client: Client, key: str, rules: dict, today: date,
               instalment: dict | None = None) -> tuple[str, int]:
    tpl = templates.active(db, key)
    discount = active_discount(rules, account.balance, today)
    discount_line = ""
    if discount:
        discount_line = (
            f"Pay {format_naira(discount['pay_amount'])} in full by "
            f"{date.fromisoformat(discount['deadline']):%d/%m/%Y} and save {format_naira(discount['discount_amount'])}."
        )
    values = {
        "debtor_first_name": account.debtor_name.split()[0] if account.debtor_name else "there",
        "client_name": client.business_name,
        "description": account.description,
        "balance": format_naira(account.balance),
        "link": debtor_link(account),
        "discount_line": discount_line,
        "instalment_amount": format_naira(instalment["remaining"]) if instalment else "",
        "due_date": date.fromisoformat(instalment["due"]).strftime("%d/%m/%Y") if instalment else "",
    }
    return templates.render(tpl.body, values), tpl.version


def debtor_link(account: DebtorAccount) -> str:
    return emailer.site_url(f"/collections/p/{account.access_token}")


# ---------------------------------------------------------------- building the queue


def activate_new_accounts(db: Session, today: date) -> int:
    """New accounts become Active once their client is Approved and the
    campaign start date has arrived."""
    rows = db.execute(
        select(DebtorAccount, Client, UploadBatch)
        .join(Client, Client.id == DebtorAccount.client_id)
        .join(UploadBatch, UploadBatch.id == DebtorAccount.batch_id)
        .where(DebtorAccount.status == AccountStatus.NEW, Client.status == ClientStatus.APPROVED)
    ).all()
    count = 0
    for account, client, batch in rows:
        start = effective_rules(account, batch, client).get("start_date")
        if start and date.fromisoformat(start) > today:
            continue
        service.set_status(db, account, AccountStatus.ACTIVE, audit.SYSTEM, "campaign started")
        count += 1
    return count


def expire_stale(db: Session, today: date) -> int:
    stale = db.execute(
        select(OutreachItem).where(OutreachItem.status == "Queued", OutreachItem.queue_date < today)
    ).scalars().all()
    for item in stale:
        item.status, item.skip_reason = "Skipped", "Expired (not sent that day)"
    return len(stale)


def withdraw_ineligible(db: Session, today: date) -> int:
    """Pull today's still-queued items whose account or client stopped being
    contactable since the queue was built (suspension, opt-out, dispute)."""
    rows = db.execute(
        select(OutreachItem, DebtorAccount, Client)
        .join(DebtorAccount, DebtorAccount.id == OutreachItem.account_id)
        .join(Client, Client.id == OutreachItem.client_id)
        .where(OutreachItem.status == "Queued", OutreachItem.queue_date == today)
    ).all()
    n = 0
    for item, account, client in rows:
        reason = contactable(account, client)
        if reason:
            item.status, item.skip_reason = "Skipped", f"Not contactable: {reason}"
            n += 1
    return n


def build_queue(db: Session, now: datetime | None = None) -> dict:
    """Idempotent: safe to call repeatedly in a day; it only adds items for
    accounts that don't have one today."""
    now = now or utcnow()
    today = lagos_date(now)
    holidays = settings_store.holidays(db)
    expired = expire_stale(db, today)
    if not is_contact_day(today, holidays):
        db.flush()
        return {"date": today.isoformat(), "created": 0, "expired": expired, "reason": "no outreach on Sundays or public holidays"}
    templates.ensure_defaults(db)
    activated = activate_new_accounts(db, today)
    withdrawn = withdraw_ineligible(db, today)
    db.flush()

    already = set(
        db.execute(select(OutreachItem.account_id).where(OutreachItem.queue_date == today)).scalars()
    )
    rows = db.execute(
        select(DebtorAccount, Client, UploadBatch)
        .join(Client, Client.id == DebtorAccount.client_id)
        .join(UploadBatch, UploadBatch.id == DebtorAccount.batch_id)
        .where(
            Client.status == ClientStatus.APPROVED,
            DebtorAccount.status.in_((AccountStatus.ACTIVE, AccountStatus.ON_PLAN)),
            DebtorAccount.opted_out_at.is_(None),
            DebtorAccount.balance > 0,
        )
        .order_by(DebtorAccount.created_at)
    ).all()
    scheduled_for = max(now, lagos_datetime(today, CONTACT_START))
    created, skipped = 0, {}
    for account, client, batch in rows:
        if account.id in already:
            continue
        why = contactable(account, client) or (service.open_dispute(db, account) and "open dispute") or None
        why = why or frequency_block(_sent_times(db, account.id), now)
        key = context_key = instalment = None
        rules = effective_rules(account, batch, client)
        if not why:
            if account.pending_notice:
                key = account.pending_notice
            elif account.status == AccountStatus.ON_PLAN:
                plan = service.active_plan(db, account)
                if plan is None:
                    service.set_status(db, account, AccountStatus.ACTIVE, audit.SYSTEM, "no active plan")
                    key, why = next_sequence_message(db, account, rules, today, now)
                else:
                    context_key, instalment, why = plan_reminder(db, account, plan, today)
                    if context_key:
                        key = "INSTALMENT_DUE"
                    elif plan.status == "Broken":
                        key, why = next_sequence_message(db, account, rules, today, now)
            else:
                key, why = next_sequence_message(db, account, rules, today, now)
        if not key:
            skipped[why or "not due"] = skipped.get(why or "not due", 0) + 1
            continue
        text, version = render_for(db, account, client, key, rules, today, instalment)
        db.add(
            OutreachItem(
                account_id=account.id,
                client_id=client.id,
                template_key=key,
                template_version=version,
                context_key=context_key,
                rendered_text=text,
                queue_date=today,
                scheduled_for=scheduled_for,
                status="Queued",
            )
        )
        created += 1
    db.flush()
    audit.record(db, audit.SYSTEM, "queue.built", "OutreachQueue", today.isoformat(),
                 after={"created": created, "activated": activated, "expired": expired, "withdrawn": withdrawn})
    return {"date": today.isoformat(), "created": created, "activated": activated, "expired": expired,
            "withdrawn": withdrawn, "not_queued": skipped}


# ---------------------------------------------------------------- ops actions


def _check_sendable(db: Session, item: OutreachItem, now: datetime) -> tuple[DebtorAccount, Client]:
    if item.status != "Queued":
        raise OutreachRefused(f"This item is already {item.status.lower()}.")
    if item.queue_date != lagos_date(now):
        raise OutreachRefused("This item is from an earlier day's queue.")
    if not in_contact_window(now, settings_store.holidays(db)):
        raise OutreachRefused("Messages can only go out 8am–7pm WAT, Monday to Saturday (not public holidays).")
    account = db.get(DebtorAccount, item.account_id)
    client = db.get(Client, item.client_id)
    reason = contactable(account, client) or (service.open_dispute(db, account) and "open dispute")
    if reason:
        item.status, item.skip_reason = "Skipped", f"Not contactable: {reason}"
        raise OutreachRefused(f"This person can't be contacted now ({reason}). The item was removed.")
    block = frequency_block(_sent_times(db, account.id), now)
    if block:
        raise OutreachRefused(f"Frequency limit: {block}.")
    return account, client


def _record_sent(db: Session, item: OutreachItem, account: DebtorAccount, channel: str, actor: Actor, now: datetime) -> None:
    item.status, item.channel, item.sent_by, item.sent_at = "Sent", channel, actor.id, now
    account.last_contacted_at = now
    account.contact_count_7d = sent_in_last_7_days(_sent_times(db, account.id), lagos_date(now))
    if account.pending_notice and account.pending_notice == item.template_key:
        account.pending_notice = None
    audit.record(db, actor, "outreach.sent", "OutreachItem", item.id, client_id=item.client_id,
                 after={"channel": channel, "template": item.template_key, "account": account.reference_code})


def mark_sent(db: Session, item: OutreachItem, channel: str, actor: Actor, now: datetime | None = None) -> OutreachItem:
    now = now or utcnow()
    if channel not in ("WhatsApp", "SMS"):
        raise OutreachRefused("Channel must be WhatsApp or SMS.")
    account, _ = _check_sendable(db, item, now)
    db.flush()
    _record_sent(db, item, account, channel, actor, now)
    return item


def skip(db: Session, item: OutreachItem, reason: str, note: str | None, actor: Actor, now: datetime | None = None) -> OutreachItem:
    """Skip the phone send. Where the debtor has an email address, the same
    message goes out by email instead (it's still the day's one message)."""
    now = now or utcnow()
    if reason not in SKIP_REASONS:
        raise OutreachRefused(f"Reason must be one of: {', '.join(SKIP_REASONS)}.")
    if item.status != "Queued":
        raise OutreachRefused(f"This item is already {item.status.lower()}.")
    full_reason = reason if not note else f"{reason}: {note.strip()[:80]}"
    account = db.get(DebtorAccount, item.account_id)
    client = db.get(Client, item.client_id)
    if account.email and settings_store.get(db, "email_fallback_enabled"):
        try:
            _check_sendable(db, item, now)
        except OutreachRefused:
            pass
        else:
            emailer.send(db, account.email, f"A message from {client.business_name} via Waterline", item.rendered_text,
                         kind="debtor_outreach", client_id=client.id)
            item.skip_reason = f"Phone skipped ({full_reason}); sent by email"
            _record_sent(db, item, account, "Email", actor, now)
            return item
    if item.status == "Queued":
        item.status, item.skip_reason = "Skipped", full_reason
        audit.record(db, actor, "outreach.skipped", "OutreachItem", item.id, client_id=item.client_id, after={"reason": full_reason})
    return item


def log_reply(db: Session, item: OutreachItem, category: str, note: str | None, actor: Actor) -> OutreachItem:
    if category not in REPLY_CATEGORIES:
        raise OutreachRefused(f"Category must be one of: {', '.join(REPLY_CATEGORIES)}.")
    if item.status != "Sent":
        raise OutreachRefused("Replies can only be logged against a sent message.")
    account = db.get(DebtorAccount, item.account_id)
    item.reply_category, item.reply_note, item.replied_at = category, (note or "").strip()[:1000] or None, utcnow()
    service.mark_response(account)
    audit.record(db, actor, "outreach.reply_logged", "OutreachItem", item.id, client_id=item.client_id,
                 after={"category": category})
    if category == "Asks to stop":
        service.opt_out(db, account, actor, via="reply STOP")
    elif category in ("Disputes amount", "Already paid") and account.status not in AccountStatus.CLOSED:
        service.raise_dispute(
            db, account,
            type="Wrong amount" if category == "Disputes amount" else "Already paid",
            details=f"Logged by Waterline from a reply: {item.reply_note or category}",
            evidence_bytes=None, raised_by="Ops", actor=actor,
        )
    return item


def links(phone: str, text: str) -> dict:
    digits = phone.lstrip("+")
    return {
        "whatsapp": f"https://wa.me/{digits}?text={quote(text)}",
        "sms": f"sms:{phone}?&body={quote(text)}",
    }


def queue_count(db: Session, today: date) -> int:
    return db.execute(
        select(func.count()).select_from(OutreachItem).where(OutreachItem.queue_date == today, OutreachItem.status == "Queued")
    ).scalar_one()
