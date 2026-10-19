from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_web import login, post
from test_web import web as web

from campushallen.cli import main
from campushallen.database.models import AuditEvent, Invitation, User, utcnow
from campushallen.database.session import session_factory
from campushallen.services.invitations import (
    InvalidInvitation,
    consume_invite,
    create_invite,
    digest,
    validate_invite,
)
from campushallen.services.user_security import set_admin


def new_invite(app):
    with app.state.sessions() as db:
        invite, token = create_invite(db, label="Friend")
        db.commit()
        return invite.id, token


def test_tokens_are_high_entropy_hashed_and_registration_is_single_use(web):
    client, app = web
    invite_id, token = new_invite(app)
    path = f"/invite/{token}"
    with app.state.sessions() as db:
        row = db.get(Invitation, invite_id)
        assert row.token_hash == digest(token) and token not in row.token_hash
    assert client.get("/register").status_code == 404
    response = client.get(path)
    assert response.status_code == 200
    assert token not in response.text
    data = {
        "username": "friend",
        "password": "a-long-password-manager-passphrase",
        "confirmation": "a-long-password-manager-passphrase",
    }
    assert post(client, path, data, path).status_code == 303
    assert client.get(path).status_code == 400
    # Reuse is rejected even with a valid CSRF token from the login page.
    assert post(client, path, {**data, "username": "another"}, "/login").status_code == 400
    with app.state.sessions() as db:
        row = db.get(Invitation, invite_id)
        friend = db.scalar(select(User).where(User.username == "friend"))
        assert row.used_at and row.used_by_user_id == friend.id and not friend.is_admin
        assert friend.password_hash.startswith("$argon2id$")
        assert {"INVITE_CREATED", "INVITE_USED", "USER_REGISTERED"} <= set(
            db.scalars(select(AuditEvent.code))
        )
        # Deletion never makes the invitation usable again.
        db.delete(friend)
        db.commit()
        assert db.get(Invitation, invite_id).used_at
        with pytest.raises(InvalidInvitation):
            validate_invite(db, token)


@pytest.mark.parametrize("state", ["invalid", "expired", "revoked", "used"])
def test_unavailable_invites_give_same_generic_message(web, state):
    client, app = web
    invite_id, token = new_invite(app)
    with app.state.sessions() as db:
        row = db.get(Invitation, invite_id)
        if state == "expired":
            row.expires_at = utcnow() - timedelta(seconds=1)
        elif state == "revoked":
            row.revoked_at = utcnow()
        elif state == "used":
            row.used_at = utcnow()
        else:
            token = "A" * 43
        db.commit()
    response = client.get(f"/invite/{token}")
    assert response.status_code == 400
    assert "inbjudningslänken är inte längre giltig" in response.text
    assert 'name="username"' not in response.text


def test_invite_registration_csrf_validation_and_failure_does_not_consume(web):
    client, app = web
    invite_id, token = new_invite(app)
    path = f"/invite/{token}"
    assert client.post(path, data={"username": "x"}).status_code == 403
    assert (
        post(
            client, path, {"username": "new", "password": "short", "confirmation": "short"}, path
        ).status_code
        == 422
    )
    assert (
        post(
            client,
            path,
            {"username": "new", "password": "password-long-enough", "confirmation": "mismatch"},
            path,
        ).status_code
        == 422
    )
    assert (
        post(
            client,
            path,
            {
                "username": "alice",
                "password": "password-long-enough",
                "confirmation": "password-long-enough",
            },
            path,
        ).status_code
        == 422
    )
    with app.state.sessions() as db:
        assert db.get(Invitation, invite_id).used_at is None


def test_invitation_concurrent_consumption_is_atomic(database):
    sessions = session_factory(database)
    with sessions() as db:
        row, token = create_invite(db)
        db.commit()

    def consume(name):
        with sessions() as db:
            try:
                return consume_invite(db, token, name, "concurrent-long-password").username
            except InvalidInvitation:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(consume, ["first-friend", "second-friend"]))
    assert sum(value is not None for value in results) == 1
    with sessions() as db:
        assert len(list(db.scalars(select(User)))) == 1


def test_normal_users_cannot_manage_invitations_and_admins_cannot_see_credentials(web):
    client, app = web
    login(client)
    assert client.get("/admin").status_code == 403
    assert post(client, "/admin/invites", {"count": "1"}).status_code == 403
    assert post(client, "/admin/invites/1/revoke").status_code == 403
    with app.state.sessions() as db:
        set_admin(db, db.get(User, app.state.alice_id), enabled=True)
    assert client.get("/admin").status_code == 200
    assert client.get(f"/accounts/{app.state.bob_account}").status_code == 404
    response = post(
        client, "/admin/invites", {"count": "2", "label": "Friends", "expires_days": "7"}
    )
    assert response.status_code == 200 and "Spara länkarna" in response.text
    import re

    links = re.findall(r"http://127\.0\.0\.1:8000/invite/([A-Za-z0-9_-]{43})", response.text)
    assert len(links) == 2
    listing = client.get("/admin")
    assert all(token not in listing.text for token in links)
    with app.state.sessions() as db:
        row = db.scalar(select(Invitation).order_by(Invitation.id.desc()))
        assert row.created_by == app.state.alice_id and row.token_hash not in listing.text
        invite_id = row.id
    assert post(client, f"/admin/invites/{invite_id}/revoke").status_code == 303
    assert client.get(f"/invite/{links[-1]}").status_code == 400


def test_invite_and_registration_rate_limits(web):
    client, app = web
    _, token = new_invite(app)
    app.state.limiter.limit = 1
    path = f"/invite/{token}"
    first = client.get(path)
    assert first.status_code == 200
    form_token = client.cookies.get("csrf")
    assert client.get(path).status_code == 429
    assert client.post(path, data={"csrf_token": form_token}).status_code == 422
    assert client.post(path, data={"csrf_token": form_token}).status_code == 429


def test_cli_batch_invites_list_revoke_and_admin_bootstrap(database, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", str(database.url))
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(
        "sys.argv", ["campushallen-admin", "invite", "create", "--count", "5", "--label", "Friends"]
    )
    main()
    output = capsys.readouterr().out
    tokens = [line.rsplit("/", 1)[-1] for line in output.splitlines() if line.startswith("http")]
    assert len(tokens) == 5 and len(set(tokens)) == 5
    assert "cannot be displayed again" in output
    monkeypatch.setattr("sys.argv", ["campushallen-admin", "invite", "list"])
    main()
    listing = capsys.readouterr().out
    assert all(token not in listing for token in tokens)
    with session_factory(database)() as db:
        assert len(list(db.scalars(select(Invitation)))) == 5
        invite_id = db.scalar(select(Invitation.id))
    monkeypatch.setattr("sys.argv", ["campushallen-admin", "invite", "revoke", str(invite_id)])
    main()
    capsys.readouterr()
    monkeypatch.setattr("getpass.getpass", lambda prompt: "admin-long-password")
    monkeypatch.setattr("sys.argv", ["campushallen-admin", "create-user", "operator", "--admin"])
    main()
    with session_factory(database)() as db:
        user = db.scalar(select(User).where(User.username == "operator"))
        assert user.is_admin
        with pytest.raises(ValueError, match="At least one"):
            set_admin(db, user, enabled=False)
