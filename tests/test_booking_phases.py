"""October 20 DST incident regression: all provider requests use MockTransport."""

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

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
    CampushallenAccount,
)
from campushallen.services.account_auth import AccountAuthentication
from campushallen.services.accounts import AccountGateway
from campushallen.services.credentials import remember
from campushallen.services.engine import BookingEngine
from campushallen.services.secrets import CredentialVault, TokenVault
from campushallen.settings import Settings


@pytest.fixture
def incident(booking, tmp_path, monkeypatch):
    b = booking
    tz = ZoneInfo("Europe/Stockholm")
    opening = datetime(2026, 10, 20, 18, 45, tzinfo=tz)
    start = datetime(2026, 10, 27, 18, 45, tzinfo=tz)
    assert opening.utcoffset() == timedelta(hours=2)
    assert start.utcoffset() == timedelta(hours=1)
    slot = replace(
        b.slot,
        id=4343840,
        starts_at=start,
        ends_at=start + timedelta(minutes=85),
        bookable_earliest=opening,
    )
    b.gateway.slots = [slot]
    b.plan = replace(b.plan, slot=slot, target_date=start.date())
    key = Fernet.generate_key().decode()
    tokens, credentials = TokenVault(key), CredentialVault(key)
    gateway = AccountGateway(Settings(state_dir=tmp_path, _env_file=None), tokens)
    gateway.auth_service = AccountAuthentication(b.sessions, gateway, tokens, credentials)
    server = SimpleNamespace(
        posts=0,
        logins=0,
        post_status=200,
        timeout=False,
        preinvalid=False,
        verify401=False,
        verify_always401=False,
        verified=True,
        login_status=200,
        malformed_post=False,
        methods=[],
        post_error=None,
        before_bookings=[],
        after_bookings=[],
        verified_type="groupActivityBooking",
        discovery_token_rejected=False,
    )
    customer = b.accounts[0] * 100

    def respond(request):
        server.methods.append((request.method, request.url.path))
        if request.url.path.endswith("/auth/login"):
            server.logins += 1
            return httpx.Response(
                server.login_status,
                json={
                    "username": str(customer),
                    "access_token": "fresh-test-token",
                    "expires_in": 3600,
                },
            )
        if request.url.path.endswith("/bookings/groupactivities"):
            if request.method == "POST":
                server.posts += 1
                if server.timeout:
                    raise httpx.ReadTimeout("private-provider-body", request=request)
                if server.malformed_post:
                    return httpx.Response(200, text="private-provider-body")
                return httpx.Response(
                    server.post_status,
                    json=server.post_error or {"sensitive": "private-provider-body"},
                )
            assert request.method == "GET"
            if server.posts and (
                server.verify_always401
                or (
                    server.verify401
                    and request.headers.get("Authorization") != "Bearer fresh-test-token"
                )
            ):
                return httpx.Response(401, json={"sensitive": "private-provider-body"})
            return httpx.Response(
                200,
                json=[{"groupActivity": {"id": 4343840}, "type": server.verified_type}]
                if server.posts and server.verified
                else server.after_bookings
                if server.posts
                else server.before_bookings,
            )
        assert request.method == "GET", "No other mutations allowed"
        if "/apps/" in request.url.path:
            if (
                server.discovery_token_rejected
                and request.headers.get("Authorization") != "Bearer fresh-test-token"
            ):
                return httpx.Response(401)
            return httpx.Response(
                200,
                json=FakeClient(b.gateway, b.accounts[0]).group_activities(
                    start - timedelta(hours=1), start + timedelta(hours=2)
                ),
            )
        if server.preinvalid and request.headers.get("Authorization") != "Bearer fresh-test-token":
            return httpx.Response(401)
        return httpx.Response(200, json={"id": customer})

    original = CampushallenHttpClient.__init__

    def init(self, config, **kwargs):
        original(self, config, **kwargs)
        self.client.close()
        self.client = httpx.Client(
            base_url="https://mock.test", transport=httpx.MockTransport(respond)
        )

    monkeypatch.setattr(CampushallenHttpClient, "__init__", init)
    monkeypatch.setattr("campushallen.services.account_auth.utcnow", lambda: opening)
    monkeypatch.setattr("campushallen.services.engine.utcnow", lambda: opening)
    with b.sessions() as db:
        from campushallen.database.models import BookingRule

        db.get(BookingRule, b.rules[0]).weekday = start.weekday()
        account = db.get(CampushallenAccount, b.accounts[0])
        account.auth_state = tokens.seal(
            {
                "access_token": "original-test-token",
                "customer_id": customer,
                "expires_at": (start + timedelta(days=1)).timestamp(),
            }
        )
        db.get(BookingIdentity, b.gateway.vault.identity_digest(customer)).account_id = None
        db.flush()
        db.add(BookingIdentity(digest=tokens.identity_digest(customer), account_id=account.id))
        remember(db, credentials, account, "private-test-password")
        db.commit()
    b.engine = BookingEngine(b.sessions, gateway)
    b.server = server
    b.opening = opening
    return b


@pytest.mark.parametrize(
    "scenario,expected,phase,logins",
    [
        ("normal", "BOOKED", "VERIFIED_BOOKED", 0),
        ("preinvalid", "BOOKED", "VERIFIED_BOOKED", 1),
        ("post401", "VERIFICATION_FAILED", "BOOK_POST_AUTH_REJECTED", 1),
        ("post403", "VERIFICATION_FAILED", "BOOK_POST_AUTH_REJECTED", 0),
        ("post400", "VERIFICATION_FAILED", "BOOK_POST_REJECTED", 0),
        ("post500", "VERIFICATION_FAILED", "BOOK_POST_TIMEOUT_OR_UNCERTAIN", 0),
        ("verify401", "BOOKED", "VERIFY_AUTH_FAILED", 1),
        ("verify_always401", "VERIFICATION_FAILED", "VERIFY_AUTH_FAILED", 1),
        ("timeout", "VERIFICATION_FAILED", "BOOK_POST_TIMEOUT_OR_UNCERTAIN", 0),
        ("timeout_booked", "BOOKED", "BOOK_POST_TIMEOUT_OR_UNCERTAIN", 0),
    ],
)
def test_incident_phase_and_one_mutation(incident, scenario, expected, phase, logins, caplog):
    b = incident
    s = b.server
    if scenario.startswith("post"):
        s.post_status = int(scenario[4:])
        s.verified = False
    elif scenario.startswith("timeout"):
        s.timeout = True
        s.verified = scenario.endswith("booked")
    elif scenario != "normal":
        setattr(s, scenario, True)
    result = b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert result.result == expected
    assert s.posts == 1 and s.logins == logins
    with b.sessions() as db:
        attempts = db.scalars(select(BookingAttempt)).all()
        phases = [a.phase for a in attempts]
        assert phase in phases
        assert db.get(BookingClaim, (b.accounts[0], 4343840))
        if scenario.startswith("post"):
            assert any(
                a.http_status == s.post_status and a.endpoint_category == "booking"
                for a in attempts
            )
        assert all(a.attempted_at.tzinfo for a in attempts)
        assert "private-provider-body" not in str([a.message for a in attempts])
    # A new engine simulates restart; neither UNKNOWN nor verified state permits repost.
    restarted = BookingEngine(b.sessions, b.engine.gateway)
    restarted.run(b.plan, dry_run=False, now=b.opening)
    assert s.posts == 1
    assert "private-provider-body" not in caplog.text


def test_bad_saved_password_latches_before_claim(incident):
    b = incident
    b.server.preinvalid = True
    b.server.login_status = 401
    result = b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert result.phase == "PREBOOK_AUTH_FAILED"
    assert b.server.posts == 0 and b.server.logins == 1
    b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert b.server.logins == 1
    with b.sessions() as db:
        assert db.get(CampushallenAccount, b.accounts[0]).requires_reconnect
        assert db.get(BookingClaim, (b.accounts[0], 4343840)) is None


def test_preflight_and_verification_share_one_login_budget(incident):
    b = incident
    b.server.preinvalid = True
    b.server.verify_always401 = True
    result = b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert result.result == "VERIFICATION_FAILED"
    assert b.server.logins == 1 and b.server.posts == 1


def test_discovery_preview_dry_and_early_run_do_not_claim(incident):
    b = incident
    b.engine.discover(b.accounts[0], now=b.opening - timedelta(minutes=1))
    b.engine.run(b.plan, now=b.opening)
    assert (
        b.engine.run(b.plan, dry_run=False, now=b.opening - timedelta(seconds=1)).result
        == "NOT_OPEN"
    )
    with b.sessions() as db:
        assert db.get(BookingClaim, (b.accounts[0], 4343840)) is None
    assert b.server.posts == 0


def test_success_response_with_unreadable_body_retains_status_and_never_reposts(incident):
    b = incident
    b.server.malformed_post = True
    result = b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert result.result == "BOOKED"
    with b.sessions() as db:
        event = db.scalar(
            select(BookingAttempt).where(BookingAttempt.phase == "BOOK_POST_TIMEOUT_OR_UNCERTAIN")
        )
        assert event.http_status == 200
    assert b.server.posts == 1


@pytest.mark.parametrize("kind", ["groupActivityBooking", "waitingListBooking"])
def test_201_is_independently_verified_for_regular_and_waitlist(incident, kind):
    b = incident
    if kind == "waitingListBooking":
        from campushallen.database.models import BookingRule

        with b.sessions() as db:
            db.get(BookingRule, b.rules[0]).allow_waitlist = True
            db.commit()
    b.server.post_status = 201
    b.server.verified_type = kind
    result = b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert result.result == "BOOKED"
    assert result.verification_outcome == (
        "WAITLISTED" if kind == "waitingListBooking" else "BOOKED"
    )
    with b.sessions() as db:
        assert (
            db.scalar(
                select(BookingAttempt).where(BookingAttempt.phase == "BOOK_POST_ACCEPTED")
            ).http_status
            == 201
        )
    assert b.server.posts == 1 and b.server.logins == 0


def test_explicit_limit_four_then_three_has_three_total_attempts_and_no_restart_replay(
    incident, caplog, monkeypatch
):
    """The user-authorized exact-code retry stays within the original claim and budget."""
    b = incident
    b.opening = b.opening.replace(hour=19, minute=15)
    monkeypatch.setattr("campushallen.services.engine.time.sleep", lambda seconds: None)
    b.plan = replace(b.plan, slot=replace(b.plan.slot, bookable_earliest=b.opening))
    b.gateway.slots = [b.plan.slot]
    monkeypatch.setattr("campushallen.services.engine.utcnow", lambda: b.opening)
    b.server.post_status = 403
    b.server.post_error = {
        "errorCode": "TOO_MANY_CONCURRENT_BOOKINGS",
        "rule": "private-provider-body",
        "message": "private-provider-body",
        "customerId": "private-provider-body",
    }
    b.server.verified = False
    b.server.before_bookings = [
        {
            "groupActivity": {"id": i},
            "type": "waitingListBooking" if i == 204 else "groupActivityBooking",
        }
        for i in range(201, 205)
    ]
    b.server.after_bookings = b.server.before_bookings[1:]
    result = b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert result.verification_outcome == "NOT_BOOKED"
    with b.sessions() as db:
        events = list(db.scalars(select(BookingAttempt)))
        assert next(e for e in events if e.phase == "AUTH_VALIDATED").message == (
            "Authenticated bookings read: total=4; regular=3; waitlist=1."
        )
        rejection = next(e for e in events if e.phase == "BOOK_POST_REJECTED")
        assert rejection.http_status == 403
        assert "BOOKING_LIMIT_REACHED" in rejection.message
        assert "TOO_MANY_CONCURRENT_BOOKINGS" in rejection.message
        assert "private-provider-body" not in str([e.message for e in events])
    # The provider keeps rejecting despite a count of three: exhaust the total budget.
    assert b.server.posts == 3
    # A restart must never spend that budget again.
    later = b.opening + timedelta(seconds=2)
    assert len(b.server.after_bookings) == 3
    restarted = BookingEngine(b.sessions, b.engine.gateway)
    restarted.run(b.plan, dry_run=False, now=later)
    assert b.server.posts == 3
    assert "private-provider-body" not in caplog.text
    assert "TOO_MANY_CONCURRENT_BOOKINGS" in caplog.text


def test_provider_rejection_code_never_overrides_independent_mutation_evidence(incident):
    b = incident
    b.server.post_status = 403
    b.server.post_error = {"errorCode": "TOO_MANY_CONCURRENT_BOOKINGS"}
    # Even a recognized rejection string is not sufficient evidence to replay.
    b.server.verified = True
    result = b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert result.result == "BOOKED"
    assert result.verification_outcome == "BOOKED"
    b.engine.run(b.plan, dry_run=False, now=b.opening + timedelta(seconds=2))
    assert b.server.posts == 1
