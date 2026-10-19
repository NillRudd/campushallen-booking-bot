import re
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from campushallen.database.models import (
    BookingAttempt,
    BookingRule,
    CampushallenAccount,
    User,
    WebSession,
    utcnow,
)
from campushallen.services.authentication import create_user
from campushallen.settings import Settings
from campushallen.web.app import create_app


@pytest.fixture
def web(database, tmp_path):
    settings = Settings(
        database_url=str(database.url),
        state_dir=tmp_path,
        secret_key=SecretStr("x" * 48),
        encryption_key=SecretStr(Fernet.generate_key().decode()),
        _env_file=None,
    )
    app = create_app(settings)
    with app.state.sessions() as db:
        alice = create_user(db, "Alice", "correct-horse-battery")
        bob = create_user(db, "bob", "correct-horse-battery")
        account = CampushallenAccount(
            user_id=bob.id, display_name="Bob private", username="bob-external"
        )
        db.add(account)
        db.commit()
        rule = BookingRule(
            campushallen_account_id=account.id, name="Bob private rule", activity="ball", weekday=1
        )
        db.add(rule)
        db.commit()
        db.add(
            BookingAttempt(
                campushallen_account_id=account.id,
                booking_rule_id=rule.id,
                activity_name="Bob private session",
                result="DRY_RUN",
                message="Private",
            )
        )
        db.commit()
        app.state.bob_account, app.state.bob_rule = account.id, rule.id
        app.state.alice_id = alice.id
    with TestClient(app) as client:
        yield client, app


def csrf(client, path="/login"):
    response = client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, response.text
    return match.group(1)


def post(client, path, data=None, token_path="/"):
    return client.post(
        path, data={"csrf_token": csrf(client, token_path), **(data or {})}, follow_redirects=False
    )


def login(client):
    return post(
        client, "/login", {"username": "alice", "password": "correct-horse-battery"}, "/login"
    )


def own_account(client, app):
    response = post(
        client, "/accounts", {"display_name": "My account", "username": "alice-external"}
    )
    assert response.status_code == 303
    with app.state.sessions() as db:
        return db.scalar(
            select(CampushallenAccount.id).where(CampushallenAccount.user_id == app.state.alice_id)
        )


def rule_form(account_id):
    return {
        "campushallen_account_id": str(account_id),
        "name": "Ball Tuesday",
        "activity": "Ball",
        "product_name": "Ball 85",
        "weekday": "1",
        "booking_type": "group_activity",
        "web_category_id": "40",
        "preferred_start_time": "18:45",
        "earliest_allowed_time": "17:00",
        "latest_allowed_time": "21:00",
        "facility_contains": "Vallastaden",
        "enabled": "on",
    }


def test_authentication_and_session_revocation(web):
    client, app = web
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/health").json() == {"status": "ok"}
    assert (
        post(client, "/login", {"username": "alice", "password": "wrong"}, "/login").status_code
        == 400
    )
    assert login(client).status_code == 303
    token = client.cookies.get("session")
    assert token
    response = client.get("/")
    assert response.status_code == 200
    assert "HttpOnly" in client.get("/login").headers["set-cookie"]
    assert "SameSite=lax" in login(client).headers["set-cookie"]
    token = client.cookies.get("session")
    assert post(client, "/logout").status_code == 303
    client.cookies.set("session", token)
    assert client.get("/", follow_redirects=False).status_code == 303
    with app.state.sessions() as db:
        assert db.scalar(select(WebSession)) is None
        user = db.get(User, app.state.alice_id)
        assert user.password_hash.startswith("$argon2id$")
        assert "correct-horse" not in user.password_hash


def test_csrf_and_login_rate_limit(web):
    client, app = web
    assert client.post("/login", data={"username": "alice"}).status_code == 403
    token = csrf(client)
    assert client.post("/login", data={"csrf_token": token + "tampered"}).status_code == 403
    app.state.limiter.limit = 2
    for _ in range(2):
        assert (
            post(client, "/login", {"username": "absent", "password": "bad"}, "/login").status_code
            == 400
        )
    assert post(client, "/login", {}, "/login").status_code == 429


def test_expired_and_inactive_user_sessions(web):
    client, app = web
    login(client)
    with app.state.sessions() as db:
        session = db.scalar(select(WebSession))
        session.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/rules", follow_redirects=False).status_code == 303
    login(client)
    with app.state.sessions() as db:
        db.get(User, app.state.alice_id).is_active = False
        db.commit()
    assert client.get("/", follow_redirects=False).status_code == 303
    assert login(client).status_code == 400


def test_tenant_isolation_all_id_routes(web):
    client, app = web
    login(client)
    account, rule = app.state.bob_account, app.state.bob_rule
    for path in [f"/accounts/{account}", f"/rules/{rule}/edit"]:
        assert client.get(path).status_code == 404
    for path in [
        f"/accounts/{account}/toggle",
        f"/accounts/{account}/connect",
        f"/accounts/{account}/preview",
        f"/rules/{rule}/toggle",
        f"/rules/{rule}/delete",
        f"/rules/{rule}",
    ]:
        assert post(client, path, rule_form(account)).status_code == 404
    assert post(client, "/rules", rule_form(account)).status_code == 404
    for path in ["/", "/accounts", "/rules", "/history"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "Bob private" not in response.text
    with app.state.sessions() as db:
        assert db.get(BookingRule, rule).name == "Bob private rule"


def test_rule_crud_and_validation(web):
    client, app = web
    login(client)
    account = own_account(client, app)
    data = rule_form(account)
    assert client.get("/rules/new").status_code == 200
    invalid = {**data, "latest_allowed_time": "16:00"}
    response = post(client, "/rules", invalid)
    assert response.status_code == 422
    assert "Ball Tuesday" in response.text
    assert post(client, "/rules", {**data, "weekday": "8"}).status_code == 422
    assert post(client, "/rules", data).status_code == 303
    assert post(client, "/rules", data).status_code == 409
    with app.state.sessions() as db:
        rule = db.scalar(select(BookingRule).where(BookingRule.campushallen_account_id == account))
        rule_id = rule.id
        assert not rule.allow_waitlist
        assert rule.facility_contains == ["Vallastaden"]
    assert client.get(f"/rules/{rule_id}/edit").status_code == 200
    assert (
        post(
            client, f"/rules/{rule_id}", {**data, "name": "Changed", "allow_waitlist": "on"}
        ).status_code
        == 303
    )
    assert post(client, f"/rules/{rule_id}/toggle").status_code == 303
    with app.state.sessions() as db:
        rule = db.get(BookingRule, rule_id)
        assert rule.name == "Changed" and rule.allow_waitlist and not rule.enabled
    assert post(client, f"/rules/{rule_id}/delete").status_code == 303
    with app.state.sessions() as db:
        assert db.get(BookingRule, rule_id) is None


def test_connect_uses_gateway_and_never_exposes_secrets(web, monkeypatch):
    client, app = web
    login(client)
    account = own_account(client, app)

    def connect(row, password):
        assert password == "external-password"
        row.auth_state = "encrypted-token-not-for-browser"
        row.auth_status = "connected"

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    assert (
        post(client, f"/accounts/{account}/connect", {"password": "external-password"}).status_code
        == 303
    )
    response = client.get(f"/accounts/{account}")
    assert response.status_code == 200
    assert "encrypted-token-not-for-browser" not in response.text
    assert "external-password" not in response.text
    assert post(client, f"/accounts/{account}/toggle").status_code == 303
    with app.state.sessions() as db:
        row = db.get(CampushallenAccount, account)
        assert not row.enabled
        assert not hasattr(row, "password")


def test_templates_escape_activity_content(web):
    client, app = web
    login(client)
    account = own_account(client, app)
    data = {**rule_form(account), "name": '<script>alert("x")</script>'}
    assert post(client, "/rules", data).status_code == 303
    response = client.get("/rules")
    assert "<script>" not in response.text and "&lt;script&gt;" in response.text
