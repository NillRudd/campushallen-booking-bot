"""A human-approved extra attempt never erases or unlocks the original claim."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_engine import FakeClient
from test_engine import booking as booking

from campushallen.database.models import (
    BookingClaim,
    BookingReconciliation,
    CampushallenAccount,
    User,
    utcnow,
)
from campushallen.services.engine import BookingEngine
from campushallen.services.live_policy import approve_reconciliation, configure_live, used_claims


def prepare(b):
    b.gateway.verify = False
    assert b.engine.run(b.plan, dry_run=False).result == "VERIFICATION_FAILED"
    with b.sessions() as db:
        a = db.get(CampushallenAccount, b.accounts[0])
        db.get(BookingClaim, (a.id, 101)).created_at = utcnow() - timedelta(minutes=10)
        owner = db.get(User, a.user_id)
        owner.is_admin = True
        a.auth_state = "synthetic-test-state"
        configure_live(db, a.id, owner.username, enabled=True, limit=2, activity_id=101)
        identifier = approve_reconciliation(
            db, a.id, owner.username, 101, owner.username, confirm_remote_absent=True
        )
        original = db.get(BookingClaim, (a.id, 101)).created_at
    return identifier, original


def test_one_manual_attempt_preserves_original_claim_and_consumes_allowance(booking):
    b = booking
    identifier, original = prepare(b)
    assert b.engine.run(b.plan, dry_run=False).result == "CLAIMED"  # Scheduler cannot use approval.
    b.gateway.verify = True
    result = b.engine.run(b.plan, dry_run=False, manual_approval_id=identifier)
    assert result.result == "BOOKED" and len(b.gateway.posts) == 2
    with b.sessions() as db:
        assert db.get(BookingClaim, (b.accounts[0], 101)).created_at == original
        assert db.get(BookingReconciliation, identifier).consumed_at is not None
        assert used_claims(db, b.accounts[0]) == 2
    b.gateway.booked.clear()
    assert (
        BookingEngine(b.sessions, b.gateway)
        .run(b.plan, dry_run=False, manual_approval_id=identifier)
        .result
        == "CLAIMED"
    )
    assert len(b.gateway.posts) == 2


def test_two_workers_cannot_consume_the_same_manual_approval(booking):
    b = booking
    identifier, _ = prepare(b)
    b.gateway.verify = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                BookingEngine(b.sessions, b.gateway).run,
                b.plan,
                dry_run=False,
                manual_approval_id=identifier,
            )
            for _ in range(2)
        ]
        [f.result() for f in futures]
    assert len(b.gateway.posts) == 2
    with b.sessions() as db:
        assert used_claims(db, b.accounts[0]) == 2


@pytest.mark.parametrize(
    "invalid",
    ["expired", "demoted", "inactive", "target", "other_connection", "no_claim", "wrong_id"],
)
def test_invalid_manual_approval_cannot_mutate(booking, invalid):
    b = booking
    identifier, _ = prepare(b)
    plan = b.plan
    with b.sessions() as db:
        approval = db.get(BookingReconciliation, identifier)
        if invalid == "expired":
            approval.expires_at = utcnow() - timedelta(seconds=1)
        elif invalid == "demoted":
            db.get(User, approval.approved_by_user_id).is_admin = False
        elif invalid == "inactive":
            db.get(User, approval.approved_by_user_id).is_active = False
        elif invalid == "target":
            db.get(CampushallenAccount, b.accounts[0]).live_booking_activity_id = 999
        elif invalid == "other_connection":
            plan = replace(plan, account_id=b.accounts[1], rule_id=b.rules[1])
        elif invalid == "no_claim":
            db.delete(db.get(BookingClaim, (b.accounts[0], 101)))  # Isolated corrupt fixture only.
        elif invalid == "wrong_id":
            identifier += 100
        db.commit()
    b.engine.run(plan, dry_run=False, manual_approval_id=identifier)
    assert len(b.gateway.posts) == 1


def test_timeout_during_manual_attempt_is_permanently_spent(booking, monkeypatch):
    import httpx

    b = booking
    identifier, original = prepare(b)

    def fail(client, *args, **kwargs):
        b.gateway.posts.append(("controlled-uncertain", 101, False))
        raise httpx.ReadTimeout("private-test-provider-body")

    monkeypatch.setattr(FakeClient, "book_group_activity", fail)
    result = b.engine.run(b.plan, dry_run=False, manual_approval_id=identifier)
    assert result.result == "VERIFICATION_FAILED"
    b.engine.run(b.plan, dry_run=False, manual_approval_id=identifier)
    assert len(b.gateway.posts) == 2
    with b.sessions() as db:
        assert db.get(BookingClaim, (b.accounts[0], 101)).created_at == original
        assert used_claims(db, b.accounts[0]) == 2
        with pytest.raises(ValueError):
            approve_reconciliation(
                db, b.accounts[0], "owner-a", 101, "owner-a", confirm_remote_absent=True
            )


def test_manual_dry_run_and_approval_creation_are_not_mutation_allowance(booking):
    b = booking
    identifier, _ = prepare(b)
    with pytest.raises(ValueError):
        b.engine.run(b.plan, manual_approval_id=identifier)
    with b.sessions() as db:
        assert used_claims(db, b.accounts[0]) == 1
    assert len(b.gateway.posts) == 1


def test_waitlist_is_independently_classified(booking, monkeypatch):
    b = booking
    original = FakeClient.customer_group_activity_bookings

    def bookings(client, customer_id):
        rows = original(client, customer_id)
        return [
            dict(row, type="waitingListBooking", waitingListBooking={"waitingListPosition": 2})
            for row in rows
        ]

    monkeypatch.setattr(FakeClient, "customer_group_activity_bookings", bookings)
    result = b.engine.run(b.plan, dry_run=False)
    assert result.result == "BOOKED" and result.phase == "VERIFIED_WAITLISTED"
    assert result.verification_outcome == "WAITLISTED"
    assert len(b.gateway.posts) == 1


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--once"],
        ["--once", "--live"],
        ["--once", "--confirm-real-bookings"],
        ["--live", "--confirm-real-bookings"],
    ],
)
def test_manual_worker_requires_once_and_both_live_flags(flags, monkeypatch):
    from campushallen.worker import main

    monkeypatch.setattr("sys.argv", ["worker", "--manual-approval-id", "1", *flags])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_consumed_manual_permission_counts_against_future_ordinary_targets(booking):
    b = booking
    identifier, _ = prepare(b)
    b.engine.run(b.plan, dry_run=False, manual_approval_id=identifier)
    later = replace(
        b.slot,
        id=102,
        starts_at=b.slot.starts_at + timedelta(days=7),
        ends_at=b.slot.ends_at + timedelta(days=7),
    )
    b.gateway.slots.append(later)
    with b.sessions() as db:
        configure_live(db, b.accounts[0], "owner-a", enabled=True, limit=2, activity_id=102)
    plan = replace(b.plan, slot=later, target_date=later.starts_at.date())
    assert b.engine.run(plan, dry_run=False).result == "DISABLED"
    assert len(b.gateway.posts) == 2


def test_fresh_inflight_claim_cannot_receive_manual_approval(booking):
    b = booking
    identifier, _ = prepare(b)
    with b.sessions() as db:
        approval = db.get(BookingReconciliation, identifier)
        db.delete(approval)
        db.get(BookingClaim, (b.accounts[0], 101)).created_at = utcnow()
        db.commit()
        with pytest.raises(ValueError):
            approve_reconciliation(
                db, b.accounts[0], "owner-a", 101, "owner-a", confirm_remote_absent=True
            )


def test_downgrade_cannot_erase_a_consumed_manual_allowance(booking):
    from alembic import command
    from alembic.config import Config

    b = booking
    identifier, _ = prepare(b)
    b.engine.run(b.plan, dry_run=False, manual_approval_id=identifier)
    config = Config("alembic.ini")
    config.attributes["database_url"] = str(b.sessions.kw["bind"].url)
    with pytest.raises(
        RuntimeError, match="Cannot downgrade consumed|Preserve live identity ownership"
    ):
        command.downgrade(config, "a71e420f9c03")
    with b.sessions() as db:
        assert db.get(BookingReconciliation, identifier).consumed_at is not None
        assert used_claims(db, b.accounts[0]) == 2


@pytest.mark.parametrize(
    "invalid",
    ["no_confirmation", "wrong_owner", "not_admin", "no_allowance", "ongoing", "wrong_scope"],
)
def test_manual_approval_creation_fails_closed(booking, invalid):
    b = booking
    identifier, _ = prepare(b)
    with b.sessions() as db:
        db.delete(db.get(BookingReconciliation, identifier))
        db.commit()
        owner, operator, confirmed = "owner-a", "owner-a", True
        if invalid == "no_confirmation":
            confirmed = False
        elif invalid == "wrong_owner":
            owner = "owner-b"
        elif invalid == "not_admin":
            operator = "test"
        elif invalid == "no_allowance":
            db.get(CampushallenAccount, b.accounts[0]).live_booking_claim_limit = 1
        elif invalid == "ongoing":
            db.get(CampushallenAccount, b.accounts[0]).live_booking_ongoing = True
        elif invalid == "wrong_scope":
            db.get(CampushallenAccount, b.accounts[0]).live_booking_activity_id = 999
        db.commit()
        with pytest.raises(ValueError):
            approve_reconciliation(
                db, b.accounts[0], owner, 101, operator, confirm_remote_absent=confirmed
            )
        assert not db.scalars(select(BookingReconciliation)).all()


def test_manual_worker_executes_only_the_bound_target_through_engine(
    booking, tmp_path, monkeypatch, capsys
):
    import json

    import campushallen.worker as worker
    from campushallen.settings import Settings

    b = booking
    identifier, _ = prepare(b)
    b.gateway.verify = True
    settings = Settings(
        _env_file=None, database_url=str(b.sessions.kw["bind"].url), state_dir=tmp_path
    )
    monkeypatch.setattr(worker, "Settings", lambda: settings)
    monkeypatch.setattr(worker, "AccountGateway", lambda *_: b.gateway)
    monkeypatch.setattr(
        "sys.argv",
        [
            "worker",
            "--once",
            "--live",
            "--confirm-real-bookings",
            "--manual-approval-id",
            str(identifier),
        ],
    )
    worker.main()
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == "BOOKED" and result["phase"] == "VERIFIED_BOOKED"
    assert len(b.gateway.posts) == 2 and all(p[1] == 101 for p in b.gateway.posts)


def test_disconnect_revokes_pending_manual_approval_even_after_reenable(booking):
    from campushallen.services.account_management import disconnect

    b = booking
    identifier, _ = prepare(b)
    with b.sessions() as db:
        a = db.get(CampushallenAccount, b.accounts[0])
        disconnect(db, a)
        a.username = "new-identity@example.test"
        a.auth_state = "synthetic-new-state"
        a.requires_reconnect = False
        configure_live(db, a.id, "owner-a", enabled=True, limit=2, activity_id=101)
        assert db.get(BookingReconciliation, identifier).expires_at <= utcnow()
    assert b.engine.run(b.plan, dry_run=False, manual_approval_id=identifier).result == "DISABLED"
    assert len(b.gateway.posts) == 1
