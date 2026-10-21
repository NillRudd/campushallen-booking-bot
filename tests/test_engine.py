import asyncio
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from campushallen.api import GroupActivitySlot
from campushallen.database.models import (
    BookingAttempt,
    BookingClaim,
    BookingIdentity,
    BookingRule,
    CampushallenAccount,
    Outcome,
    User,
    utcnow,
)
from campushallen.database.session import session_factory
from campushallen.services.engine import BookingEngine, Plan
from campushallen.services.scheduler import Scheduler
from campushallen.services.secrets import ReconnectRequired, TokenVault

TZ = ZoneInfo("Europe/Stockholm")


class FakeClient:
    def __init__(self, gateway, account_id):
        self.gateway, self.account_id = gateway, account_id

    def group_activities(self, period_start, period_end, **kwargs):
        from campushallen.api import _api_datetime

        return [
            {
                "id": s.id,
                "name": s.name,
                "groupActivityProduct": {"id": 1, "name": s.product_name},
                "duration": {"start": _api_datetime(s.starts_at), "end": _api_datetime(s.ends_at)},
                "businessUnit": {"id": 1, "name": "Campushallen"},
                "locations": [{"name": "Vallastaden"}],
                "bookableEarliest": _api_datetime(s.bookable_earliest)
                if s.bookable_earliest
                else None,
                "slots": {"leftToBook": s.left_to_book, "hasWaitingList": s.has_waiting_list},
            }
            for s in self.gateway.slots
            if period_start <= s.starts_at <= period_end
        ]

    def customer_group_activity_bookings(self, customer_id):
        assert customer_id == self.account_id * 100
        return [{"groupActivity": {"id": i}} for i in self.gateway.booked.get(customer_id, set())]

    def book_group_activity(self, customer_id, activity_id, *, allow_waiting_list=False):
        if self.gateway.barrier:
            self.gateway.barrier.wait(timeout=3)
        self.gateway.posts.append((customer_id, activity_id, allow_waiting_list))
        if self.gateway.verify:
            self.gateway.booked.setdefault(customer_id, set()).add(activity_id)
        return {"success": True}


class FakeGateway:
    settings = SimpleNamespace(campushallen_timezone="Europe/Stockholm")

    def __init__(self, slots):
        self.vault = TokenVault(Fernet.generate_key().decode())
        self.slots = slots
        self.posts = []
        self.booked = {}
        self.failed = set()
        self.verify = True
        self.barrier = None

    @contextmanager
    def client(self, account):
        if account.id in self.failed:
            raise ReconnectRequired("not connected")
        yield FakeClient(self, account.id), account.id * 100


@pytest.fixture
def booking(database):
    sessions = session_factory(database)
    now = utcnow()
    start = (
        (now + timedelta(days=7))
        .astimezone(TZ)
        .replace(hour=18, minute=45, second=0, microsecond=0)
    )
    slot = GroupActivitySlot(
        id=101,
        name="Ball 85",
        product_id=1,
        product_name="Ball 85",
        business_unit_id=1,
        business_unit_name="Campushallen",
        location_names=("Vallastaden",),
        starts_at=start,
        ends_at=start + timedelta(minutes=85),
        total_slots=15,
        total_bookable=15,
        left_to_book=5,
        has_waiting_list=True,
        in_waiting_list=0,
        bookable_earliest=now - timedelta(minutes=1),
        raw={},
    )
    with sessions() as db:
        user = User(username="test", password_hash="unused")
        db.add(user)
        db.commit()
        accounts, rules = [], []
        for name in ("a", "b"):
            owner = User(username="owner-" + name, password_hash="unused")
            db.add(owner)
            db.flush()
            account = CampushallenAccount(
                user_id=owner.id,
                display_name=name,
                username=name,
                auth_status="connected",
                live_booking_enabled=True,
                live_booking_claim_limit=100,
            )
            db.add(account)
            db.commit()
            rule = BookingRule(
                campushallen_account_id=account.id,
                name="Ball",
                activity="Ball",
                product_name="Ball 85",
                weekday=start.weekday(),
                facility_contains=["Vallastaden"],
                earliest_allowed_time=time(17),
                latest_allowed_time=time(21),
            )
            db.add(rule)
            db.commit()
            accounts.append(account.id)
            rules.append(rule.id)
    gateway = FakeGateway([slot])
    with sessions() as db:
        for account_id in accounts:
            db.add(
                BookingIdentity(
                    digest=gateway.vault.identity_digest(account_id * 100), account_id=account_id
                )
            )
        db.commit()
    engine = BookingEngine(sessions, gateway)
    plan = Plan(accounts[0], rules[0], start.date(), Outcome.DRY_RUN, "Matched", slot)
    return SimpleNamespace(
        engine=engine,
        gateway=gateway,
        slot=slot,
        accounts=accounts,
        rules=rules,
        plan=plan,
        sessions=sessions,
    )


def test_correct_account_selection_and_verified_booking(booking):
    b = booking
    result = b.engine.run(b.plan, dry_run=False)
    assert result.result == "BOOKED"
    assert b.gateway.posts == [(b.accounts[0] * 100, 101, False)]
    assert b.engine.run(b.plan, dry_run=False).result == "ALREADY_BOOKED"
    assert len(b.gateway.posts) == 1
    other_plan = replace(b.plan, account_id=b.accounts[1], rule_id=b.rules[1])
    assert b.engine.run(other_plan, dry_run=False).result == "BOOKED"
    assert b.gateway.posts[-1][0] == b.accounts[1] * 100


def test_dry_run_is_default_and_no_api_opening_guess(booking):
    b = booking
    assert b.engine.run(b.plan).result == "DRY_RUN"
    assert not b.gateway.posts
    b.gateway.slots = [replace(b.slot, bookable_earliest=None)]
    assert b.engine.run(b.plan, dry_run=False).result == "NOT_OPEN"
    assert not b.gateway.posts


def test_future_opening_and_disabled_rules(booking):
    b = booking
    b.gateway.slots = [replace(b.slot, bookable_earliest=utcnow() + timedelta(hours=1))]
    assert b.engine.run(b.plan, dry_run=False).result == "NOT_OPEN"
    with b.sessions() as db:
        db.get(BookingRule, b.rules[0]).enabled = False
        db.commit()
    assert b.engine.run(b.plan, dry_run=False).result == "DISABLED"
    assert not b.gateway.posts


def test_waitlist_requires_explicit_rule_opt_in(booking):
    b = booking
    b.gateway.slots = [replace(b.slot, left_to_book=0)]
    assert b.engine.run(b.plan, dry_run=False).result == "SKIPPED_WAITLIST_ONLY"
    assert not b.gateway.posts
    with b.sessions() as db:
        db.get(BookingRule, b.rules[0]).allow_waitlist = True
        db.commit()
    assert b.engine.run(b.plan, dry_run=False).result == "BOOKED"
    assert b.gateway.posts == [(b.accounts[0] * 100, 101, True)]


def test_target_not_found_ambiguous_and_changed_target(booking):
    b = booking
    b.gateway.slots = []
    assert b.engine.run(b.plan, dry_run=False).result == "TARGET_NOT_FOUND"
    b.gateway.slots = [b.slot, replace(b.slot, id=102)]
    assert b.engine.run(b.plan, dry_run=False).result == "AMBIGUOUS_TARGET"
    b.gateway.slots = [replace(b.slot, id=102)]
    assert b.engine.run(b.plan, dry_run=False).result == "SUSPICIOUS_TARGET"
    assert not b.gateway.posts


def test_verification_failure_retains_claim_and_prevents_retry(booking):
    b = booking
    b.gateway.verify = False
    assert b.engine.run(b.plan, dry_run=False).result == "VERIFICATION_FAILED"
    assert b.engine.run(b.plan, dry_run=False).result == "CLAIMED"
    assert len(b.gateway.posts) == 1
    with b.sessions() as db:
        assert db.get(BookingClaim, (b.accounts[0], 101)) is not None
        attempts = list(db.scalars(select(BookingAttempt)))
        assert all(a.created_at.tzinfo is not None for a in attempts)


def test_scheduler_accounts_fail_independently(booking):
    b = booking
    b.gateway.failed.add(b.accounts[0])
    asyncio.run(Scheduler(b.engine, dry_run=False).once())
    assert b.gateway.posts == [(b.accounts[1] * 100, 101, False)]
    with b.sessions() as db:
        results = list(db.scalars(select(BookingAttempt.result)))
        assert "AUTH_FAILED" in results and "BOOKED" in results
        assert db.get(CampushallenAccount, b.accounts[0]).auth_status == "reconnect_required"


def test_scheduler_simultaneous_targets_use_concurrent_accounts(booking):
    b = booking
    b.gateway.barrier = threading.Barrier(2)
    asyncio.run(Scheduler(b.engine, dry_run=False).once())
    assert sorted(p[0] for p in b.gateway.posts) == [a * 100 for a in b.accounts]


def test_scheduler_defaults_to_safe_preview_and_deduplicates(booking):
    b = booking

    async def run():
        scheduler = Scheduler(b.engine)
        await scheduler.once()
        await scheduler.once()
        # A new scheduler also recognizes persisted dry-runs.
        await Scheduler(b.engine).once()

    asyncio.run(run())
    assert b.gateway.posts == []
    with b.sessions() as db:
        assert len(list(db.scalars(select(BookingAttempt)))) == 2


def test_discovery_rule_filters_and_disabled_account(booking):
    b = booking
    with b.sessions() as db:
        rule = db.get(BookingRule, b.rules[1])
        rule.product_name = "Yoga"
        db.commit()
    assert b.engine.discover(b.accounts[1])[0].outcome == Outcome.TARGET_NOT_FOUND
    with b.sessions() as db:
        db.get(CampushallenAccount, b.accounts[0]).enabled = False
        db.commit()
    assert b.engine.discover(b.accounts[0]) == []


def test_live_failure_uncertain_response_never_retries(booking, monkeypatch):
    import httpx

    b = booking

    def fail(*args, **kwargs):
        raise httpx.ReadTimeout("sensitive-third-party-details")

    monkeypatch.setattr(FakeClient, "book_group_activity", fail)
    result = b.engine.run(b.plan, dry_run=False)
    assert result.result == "VERIFICATION_FAILED"
    assert "sensitive" not in result.message
    assert b.engine.run(b.plan, dry_run=False).result == "CLAIMED"


def test_scheduler_waits_using_api_opening_without_blocking_threads(booking, monkeypatch):
    import campushallen.services.scheduler as scheduler_module

    b = booking
    clock = [utcnow()]
    opening = clock[0] + timedelta(seconds=65)
    plan = replace(b.plan, slot=replace(b.slot, bookable_earliest=opening))
    sleeps, calls = [], []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += timedelta(seconds=seconds)

    def run(plan, *, dry_run):
        assert clock[0] >= opening
        assert dry_run
        calls.append(plan)
        return SimpleNamespace(result="DRY_RUN")

    monkeypatch.setattr(scheduler_module, "utcnow", lambda: clock[0])
    monkeypatch.setattr(b.engine, "run", run)
    asyncio.run(Scheduler(b.engine, sleep=fake_sleep).execute(plan, wait=True))
    assert sleeps == [30, 30, 5]
    assert calls == [plan]


def test_scheduler_serializes_overlapping_rules_for_one_account(booking):
    b = booking
    with b.sessions() as db:
        db.get(CampushallenAccount, b.accounts[1]).enabled = False
        original = db.get(BookingRule, b.rules[0])
        db.add(
            BookingRule(
                campushallen_account_id=b.accounts[0],
                name="Overlapping rule",
                activity=original.activity,
                product_name=original.product_name,
                weekday=original.weekday,
                earliest_allowed_time=time(17),
                latest_allowed_time=time(21),
            )
        )
        db.commit()
    asyncio.run(Scheduler(b.engine, dry_run=False).once())
    assert len(b.gateway.posts) == 1
    with b.sessions() as db:
        assert "ALREADY_BOOKED" in db.scalars(select(BookingAttempt.result)).all()


def test_fixed_time_rule_selects_exact_session_among_multiple(booking):
    b = booking
    b.gateway.slots.append(
        replace(b.slot, id=102, starts_at=b.slot.starts_at + timedelta(minutes=15))
    )
    with b.sessions() as db:
        db.get(BookingRule, b.rules[0]).start_time = b.slot.starts_at.time()
        db.commit()
    assert b.engine.run(b.plan, dry_run=False).result == "BOOKED"
    assert b.gateway.posts == [(b.accounts[0] * 100, 101, False)]


def test_worker_requires_both_flags_before_live_mode(monkeypatch):
    from campushallen.worker import main

    monkeypatch.setattr("sys.argv", ["campushallen-worker", "--live"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_legacy_attempt_provenance_cannot_suppress_preview_or_weaken_live_dedup(booking):
    from sqlalchemy import update

    from campushallen.database.models import BookingAttempt

    b = booking
    attempt = b.engine.record(b.plan)
    with b.sessions() as db:
        db.execute(
            update(BookingAttempt).where(BookingAttempt.id == attempt.id).values(is_preview=None)
        )
        db.commit()
    assert not Scheduler(b.engine).already_processed(b.plan)
    with b.sessions() as db:
        db.execute(
            update(BookingAttempt).where(BookingAttempt.id == attempt.id).values(result="BOOKED")
        )
        db.commit()
    assert Scheduler(b.engine, dry_run=False).already_processed(b.plan)
    assert not b.gateway.posts
