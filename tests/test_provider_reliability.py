from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from test_booking_phases import incident as incident
from test_credentials_reauth import authenticated_gateway as authenticated_gateway
from test_credentials_reauth import expire, use
from test_engine import booking as booking

from campushallen.api import CampushallenHttpClient
from campushallen.database.models import (
    BookingAttempt,
    BookingClaim,
    CampushallenAccount,
    User,
    utcnow,
)
from campushallen.services.account_auth import AccountAuthentication
from campushallen.services.accounts import AccountGateway, api_config
from campushallen.services.provider_reads import ProviderReads
from campushallen.services.secrets import ReconnectRequired
from campushallen.settings import Settings


@pytest.fixture(autouse=True)
def no_wall_clock_retry_sleep(monkeypatch):
    monkeypatch.setattr("campushallen.services.provider_reads.time.sleep", lambda seconds: None)


@pytest.mark.parametrize("failure", [408, 429, 500, 502, 503, 504, "timeout"])
def test_temporary_reads_make_at_most_three_attempts(failure):
    calls = []

    def send():
        calls.append(1)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-response")
        return httpx.Response(failure)

    if failure == "timeout":
        with pytest.raises(httpx.ReadTimeout):
            ProviderReads()(send)
    else:
        assert ProviderReads()(send).status_code == failure
    assert len(calls) == 3


def test_read_recovers_on_third_attempt_and_never_retries_generic_403():
    statuses = iter([503, 502, 200])
    assert ProviderReads()(lambda: httpx.Response(next(statuses))).status_code == 200
    calls = []
    policy = ProviderReads()
    policy.reauthenticate = lambda: pytest.fail("Generic 403 must not log in")

    def forbidden():
        calls.append(1)
        return httpx.Response(403)

    assert policy(forbidden).status_code == 403 and len(calls) == 1


def test_login_network_failure_backs_off_then_recovers_without_password_latch(
    authenticated_gateway,
):
    b = authenticated_gateway
    expire(b)
    b.server.login_exception = httpx.ConnectTimeout
    with pytest.raises(ReconnectRequired):
        use(b)
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        assert not account.requires_reconnect and account.auth_retry_at > utcnow()
        with pytest.raises(ReconnectRequired):
            use(b)
        assert b.server.login_calls == 1
        account.auth_retry_at = utcnow() - timedelta(seconds=1)
        db.commit()
    b.server.login_exception = None
    assert use(b) == "Bearer fresh-token"
    assert b.server.login_calls == 2


def test_fresh_token_persists_into_new_worker_gateway(authenticated_gateway):
    b = authenticated_gateway
    expire(b)
    assert use(b) == "Bearer fresh-token"
    restarted = AccountGateway(b.gateway.settings, b.tokens)
    restarted.auth_service = AccountAuthentication(b.sessions, restarted, b.tokens, b.credentials)
    b.gateway = restarted
    assert use(b) == "Bearer fresh-token" and b.server.login_calls == 1


def test_reauthentication_does_not_change_other_account_state(authenticated_gateway):
    b = authenticated_gateway
    with b.sessions() as db:
        user = User(username="other-owner", password_hash="unused")
        db.add(user)
        db.flush()
        other = CampushallenAccount(user_id=user.id, display_name="Other", username="other")
        other.auth_state = b.tokens.seal(
            {"access_token": "other-private-token", "customer_id": 200}
        )
        db.add(other)
        db.commit()
        other_id, original = other.id, other.auth_state
    expire(b)
    assert use(b) == "Bearer fresh-token"
    with b.sessions() as db:
        assert db.get(CampushallenAccount, other_id).auth_state == original


def test_discovery_401_after_valid_identity_recovers_original_get(incident):
    b = incident
    b.server.discovery_token_rejected = True
    result = b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert result.result == "BOOKED" and b.server.posts == 1 and b.server.logins == 1


def test_preopening_auth_is_get_only_and_does_not_claim(incident):
    b = incident
    b.server.preinvalid = True
    b.engine.prepare_auth(b.plan)
    assert b.server.logins == 1 and b.server.posts == 0
    with b.sessions() as db:
        assert db.get(BookingClaim, (b.accounts[0], b.plan.slot.id)) is None
    assert b.engine.run(b.plan, dry_run=False, now=b.opening).result == "BOOKED"
    assert b.server.logins == 1 and b.server.posts == 1


def test_limit_rejection_retries_only_after_verified_absence_and_count_drop(incident, monkeypatch):
    b = incident
    original = httpx.Client.send
    sequence = []

    def send(self, request, *args, **kwargs):
        if request.method == "POST" and request.url.path.endswith("/bookings/groupactivities"):
            if not sequence:
                b.server.post_status = 403
                b.server.post_error = {"errorCode": "TOO_MANY_CONCURRENT_BOOKINGS"}
                b.server.verified = False
            else:
                b.server.post_status = 201
                b.server.post_error = None
                b.server.verified = True
            sequence.append(1)
        return original(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", send)
    assert b.engine.run(b.plan, dry_run=False, now=b.opening).result == "BOOKED"
    assert b.server.posts == 2
    with b.sessions() as db:
        messages = list(db.scalars(select(BookingAttempt.message)))
        assert "Booking POST attempt 1/3." in messages and "Booking POST attempt 2/3." in messages
        assert db.get(BookingClaim, (b.accounts[0], b.plan.slot.id))
    b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert b.server.posts == 2


def test_limit_count_stays_four_or_pause_prevents_another_post(incident, monkeypatch):
    b = incident
    b.server.post_status = 403
    b.server.post_error = {"errorCode": "TOO_MANY_CONCURRENT_BOOKINGS"}
    b.server.verified = False
    b.server.after_bookings = [{"groupActivity": {"id": i}} for i in range(10, 14)]
    assert b.engine.run(b.plan, dry_run=False, now=b.opening).verification_outcome == "NOT_BOOKED"
    assert b.server.posts == 1


def test_pause_during_retry_wait_keeps_claim_and_stops_mutation(incident, monkeypatch):
    b = incident
    b.server.post_status = 403
    b.server.post_error = {"errorCode": "TOO_MANY_CONCURRENT_BOOKINGS"}
    b.server.verified = False

    def pause(seconds):
        with b.sessions() as db:
            db.get(CampushallenAccount, b.accounts[0]).live_booking_enabled = False
            db.commit()

    monkeypatch.setattr("campushallen.services.engine.time.sleep", pause)
    b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert b.server.posts == 1


@pytest.mark.parametrize("second", ["timeout", "generic403", "server500"])
def test_uncertain_or_unclassified_second_response_stops_limit_loop(incident, monkeypatch, second):
    b = incident
    original = httpx.Client.send

    def send(self, request, *args, **kwargs):
        if request.method == "POST" and request.url.path.endswith("/bookings/groupactivities"):
            b.server.verified = False
            if b.server.posts == 0:
                b.server.post_status = 403
                b.server.post_error = {"errorCode": "TOO_MANY_CONCURRENT_BOOKINGS"}
            else:
                b.server.post_status = 403 if second == "generic403" else 500
                b.server.post_error = {"message": "private-provider-body"}
                b.server.timeout = second == "timeout"
        return original(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", send)
    result = b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert result.result == "VERIFICATION_FAILED" and b.server.posts == 2
    with b.sessions() as db:
        if second == "timeout":
            assert (
                db.scalar(
                    select(BookingAttempt).where(
                        BookingAttempt.phase == "BOOK_POST_TIMEOUT_OR_UNCERTAIN"
                    )
                ).http_status
                is None
            )
    b.engine.run(b.plan, dry_run=False, now=b.opening)
    assert b.server.posts == 2


def test_worker_post_does_not_follow_307_and_replay_mutation(tmp_path):
    client = CampushallenHttpClient(
        api_config(Settings(state_dir=tmp_path, _env_file=None)), load_saved_auth=False
    )
    client.client.close()
    calls = []

    def respond(request):
        calls.append(request.method)
        return httpx.Response(307, headers={"Location": "/elsewhere"})

    client.client = httpx.Client(
        base_url="https://example.test",
        follow_redirects=True,
        transport=httpx.MockTransport(respond),
    )
    client.read_policy = ProviderReads()
    with pytest.raises(httpx.HTTPStatusError):
        client.book_group_activity(100, 123)
    assert calls == ["POST"]
    client.close()


def test_valid_cached_token_wins_even_if_local_expiry_metadata_is_old(authenticated_gateway):
    b = authenticated_gateway
    expire(b)
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        state = b.tokens.open(account.auth_state)
        state["access_token"] = "valid-token"
        account.auth_state = b.tokens.seal(state)
        db.commit()
    assert use(b) == "Bearer valid-token"
    assert b.server.login_calls == 0


def test_read_retries_use_short_bounded_waits(monkeypatch):
    waits = []
    monkeypatch.setattr("campushallen.services.provider_reads.time.sleep", waits.append)
    statuses = iter([503, 503, 200])
    assert ProviderReads()(lambda: httpx.Response(next(statuses))).status_code == 200
    assert waits == [0.2, 0.5]


def test_post_auth_401_with_existing_booking_never_reposts(incident):
    b = incident
    b.server.post_status = 401
    b.server.verified = True
    assert b.engine.run(b.plan, dry_run=False, now=b.opening).result == "BOOKED"
    assert b.server.posts == 1 and b.server.logins == 1


def test_newer_persisted_cache_is_used_without_another_password_login(authenticated_gateway):
    b = authenticated_gateway
    expire(b)
    assert use(b) == "Bearer fresh-token"
    client = CampushallenHttpClient(api_config(b.gateway.settings), load_saved_auth=False)
    try:
        client.client.headers["Authorization"] = "Bearer old-token"
        assert b.gateway.reauthenticate(b.account_id, client) == 100
        assert client.client.headers["Authorization"] == "Bearer fresh-token"
        assert b.server.login_calls == 1
    finally:
        client.close()


@pytest.mark.parametrize("failure", ["generic403", "malformed"])
def test_non_auth_identity_failure_never_spends_password_login(
    authenticated_gateway, monkeypatch, failure
):
    b = authenticated_gateway
    expire(b)

    def broken(self, customer):
        if failure == "malformed":
            raise ValueError("private-response")
        response = httpx.Response(
            403,
            json={"message": "private-response"},
            request=httpx.Request("GET", "https://example.test"),
        )
        response.raise_for_status()

    monkeypatch.setattr(CampushallenHttpClient, "get_customer", broken)
    with pytest.raises(ReconnectRequired):
        use(b)
    assert b.server.login_calls == 0
    with b.sessions() as db:
        assert not db.get(CampushallenAccount, b.account_id).requires_reconnect


def test_explicit_provider_invalid_credentials_code_latches_even_if_http_400(authenticated_gateway):
    b = authenticated_gateway
    expire(b)
    b.server.login_status = 400
    b.server.login_body = {"errorCode": "USER_CREDENTIALS_ARE_INVALID"}
    for _ in range(2):
        with pytest.raises(ReconnectRequired):
            use(b)
    assert b.server.login_calls == 1
    with b.sessions() as db:
        assert db.get(CampushallenAccount, b.account_id).requires_reconnect


def test_cached_validation_clears_recovered_temporary_status_without_login(authenticated_gateway):
    b = authenticated_gateway
    expire(b)
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        state = b.tokens.open(account.auth_state)
        state["access_token"] = "valid-token"
        account.auth_state = b.tokens.seal(state)
        db.commit()
    b.server.identity_status = 503
    with pytest.raises(ReconnectRequired):
        use(b)
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        assert account.auth_status == "authentication_error" and not account.requires_reconnect
        account.auth_retry_at = utcnow() - timedelta(seconds=1)
        db.commit()
    b.server.identity_status = 200
    assert use(b) == "Bearer valid-token" and b.server.login_calls == 0
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        assert account.auth_status == "connected" and account.auth_failure_count == 0
        assert account.auth_retry_at is None
