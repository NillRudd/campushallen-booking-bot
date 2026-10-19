"""Persistent website limits are independent of Campushallen reauthentication."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_web import csrf, post
from test_web import web as web

from campushallen.database.models import AuditEvent, WebsiteLoginBucket, utcnow
from campushallen.services import website_login
from campushallen.services.website_login import bucket_key, password_login
from campushallen.web.app import create_app


def attempt(app, username="alice", password="wrong", ip="192.0.2.1", now=None):
    with app.state.sessions() as db:
        return password_login(db, app.state.secret, username, password, ip, now=now)


def account_bucket(app, username="alice"):
    with app.state.sessions() as db:
        return db.get(WebsiteLoginBucket, bucket_key(app.state.secret, "account", username))


def test_failure_and_success_reset_only_website_state(web):
    _, app = web
    assert not attempt(app).user
    assert account_bucket(app).attempts == 1
    assert attempt(app, password="correct-horse-battery").user.username == "alice"
    assert account_bucket(app).attempts == 0
    assert account_bucket(app).locked_until is None


def test_tenth_failure_locks_and_eleventh_skips_password_verification(web):
    _, app = web
    with patch.object(website_login, "authenticate", wraps=website_login.authenticate) as verify:
        for number in range(10):
            result = attempt(app)
            assert result.limited == (number == 9)
        assert attempt(app, password="correct-horse-battery").limited
        assert verify.call_count == 10
    row = account_bucket(app)
    assert row.attempts == 10 and row.locked_until.tzinfo is not None
    assert row.locked_until > utcnow() + timedelta(minutes=29)


def test_unknown_username_has_equivalent_failure_and_lock_policy(web):
    _, app = web
    for number in range(11):
        existing = attempt(app, ip="192.0.2.1")
        unknown = attempt(app, username="does-not-exist", ip="192.0.2.2")
        assert (existing.user, existing.limited) == (unknown.user, unknown.limited)
    assert account_bucket(app, "does-not-exist").attempts == 10


def test_lock_survives_app_restart_and_has_generic_swedish_response(web):
    client, app = web
    for _ in range(10):
        attempt(app)
    restarted = create_app(app.state.settings)
    with TestClient(restarted) as fresh:
        response = post(
            fresh, "/login", {"username": "alice", "password": "correct-horse-battery"}, "/login"
        )
        assert response.status_code == 429
        assert "För många misslyckade inloggningsförsök" in response.text
        assert "30 minuter" in response.text
        assert "session" not in fresh.cookies
    assert account_bucket(app).attempts == 10
    assert "password" not in client.get("/health").text


def test_lock_expiry_is_automatic_and_does_not_extend_on_blocked_request(web):
    _, app = web
    now = utcnow()
    for _ in range(10):
        attempt(app, now=now)
    expiry = account_bucket(app).locked_until
    assert attempt(app, now=now + timedelta(minutes=20)).limited
    assert account_bucket(app).locked_until == expiry
    result = attempt(app, password="correct-horse-battery", now=now + timedelta(minutes=31))
    assert result.user and not result.limited
    assert account_bucket(app).attempts == 0
    with app.state.sessions() as db:
        codes = set(db.scalars(select(AuditEvent.code)))
    assert {"WEBSITE_LOGIN_LOCKED", "WEBSITE_LOGIN_LOCK_EXPIRED", "WEBSITE_LOGIN_SUCCESS"} <= codes


def test_failure_observation_window_expires(web):
    _, app = web
    now = utcnow()
    for _ in range(9):
        attempt(app, now=now)
    assert not attempt(app, now=now + timedelta(minutes=31)).limited
    assert account_bucket(app).attempts == 1


def test_ip_limit_is_independent_and_cannot_lock_other_accounts(web):
    _, app = web
    now = utcnow()
    for index in range(20):
        assert not attempt(app, username=f"absent-{index}", now=now).limited
    assert attempt(app, username="bob", password="correct-horse-battery", now=now).limited
    assert account_bucket(app, "bob") is None
    assert attempt(
        app, username="bob", password="correct-horse-battery", ip="192.0.2.2", now=now
    ).user
    assert attempt(app, password="correct-horse-battery", now=now + timedelta(minutes=16)).user


def test_one_username_lock_does_not_lock_another(web):
    _, app = web
    for _ in range(10):
        attempt(app)
    assert attempt(app, username="bob", password="correct-horse-battery").user
    assert attempt(app, password="correct-horse-battery", ip="192.0.2.50").limited


def test_concurrent_failures_serialize_at_persistent_boundary(web):
    _, app = web
    with patch.object(website_login, "authenticate", return_value=None) as verify:
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda index: attempt(app, ip=f"192.0.2.{index}"), range(24)))
        assert verify.call_count == 10
    assert account_bucket(app).attempts == 10
    assert sum(not result.limited for result in results) == 9


def test_no_password_or_raw_identifiers_in_buckets_or_audit_logs(web, caplog):
    _, app = web
    password = "never-persist-this-submitted-password"
    attempt(app, username="absent-secret-username", password=password, ip="192.0.2.99")
    with app.state.sessions() as db:
        rows = db.scalars(select(WebsiteLoginBucket)).all()
        events = db.scalars(select(AuditEvent)).all()
        assert all(len(row.key) == 64 for row in rows)
        assert all(not hasattr(row, "password") for row in rows)
        assert all(password not in str(event.__dict__) for event in events)
    assert password not in caplog.text and "absent-secret-username" not in caplog.text


def test_generic_http_responses_and_csrf_are_preserved(web):
    client, _ = web
    token = csrf(client)
    existing = post(client, "/login", {"username": "alice", "password": "wrong"}, "/login")
    unknown = post(client, "/login", {"username": "nobody", "password": "wrong"}, "/login")
    assert existing.status_code == unknown.status_code == 400
    assert existing.text == unknown.text
    assert "Felaktigt användarnamn eller lösenord" in existing.text
    assert client.post("/login", data={"csrf_token": token + "tampered"}).status_code == 403
