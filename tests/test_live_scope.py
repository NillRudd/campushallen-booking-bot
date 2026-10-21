from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from test_engine import FakeClient
from test_engine import booking as booking

from campushallen.database.models import BookingClaim, CampushallenAccount, User
from campushallen.services.engine import BookingEngine
from campushallen.services.live_policy import configure_live
from campushallen.services.scheduler import Scheduler


def test_accounts_default_to_no_live_permission_in_database_and_orm(booking):
    b = booking
    with b.sessions() as db:
        owner = User(username="future-user", password_hash="unused")
        db.add(owner)
        db.flush()
        row = CampushallenAccount(user_id=owner.id, username="new@example.com", display_name="New")
        db.add(row)
        db.commit()
        assert not row.live_booking_enabled and row.live_booking_claim_limit == 0
        owner_id = owner.id
        db.delete(row)
        db.commit()
        # Reusing an old numeric ID must never inherit operator eligibility.
        db.execute(
            text(
                "INSERT INTO campushallen_accounts (user_id,username,display_name,enabled,auth_status,created_at,updated_at) VALUES (:owner,'new@example.com','New',1,'disconnected',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {"owner": owner_id},
        )
        db.commit()
        row = db.scalar(select(CampushallenAccount).where(CampushallenAccount.user_id == owner_id))
        assert not row.live_booking_enabled and row.live_booking_claim_limit == 0


def test_global_live_does_not_grant_other_account_scope(booking):
    b = booking
    with b.sessions() as db:
        db.get(CampushallenAccount, b.accounts[1]).live_booking_enabled = False
        db.commit()
    scheduler = Scheduler(b.engine, dry_run=False)
    assert scheduler.accounts() == [b.accounts[0]]
    assert set(Scheduler(b.engine).accounts()) == set(b.accounts)
    other = replace(b.plan, account_id=b.accounts[1], rule_id=b.rules[1])
    assert b.engine.run(other, dry_run=False).result == "DISABLED"
    assert not b.gateway.posts


def test_live_eligibility_does_not_override_global_dry_run(booking):
    assert booking.engine.run(booking.plan).result == "DRY_RUN"
    assert not booking.gateway.posts


@pytest.mark.parametrize("revoked", ["account", "rule", "user"])
def test_account_gate_is_rechecked_after_network_reads(booking, monkeypatch, revoked):
    b = booking
    original = FakeClient.customer_group_activity_bookings

    def revoke(self, customer_id):
        result = original(self, customer_id)
        with b.sessions() as db:
            account = db.get(CampushallenAccount, b.accounts[0])
            if revoked == "account":
                account.live_booking_enabled = False
            elif revoked == "user":
                db.get(User, account.user_id).is_active = False
            else:
                from campushallen.database.models import BookingRule

                db.get(BookingRule, b.rules[0]).enabled = False
            db.commit()
        return result

    monkeypatch.setattr(FakeClient, "customer_group_activity_bookings", revoke)
    assert b.engine.run(b.plan, dry_run=False).result == "DISABLED"
    assert not b.gateway.posts


def test_one_authorized_attempt_is_durable_and_atomic_across_workers(booking):
    b = booking
    with b.sessions() as db:
        db.get(CampushallenAccount, b.accounts[0]).live_booking_claim_limit = 1
        db.commit()
    later = replace(
        b.slot,
        id=102,
        starts_at=b.slot.starts_at + timedelta(days=7),
        ends_at=b.slot.ends_at + timedelta(days=7),
    )
    b.gateway.slots.append(later)
    second = replace(b.plan, slot=later, target_date=later.starts_at.date())
    other_engine = BookingEngine(b.sessions, b.gateway)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(b.engine.run, b.plan, dry_run=False)
        z = pool.submit(other_engine.run, second, dry_run=False)
        assert sorted([a.result().result, z.result().result]) == ["BOOKED", "DISABLED"]
    assert len(b.gateway.posts) == 1
    with b.sessions() as db:
        assert len(db.scalars(select(BookingClaim)).all()) == 1
    loser = second if b.gateway.posts[0][1] == 101 else b.plan
    assert BookingEngine(b.sessions, b.gateway).run(loser, dry_run=False).result == "DISABLED"
    assert len(b.gateway.posts) == 1


def test_uncertain_attempt_consumes_allowance_without_reset(booking):
    b = booking
    b.gateway.verify = False
    with b.sessions() as db:
        db.get(CampushallenAccount, b.accounts[0]).live_booking_claim_limit = 1
        db.commit()
    assert b.engine.run(b.plan, dry_run=False).result == "VERIFICATION_FAILED"
    assert b.engine.run(b.plan, dry_run=False).result == "CLAIMED"
    assert len(b.gateway.posts) == 1


def test_operator_scope_requires_expected_owner_and_explicit_positive_allowance(booking):
    b = booking
    with b.sessions() as db:
        with pytest.raises(ValueError):
            configure_live(db, b.accounts[0], "wrong-owner", enabled=True, limit=1)
        with pytest.raises(ValueError):
            configure_live(db, b.accounts[0], "owner-a", enabled=True, limit=0)
        row = db.get(CampushallenAccount, b.accounts[0])
        row.auth_state = "synthetic-encrypted-state"
        db.commit()
        status = configure_live(
            db, b.accounts[0], "owner-a", enabled=True, limit=1, activity_id=101
        )
        assert status["live_booking_enabled"] and status["max_claims"] == 1
        assert not configure_live(db, b.accounts[0], "owner-a", enabled=False)[
            "live_booking_enabled"
        ]


def test_migration_does_not_opt_existing_accounts_in(booking):
    b = booking
    with b.sessions() as db:
        url = str(db.bind.url)
    # Remove only synthetic identity reservations before exercising the older
    # opt-in migration; production registry downgrade has its own guard test.
    from sqlalchemy import delete

    from campushallen.database.models import BookingIdentity

    with b.sessions() as db:
        db.execute(delete(BookingIdentity))
        db.commit()
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.downgrade(config, "e6b41c9a203d")
    command.upgrade(config, "head")
    with b.sessions() as db:
        rows = db.scalars(select(CampushallenAccount)).all()
        assert rows and all(
            not x.live_booking_enabled and x.live_booking_claim_limit == 0 for x in rows
        )
    command.check(config)


def test_reviewed_activity_gate_cannot_book_another_occurrence(booking):
    b = booking
    with b.sessions() as db:
        row = db.get(CampushallenAccount, b.accounts[0])
        row.live_booking_activity_id = 999
        row.live_booking_claim_limit = 1
        db.commit()
    assert b.engine.run(b.plan, dry_run=False).result == "DISABLED"
    assert not b.gateway.posts
    with b.sessions() as db:
        assert not db.scalars(select(BookingClaim)).all()


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_allowance_fails_closed(booking, limit):
    b = booking
    with b.sessions() as db:
        db.get(CampushallenAccount, b.accounts[0]).live_booking_claim_limit = limit
        db.commit()
    assert b.engine.run(b.plan, dry_run=False).result == "DISABLED"
    assert not b.gateway.posts


def test_confirmation_flag_alone_cannot_start_live_worker(booking, tmp_path, monkeypatch):
    import campushallen.worker as worker
    from campushallen.settings import Settings

    with booking.sessions() as db:
        settings = Settings(_env_file=None, database_url=str(db.bind.url), state_dir=tmp_path)
    observed = []

    async def fake_run(scheduler, **kwargs):
        observed.append(scheduler.dry_run)

    monkeypatch.setattr(worker, "Settings", lambda: settings)
    monkeypatch.setattr(worker, "run_worker", fake_run)
    monkeypatch.setattr("sys.argv", ["worker", "--confirm-real-bookings", "--once"])
    worker.main()
    assert observed == [True]


def test_disconnect_and_identity_replacement_revoke_scope_without_resetting_claims(booking):
    from campushallen.services.account_management import disconnect

    b = booking
    assert b.engine.run(b.plan, dry_run=False).result == "BOOKED"
    with b.sessions() as db:
        row = db.get(CampushallenAccount, b.accounts[0])
        disconnect(db, row)
        row.username = "replacement@example.com"
        row.auth_state = "synthetic-new-identity"
        row.requires_reconnect = False
        db.commit()
        assert not row.live_booking_enabled
        assert row.live_booking_claim_limit == 0 and row.live_booking_activity_id is None
        assert db.get(BookingClaim, (b.accounts[0], 101)) is not None


def test_explicit_ongoing_approval_still_requires_durable_claim_and_global_live(booking):
    b = booking
    with b.sessions() as db:
        a = db.get(CampushallenAccount, b.accounts[0])
        a.live_booking_ongoing = True
        a.live_booking_claim_limit = 0
        db.commit()
    assert b.engine.run(b.plan).result == "DRY_RUN"
    assert not b.gateway.posts
    assert b.engine.run(b.plan, dry_run=False).result == "BOOKED"
    b.gateway.booked.clear()
    assert BookingEngine(b.sessions, b.gateway).run(b.plan, dry_run=False).result == "CLAIMED"
    assert len(b.gateway.posts) == 1
