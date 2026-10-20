import logging

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_web import login, own_account, post, rule_form
from test_web import web as web

from campushallen.database.models import (
    AuditEvent,
    BookingRule,
    CampushallenAccount,
    CampushallenCredential,
    Invitation,
    User,
    WebSession,
    utcnow,
)
from campushallen.services.credentials import remember
from campushallen.services.logging import InviteAccessFilter
from campushallen.services.user_security import set_admin


def test_password_change_revokes_all_sessions_and_accepts_long_passwords(web):
    client, app = web
    login(client)
    with TestClient(app) as other:
        login(other)
        assert client.get("/settings").status_code == 200
        data = {"current_password": "wrong", "new_password": "a" * 1500, "confirmation": "a" * 1500}
        assert post(client, "/settings/password", data).status_code == 422
        assert other.get("/", follow_redirects=False).status_code == 200
        data["current_password"] = "correct-horse-battery"
        assert post(client, "/settings/password", data).status_code == 303
        assert client.get("/", follow_redirects=False).status_code == 303
        assert other.get("/", follow_redirects=False).status_code == 303
    assert (
        post(
            client, "/login", {"username": "alice", "password": "correct-horse-battery"}, "/login"
        ).status_code
        == 400
    )
    assert (
        post(client, "/login", {"username": "alice", "password": "a" * 1500}, "/login").status_code
        == 303
    )
    with app.state.sessions() as db:
        assert "PASSWORD_CHANGED" in db.scalars(select(AuditEvent.code)).all()


def test_logout_all_revokes_other_browser_sessions(web):
    client, app = web
    login(client)
    with TestClient(app) as other:
        login(other)
        assert post(client, "/settings/sessions/logout-all").status_code == 303
        assert other.get("/settings", follow_redirects=False).status_code == 303
    with app.state.sessions() as db:
        assert not db.scalars(select(WebSession)).all()


def test_new_account_actions_are_owned_and_csrf_protected(web):
    client, app = web
    login(client)
    for path in (
        f"/accounts/{app.state.bob_account}/credentials/forget",
        f"/accounts/{app.state.bob_account}/disconnect",
        f"/accounts/{app.state.bob_account}/delete",
    ):
        assert post(client, path).status_code == 404
    for path in ("/settings/password", "/settings/delete", "/settings/sessions/logout-all"):
        assert client.post(path).status_code == 403
    assert client.get("/openapi.json").status_code == 404


def test_default_remember_opt_out_and_forget_ui_never_exposes_saved_secrets(
    web, monkeypatch, caplog
):
    client, app = web
    login(client)
    account_id = own_account(client, app)

    def connect(account, password):
        account.auth_state = app.state.gateway.vault.seal(
            {"access_token": "private-token", "customer_id": 100}
        )
        account.auth_status = "connected"
        account.requires_reconnect = False

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    path = f"/accounts/{account_id}"
    assert 'name="remember" checked' in client.get(path).text
    assert post(client, path + "/connect", {"password": "one-use-password"}).status_code == 303
    with app.state.sessions() as db:
        assert db.scalar(select(CampushallenCredential)) is None
    assert (
        post(
            client, path + "/connect", {"password": "private-campu-password", "remember": "on"}
        ).status_code
        == 303
    )
    with app.state.sessions() as db:
        row = db.scalar(select(CampushallenCredential))
        ciphertext = row.encrypted_password
        assert app.state.credential_vault.open(account_id, ciphertext) == "private-campu-password"
    for route in (path, "/accounts", "/", "/history", "/settings"):
        response = client.get(route)
        assert response.status_code == 200
        assert "private-campu-password" not in response.text and ciphertext not in response.text
        assert "private-token" not in response.text
    assert "private-campu-password" not in caplog.text
    assert post(client, path + "/credentials/forget").status_code == 303
    with app.state.sessions() as db:
        assert db.scalar(select(CampushallenCredential)) is None
        assert db.get(CampushallenAccount, account_id).auth_state


def test_disconnect_and_account_delete_are_local_and_preserve_other_users(web):
    client, app = web
    login(client)
    account_id = own_account(client, app)
    assert post(client, "/rules", rule_form(account_id)).status_code == 303
    with app.state.sessions() as db:
        account = db.get(CampushallenAccount, account_id)
        account.auth_state = "opaque-encrypted-token"
        remember(db, app.state.credential_vault, account, "opaque-password")
        db.commit()
    assert post(client, f"/accounts/{account_id}/disconnect").status_code == 422
    assert (
        post(
            client, f"/accounts/{account_id}/disconnect", {"confirmation": "DISCONNECT"}
        ).status_code
        == 303
    )
    with app.state.sessions() as db:
        assert db.get(CampushallenAccount, account_id).auth_state is None
        assert db.scalar(select(CampushallenCredential)) is None
        assert db.scalar(
            select(BookingRule).where(BookingRule.campushallen_account_id == account_id)
        )
    assert (
        post(
            client,
            f"/accounts/{account_id}/delete",
            {"password": "wrong", "confirmation": "DELETE"},
        ).status_code
        == 422
    )
    assert (
        post(
            client,
            f"/accounts/{account_id}/delete",
            {"password": "correct-horse-battery", "confirmation": "DELETE"},
        ).status_code
        == 303
    )
    with app.state.sessions() as db:
        assert db.get(CampushallenAccount, account_id) is None
        assert db.get(CampushallenAccount, app.state.bob_account) is not None


def test_full_account_deletion_cascades_and_last_admin_is_protected(web):
    client, app = web
    login(client)
    account_id = own_account(client, app)
    with app.state.sessions() as db:
        user = db.get(User, app.state.alice_id)
        set_admin(db, user, enabled=True)
        account = db.get(CampushallenAccount, account_id)
        account.auth_state = "opaque-token"
        remember(db, app.state.credential_vault, account, "opaque-password")
        db.commit()
    with app.state.sessions() as db:
        invitation = Invitation(
            token_hash="used-tombstone",
            label="Identifying label",
            used_at=utcnow(),
            used_by_user_id=app.state.alice_id,
        )
        db.add(invitation)
        db.commit()
        invite_id = invitation.id
    data = {"password": "correct-horse-battery", "confirmation": "DELETE"}
    assert post(client, "/settings/delete", data).status_code == 422
    with app.state.sessions() as db:
        bob = db.scalar(select(User).where(User.username == "bob"))
        set_admin(db, bob, enabled=True)
    assert post(client, "/settings/delete", {**data, "confirmation": "NO"}).status_code == 422
    assert post(client, "/settings/delete", data).status_code == 303
    with app.state.sessions() as db:
        assert db.get(User, app.state.alice_id) is None
        invitation = db.get(Invitation, invite_id)
        assert invitation.used_at and invitation.used_by_user_id is None and invitation.label == ""
        assert db.get(CampushallenAccount, account_id) is None
        assert not db.scalars(select(CampushallenCredential)).all()
        assert not db.scalars(select(WebSession)).all()
        assert not db.scalars(
            select(AuditEvent).where(AuditEvent.user_id == app.state.alice_id)
        ).all()
        assert db.get(CampushallenAccount, app.state.bob_account)


def test_invite_access_logs_redact_bearer_tokens():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("local", "GET", "/invite/private-token-value?x=1", "1.1", 200),
        None,
    )
    InviteAccessFilter().filter(record)
    assert "private-token-value" not in record.getMessage()
    assert "[redacted]" in record.getMessage()


def test_form_body_size_limit_handles_declared_and_streamed_bodies(web):
    client, app = web
    assert client.post("/login", content=b"x" * 262145).status_code == 413
    chunks = (b"x" * 65536 for _ in range(5))
    assert client.post("/login", content=chunks).status_code == 413


def test_expired_token_status_is_visible_without_triggering_network(web):
    from datetime import timedelta

    from campushallen.database.models import utcnow

    client, app = web
    login(client)
    account_id = own_account(client, app)
    with app.state.sessions() as db:
        account = db.get(CampushallenAccount, account_id)
        account.auth_status = "connected"
        account.auth_state = app.state.gateway.vault.seal(
            {
                "access_token": "private-token",
                "customer_id": 100,
                "expires_at": (utcnow() - timedelta(minutes=1)).timestamp(),
            }
        )
        db.commit()
    assert (
        "Anslutningen har gått ut. Återanslutning krävs"
        in client.get(f"/accounts/{account_id}").text
    )
    assert "private-token" not in client.get("/").text
