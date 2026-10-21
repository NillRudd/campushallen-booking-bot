"""Three real gateway/auth paths, fake wire only, shared openings and isolation."""

import asyncio
import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from test_engine import FakeClient
from test_engine import booking as booking

from campushallen.api import CampushallenHttpClient
from campushallen.database.models import (
    BookingAttempt,
    BookingClaim,
    BookingIdentity,
    BookingRule,
    CampushallenAccount,
    User,
)
from campushallen.services.account_auth import AccountAuthentication
from campushallen.services.accounts import AccountGateway
from campushallen.services.credentials import remember
from campushallen.services.engine import BookingEngine, StalePlan
from campushallen.services.identities import (
    claim_identity_allowed,
    identity_for,
    register_external,
    reserve_identity,
)
from campushallen.services.scheduler import Scheduler
from campushallen.services.secrets import CredentialVault, TokenVault
from campushallen.settings import Settings


@pytest.mark.parametrize("mixed_failure", [False, "invalid_password", "temporary_backoff"])
def test_three_accounts_at_184500_use_independent_auth_claims_and_history(
    booking, tmp_path, monkeypatch, mixed_failure
):
    b = booking
    opening = datetime(2026, 10, 22, 16, 45, tzinfo=timezone.utc)
    start = opening + timedelta(days=7, hours=1)  # 18:45 CET, across DST
    slot = replace(
        b.slot, starts_at=start, ends_at=start + timedelta(minutes=85), bookable_earliest=opening
    )
    b.gateway.slots = [slot]
    with b.sessions() as db:
        user = User(username="beta-b", password_hash="unused")
        db.add(user)
        db.flush()
        a = CampushallenAccount(
            user_id=user.id,
            username="beta-b@example.test",
            display_name="Beta B",
            live_booking_enabled=True,
            live_booking_ongoing=True,
        )
        db.add(a)
        db.flush()
        db.add(
            BookingRule(
                campushallen_account_id=a.id,
                name="Beta Thursday",
                activity="Ball",
                product_name="Ball 85",
                weekday=start.weekday(),
            )
        )
        b.accounts.append(a.id)
        for rule in db.scalars(select(BookingRule)):
            rule.weekday = start.weekday()
        db.commit()
    tokens = TokenVault(Fernet.generate_key().decode())
    credentials = CredentialVault(Fernet.generate_key().decode())
    gateway = AccountGateway(Settings(_env_file=None, state_dir=tmp_path), tokens)
    gateway.auth_service = AccountAuthentication(b.sessions, gateway, tokens, credentials)
    by_email = {}
    with b.sessions() as db:
        for i, aid in enumerate(b.accounts):
            a = db.get(CampushallenAccount, aid)
            a.username = f"beta-{i}@example.test"
            by_email[a.username] = aid
            a.auth_state = tokens.seal(
                {"customer_id": aid * 100, "access_token": f"old-{aid}", "expires_at": 0}
            )
            remember(db, credentials, a, f"private-password-{aid}")
            old = db.scalar(select(BookingIdentity).where(BookingIdentity.account_id == aid))
            if old:
                old.account_id = None
                db.flush()
            db.add(BookingIdentity(digest=tokens.identity_digest(aid * 100), account_id=aid))
        db.commit()
    posts, logins, headers = [], [], []
    # No account can reach verification until the independent POSTs are in flight.
    barrier = threading.Barrier(2 if mixed_failure else 3)
    booked = set()

    def wire(request):
        if request.url.path.endswith("/auth/login"):
            body = json.loads(request.content)
            aid = by_email[body["username"]]
            assert body["password"] == f"private-password-{aid}"
            logins.append(aid)
            if mixed_failure and aid == b.accounts[1]:
                return httpx.Response(503 if mixed_failure == "temporary_backoff" else 401, json={})
            return httpx.Response(
                200,
                json={
                    "username": str(aid * 100),
                    "access_token": f"new-{aid}",
                    "expires_in": 100000000,
                },
            )
        header = request.headers["Authorization"]
        aid = int(header.removeprefix("Bearer new-"))
        assert aid in b.accounts
        headers.append((aid, request.method))
        if "/apps/" in request.url.path:
            assert request.method == "GET"
            return httpx.Response(
                200,
                json=FakeClient(b.gateway, aid).group_activities(
                    start - timedelta(hours=1), start + timedelta(hours=2)
                ),
            )
        if "/bookings/groupactivities" in request.url.path:
            assert f"/customers/{aid * 100}/" in request.url.path
            if request.method == "POST":
                assert json.loads(request.content) == {
                    "groupActivity": 101,
                    "allowWaitingList": False,
                }
                posts.append(aid)
                barrier.wait(timeout=5)
                booked.add(aid)
                return httpx.Response(201, json={})
            if aid in booked and mixed_failure and aid == b.accounts[2]:
                return httpx.Response(503, json={})
            return httpx.Response(
                200, json=[{"groupActivity": {"id": 101}}] if aid in booked else []
            )
        assert request.method == "GET" and request.url.path.endswith(f"/customers/{aid * 100}")
        return httpx.Response(200, json={"id": aid * 100})

    original = CampushallenHttpClient.__init__

    def init(self, config, **kwargs):
        original(self, config, **kwargs)
        self.client.close()
        self.client = httpx.Client(
            base_url="https://mock.test", transport=httpx.MockTransport(wire)
        )

    monkeypatch.setattr(CampushallenHttpClient, "__init__", init)
    clock = [opening - timedelta(seconds=1)]
    monkeypatch.setattr("campushallen.services.engine.utcnow", lambda: clock[0])
    monkeypatch.setattr("campushallen.services.account_auth.utcnow", lambda: clock[0])
    monkeypatch.setattr("campushallen.services.scheduler.utcnow", lambda: clock[0])
    engine = BookingEngine(b.sessions, gateway)

    async def exercise():
        sleeping = 0
        release = asyncio.Event()

        async def sleep(seconds):
            nonlocal sleeping
            sleeping += 1
            if sleeping == (2 if mixed_failure else 3):
                clock[0] = opening
                release.set()
            await release.wait()

        scheduler = Scheduler(engine, dry_run=False, concurrency=3, sleep=sleep)
        await scheduler.refresh()
        await asyncio.wait_for(asyncio.gather(*scheduler.tasks.values()), timeout=10)
        # Persisted claims/results also protect a newly started worker.
        await Scheduler(engine, dry_run=False, concurrency=3).once()

    asyncio.run(exercise())
    assert sorted(posts) == sorted([b.accounts[0], b.accounts[2]] if mixed_failure else b.accounts)
    assert sorted(logins) == sorted(b.accounts)  # one distinct credential login each
    with b.sessions() as db:
        for aid in b.accounts:
            a = db.get(CampushallenAccount, aid)
            results = list(
                db.scalars(
                    select(BookingAttempt.result).where(
                        BookingAttempt.campushallen_account_id == aid
                    )
                )
            )
            if mixed_failure and aid == b.accounts[1]:
                assert "AUTH_FAILED" in results
                if mixed_failure == "temporary_backoff":
                    assert not a.requires_reconnect and a.auth_retry_at > opening
                else:
                    assert a.requires_reconnect
                assert not db.get(BookingClaim, (aid, 101))
            else:
                assert db.get(BookingClaim, (aid, 101))
                expected = (
                    "VERIFICATION_FAILED" if mixed_failure and aid == b.accounts[2] else "BOOKED"
                )
                assert expected in results
                assert not a.requires_reconnect


def test_slow_discovery_does_not_delay_another_accounts_due_booking(booking, monkeypatch):
    b = booking
    first_post = threading.Event()
    original_discover = b.engine.discover
    original_post = FakeClient.book_group_activity

    def discover(aid):
        if aid == b.accounts[1]:
            assert first_post.wait(timeout=3), "Fast account was held behind slow discovery"
        return original_discover(aid)

    def post(self, *args, **kwargs):
        first_post.set()
        return original_post(self, *args, **kwargs)

    monkeypatch.setattr(b.engine, "discover", discover)
    monkeypatch.setattr(FakeClient, "book_group_activity", post)
    asyncio.run(Scheduler(b.engine, dry_run=False, concurrency=3).once())
    assert len(b.gateway.posts) == 2


def test_legacy_identity_cannot_activate_or_claim_even_with_different_display_email(booking):
    b = booking
    with b.sessions() as db:
        a = db.get(CampushallenAccount, b.accounts[0])
        a.live_booking_enabled = False
        a.auth_state = b.gateway.vault.seal({"customer_id": a.id * 100})
        owner = db.get(User, a.user_id)
        owner.is_admin = True
        register_external(db, b.gateway.vault, a.id * 100, "friend_legacy", active=True)
        db.commit()
        with pytest.raises(ValueError, match="Legacy booking system active"):
            reserve_identity(
                db, a, b.gateway.vault, owner.id, expected_digest=identity_for(a, b.gateway.vault)
            )
        db.rollback()
        a.live_booking_enabled = True  # even a direct flag write cannot bypass the guard
        db.commit()
    assert b.engine.run(b.plan, dry_run=False).result == "DISABLED"
    assert not b.gateway.posts


def test_duplicate_new_identity_and_changed_identity_cannot_inherit_live_approval(booking):
    b = booking
    with b.sessions() as db:
        a = db.get(CampushallenAccount, b.accounts[0])
        other = db.get(CampushallenAccount, b.accounts[1])
        other.auth_state = b.gateway.vault.seal({"customer_id": a.id * 100})
        owner = db.get(User, other.user_id)
        owner.is_admin = True
        with pytest.raises(ValueError, match="another live connection"):
            reserve_identity(
                db,
                other,
                b.gateway.vault,
                owner.id,
                expected_digest=identity_for(other, b.gateway.vault),
            )
        db.rollback()
        assert not claim_identity_allowed(db, a.id, b.gateway.vault.identity_digest(999999))
        assert not claim_identity_allowed(db, a.id, None)
        assert not claim_identity_allowed(db, other.id, b.gateway.vault.identity_digest(a.id * 100))


def test_unreviewed_identity_fails_closed_and_cannot_steal_other_users_rule(booking):
    b = booking
    with b.sessions() as db:
        db.delete(db.get(BookingIdentity, b.gateway.vault.identity_digest(b.accounts[0] * 100)))
        db.commit()
    assert b.engine.run(b.plan, dry_run=False).result == "DISABLED"
    with pytest.raises(StalePlan, match="Account or rule changed"):
        b.engine.run(replace(b.plan, account_id=b.accounts[1]), dry_run=False)
    assert not b.gateway.posts


def test_concurrent_alias_activation_has_only_one_live_identity_owner(booking):
    from concurrent.futures import ThreadPoolExecutor

    b = booking
    with b.sessions() as db:
        for aid in b.accounts:
            a = db.get(CampushallenAccount, aid)
            a.live_booking_enabled = False
            a.auth_state = b.gateway.vault.seal({"customer_id": 98765})
            db.get(User, a.user_id).is_admin = True
        db.commit()
    gate = threading.Barrier(2)

    def activate(aid):
        with b.sessions() as db:
            a = db.get(CampushallenAccount, aid)
            actor = a.user_id
            digest = identity_for(a, b.gateway.vault)
            gate.wait(timeout=3)
            try:
                reserve_identity(db, a, b.gateway.vault, actor, expected_digest=digest)
                a.live_booking_enabled = True
                db.commit()
                return True
            except ValueError:
                db.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(activate, b.accounts))
    assert sorted(outcomes) == [False, True]
    with b.sessions() as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(CampushallenAccount).where(
                            CampushallenAccount.live_booking_enabled.is_(True)
                        )
                    )
                )
            )
            == 1
        )


def test_identity_change_during_review_requires_fresh_review(booking):
    b = booking
    with b.sessions() as db:
        a = db.get(CampushallenAccount, b.accounts[0])
        a.auth_state = b.gateway.vault.seal({"customer_id": 100})
        db.get(User, a.user_id).is_admin = True
        db.commit()
        digest = identity_for(a, b.gateway.vault)
        with b.sessions() as changed:
            changed.get(CampushallenAccount, a.id).auth_state = b.gateway.vault.seal(
                {"customer_id": 200}
            )
            changed.commit()
        with pytest.raises(ValueError, match="identity changed during review"):
            reserve_identity(db, a, b.gateway.vault, a.user_id, expected_digest=digest)
        db.rollback()


def test_identity_registry_migration_preserves_exclusion_and_blocks_unsafe_downgrade(booking):
    from alembic import command
    from alembic.config import Config

    b = booking
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = str(b.sessions.kw["bind"].url)
    with pytest.raises(RuntimeError, match="Preserve live identity ownership"):
        command.downgrade(cfg, "b2d934a8e710")
    with b.sessions() as db:
        assert db.get(BookingIdentity, b.gateway.vault.identity_digest(b.accounts[0] * 100))
