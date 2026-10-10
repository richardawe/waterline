"""Outreach queue rules — one test per rule, driven with explicit clocks."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.collections import audit, outreach, service, settings_store
from app.collections.models import AccountStatus, ClientStatus, OutreachItem
from tests.collections_factory import (
    a_weekday,
    add_sent,
    make_account,
    make_client,
    wat,
)
from tests.conftest import requires_db

pytestmark = requires_db

MON = a_weekday()


def queued(db, account, day=None):
    stmt = select(OutreachItem).where(OutreachItem.account_id == account.id, OutreachItem.status == "Queued")
    if day:
        stmt = stmt.where(OutreachItem.queue_date == day)
    return db.execute(stmt).scalars().all()


def test_intro_is_queued_for_an_approved_clients_new_account(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client, status=AccountStatus.NEW)
    outreach.build_queue(cl_db, wat(MON, 7))
    [item] = queued(cl_db, account)
    assert item.template_key == "INTRO"
    assert account.status == AccountStatus.ACTIVE
    assert "Greenfield School via Waterline" in item.rendered_text
    assert f"/collections/p/{account.access_token}" in item.rendered_text
    assert "Reply STOP to stop messages." in item.rendered_text
    assert item.scheduled_for == wat(MON, 8)  # never before 8am


@pytest.mark.parametrize("status", [ClientStatus.PENDING, ClientStatus.SUSPENDED, ClientStatus.REJECTED])
def test_nothing_queued_unless_client_approved(cl_db, status):
    client, _ = make_client(cl_db, status=status)
    account = make_account(cl_db, client, status=AccountStatus.NEW)
    outreach.build_queue(cl_db, wat(MON))
    assert queued(cl_db, account) == []


def test_no_queue_on_sunday_or_public_holiday(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    sunday = MON - timedelta(days=1)
    assert outreach.build_queue(cl_db, wat(sunday))["created"] == 0
    settings_store.set_value(cl_db, "public_holidays", [MON.isoformat()])
    assert outreach.build_queue(cl_db, wat(MON))["created"] == 0
    assert queued(cl_db, account) == []


def test_mark_sent_refused_outside_contact_hours(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    outreach.build_queue(cl_db, wat(MON, 6))
    [item] = queued(cl_db, account)
    with pytest.raises(outreach.OutreachRefused, match="8am–7pm"):
        outreach.mark_sent(cl_db, item, "WhatsApp", audit.SYSTEM, now=wat(MON, 7, 30))
    with pytest.raises(outreach.OutreachRefused, match="8am–7pm"):
        outreach.mark_sent(cl_db, item, "WhatsApp", audit.SYSTEM, now=wat(MON, 19, 5))
    outreach.mark_sent(cl_db, item, "WhatsApp", audit.SYSTEM, now=wat(MON, 9))
    assert item.status == "Sent" and account.last_contacted_at == wat(MON, 9)


def test_one_message_per_day_across_channels(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    add_sent(cl_db, account, wat(MON, 9), channel="Email")
    outreach.build_queue(cl_db, wat(MON, 10))
    assert queued(cl_db, account) == []
    # a second build the same day never duplicates
    other = make_account(cl_db, client, phone="+2348039999999")
    outreach.build_queue(cl_db, wat(MON, 11))
    outreach.build_queue(cl_db, wat(MON, 12))
    assert len(queued(cl_db, other)) == 1


def test_two_per_rolling_seven_days(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    add_sent(cl_db, account, wat(MON - timedelta(days=6), 9), key="INTRO")
    add_sent(cl_db, account, wat(MON - timedelta(days=3), 9), key="REMINDER")
    outreach.build_queue(cl_db, wat(MON, 9))  # day 6 since intro, PLAN_OFFER would be due day 7 anyway
    assert queued(cl_db, account) == []
    tue = MON + timedelta(days=1)
    outreach.build_queue(cl_db, wat(tue, 9, 30))  # intro now >7 days ago (rolling), plan offer due
    [item] = queued(cl_db, account, tue)
    assert item.template_key == "PLAN_OFFER"


def test_database_allows_only_one_outreach_item_per_account_per_day(cl_db):
    from sqlalchemy.exc import IntegrityError

    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    outreach.build_queue(cl_db, wat(MON, 8))
    with pytest.raises(IntegrityError):
        with cl_db.begin_nested():
            add_sent(cl_db, account, wat(MON, 8, 30), channel="Email")


def test_frequency_rechecked_at_send_time(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    add_sent(cl_db, account, wat(MON - timedelta(days=4), 9), key="INTRO")
    outreach.build_queue(cl_db, wat(MON, 8))
    [item] = queued(cl_db, account)
    # an earlier-day message recorded after the queue was built (e.g. logged late)
    add_sent(cl_db, account, wat(MON - timedelta(days=1), 18), key="REMINDER")
    with pytest.raises(outreach.OutreachRefused, match="Frequency"):
        outreach.mark_sent(cl_db, item, "WhatsApp", audit.SYSTEM, now=wat(MON, 9))


def test_sequence_day0_day3_day7_then_weekly_then_unresponsive(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    day0 = MON
    expected = [(0, "INTRO"), (3, "REMINDER"), (7, "PLAN_OFFER"), (14, "REMINDER"), (21, "REMINDER"), (28, "REMINDER")]
    for offset, key in expected:
        day = day0 + timedelta(days=offset)
        # nothing the day before it's due
        if offset:
            outreach.build_queue(cl_db, wat(day - timedelta(days=1), 9))
            assert queued(cl_db, account, day - timedelta(days=1)) == [], offset
        outreach.build_queue(cl_db, wat(day, 9))
        [item] = queued(cl_db, account, day)
        assert item.template_key == key, offset
        outreach.mark_sent(cl_db, item, "WhatsApp", audit.SYSTEM, now=wat(day, 9, 30))
    outreach.build_queue(cl_db, wat(day0 + timedelta(days=35), 9))
    assert account.status == AccountStatus.UNRESPONSIVE
    assert queued(cl_db, account) == []


def test_response_resets_unanswered_count(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    for i in range(6):
        add_sent(cl_db, account, wat(MON - timedelta(days=60 - 7 * i), 9), key="REMINDER" if i else "INTRO")
    account.last_response_at = wat(MON - timedelta(days=20), 12)
    outreach.build_queue(cl_db, wat(MON, 9))
    assert account.status == AccountStatus.ACTIVE
    assert len(queued(cl_db, account)) == 1


def test_opted_out_and_disputed_and_paused_accounts_never_queued(cl_db):
    client, _ = make_client(cl_db)
    opted = make_account(cl_db, client, phone="+2348031111111")
    disputed = make_account(cl_db, client, phone="+2348032222222")
    paused = make_account(cl_db, client, phone="+2348033333333")
    service.opt_out(cl_db, opted, audit.SYSTEM, via="test")
    service.raise_dispute(cl_db, disputed, type="Wrong amount", details=None, evidence_bytes=None, raised_by="Debtor", actor=audit.SYSTEM)
    service.pause_account(cl_db, paused, audit.SYSTEM)
    outreach.build_queue(cl_db, wat(MON, 9))
    for account in (opted, disputed, paused):
        assert queued(cl_db, account) == []


def test_dispute_and_opt_out_pull_todays_queued_item_immediately(cl_db):
    client, _ = make_client(cl_db)
    a = make_account(cl_db, client, phone="+2348031111111")
    b = make_account(cl_db, client, phone="+2348032222222")
    outreach.build_queue(cl_db, wat(MON, 8))
    [item_a], [item_b] = queued(cl_db, a), queued(cl_db, b)
    service.raise_dispute(cl_db, a, type="Already paid", details=None, evidence_bytes=None, raised_by="Debtor", actor=audit.SYSTEM)
    service.opt_out(cl_db, b, audit.SYSTEM, via="debtor page")
    assert item_a.status == "Skipped" and item_b.status == "Skipped"
    with pytest.raises(outreach.OutreachRefused):
        outreach.mark_sent(cl_db, item_a, "WhatsApp", audit.SYSTEM, now=wat(MON, 9))


def test_suspension_withdraws_queued_items_at_next_build(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    outreach.build_queue(cl_db, wat(MON, 8))
    [item] = queued(cl_db, account)
    client.status = ClientStatus.SUSPENDED
    outreach.build_queue(cl_db, wat(MON, 10))
    assert item.status == "Skipped" and "suspended" in item.skip_reason.lower()


def test_campaign_start_date_respected(cl_db):
    from tests.collections_factory import make_batch

    client, _ = make_client(cl_db)
    batch = make_batch(cl_db, client, rules={"start_date": (MON + timedelta(days=2)).isoformat()})
    account = make_account(cl_db, client, batch, status=AccountStatus.NEW)
    outreach.build_queue(cl_db, wat(MON, 9))
    assert account.status == AccountStatus.NEW and queued(cl_db, account) == []
    outreach.build_queue(cl_db, wat(MON + timedelta(days=2), 9))
    assert len(queued(cl_db, account)) == 1


def test_plan_holders_only_get_instalment_reminders(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client, balance=90_000_00)
    add_sent(cl_db, account, wat(MON - timedelta(days=10), 9))
    plan = service.choose_plan(cl_db, account, 3, audit.SYSTEM, MON)  # first instalment due MON+7
    assert account.status == AccountStatus.ON_PLAN
    for offset in range(0, 5):  # no sequence messages while on plan
        outreach.build_queue(cl_db, wat(MON + timedelta(days=offset), 9))
        assert queued(cl_db, account, MON + timedelta(days=offset)) == [], offset
    due = MON + timedelta(days=7)
    outreach.build_queue(cl_db, wat(due - timedelta(days=2), 9))
    [before] = queued(cl_db, account, due - timedelta(days=2))
    assert before.template_key == "INSTALMENT_DUE" and before.context_key == "inst:0:before"
    assert "₦30,000.00" in before.rendered_text and due.strftime("%d/%m/%Y") in before.rendered_text
    outreach.mark_sent(cl_db, before, "WhatsApp", audit.SYSTEM, now=wat(due - timedelta(days=2), 10))
    outreach.build_queue(cl_db, wat(due, 9))
    assert queued(cl_db, account, due) == []  # already reminded for this instalment
    outreach.build_queue(cl_db, wat(due + timedelta(days=1), 9))
    [missed] = queued(cl_db, account, due + timedelta(days=1))
    assert missed.context_key == "inst:0:missed"
    assert plan.status == "Active"


def test_dispute_resolution_notifies_debtor_on_next_queue(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    dispute = service.raise_dispute(cl_db, account, type="Wrong amount", details="x", evidence_bytes=None, raised_by="Debtor", actor=audit.SYSTEM)
    service.resolve_dispute(cl_db, dispute, outcome="Adjust", new_balance=100_000_00, note="corrected", actor=audit.SYSTEM)
    assert account.status == AccountStatus.ACTIVE and account.balance == 100_000_00
    outreach.build_queue(cl_db, wat(MON, 9))
    [item] = queued(cl_db, account)
    assert item.template_key == "DISPUTE_RESOLVED"
    outreach.mark_sent(cl_db, item, "SMS", audit.SYSTEM, now=wat(MON, 9, 30))
    assert account.pending_notice is None


def test_skip_falls_back_to_email_when_address_exists(cl_db):
    from app.collections.models import EmailMessage

    client, _ = make_client(cl_db)
    with_email = make_account(cl_db, client, email="ada@example.com", phone="+2348031111111")
    no_email = make_account(cl_db, client, phone="+2348032222222")
    outreach.build_queue(cl_db, wat(MON, 8))
    [a], [b] = queued(cl_db, with_email), queued(cl_db, no_email)
    outreach.skip(cl_db, a, "Not on WhatsApp", None, audit.SYSTEM, now=wat(MON, 9))
    outreach.skip(cl_db, b, "Wrong number", None, audit.SYSTEM, now=wat(MON, 9))
    assert a.status == "Sent" and a.channel == "Email"
    assert b.status == "Skipped" and b.skip_reason == "Wrong number"
    sent = cl_db.execute(select(EmailMessage).where(EmailMessage.kind == "debtor_outreach")).scalars().all()
    assert any(e.to_address == "ada@example.com" for e in sent)


def test_reply_stop_opts_out_and_dispute_reply_opens_dispute(cl_db):
    client, _ = make_client(cl_db)
    a = make_account(cl_db, client, phone="+2348031111111")
    b = make_account(cl_db, client, phone="+2348032222222")
    ia, ib = add_sent(cl_db, a, wat(MON, 9)), add_sent(cl_db, b, wat(MON, 9))
    outreach.log_reply(cl_db, ia, "Asks to stop", "STOP", audit.SYSTEM)
    outreach.log_reply(cl_db, ib, "Disputes amount", "I paid half already", audit.SYSTEM)
    assert a.opted_out_at is not None
    assert b.status == AccountStatus.DISPUTED and service.open_dispute(cl_db, b).raised_by == "Ops"


def test_stale_queue_items_expire_next_day(cl_db):
    client, _ = make_client(cl_db)
    account = make_account(cl_db, client)
    outreach.build_queue(cl_db, wat(MON, 8))
    [item] = queued(cl_db, account)
    outreach.build_queue(cl_db, wat(MON + timedelta(days=1), 8))
    assert item.status == "Skipped" and item.skip_reason.startswith("Expired")
