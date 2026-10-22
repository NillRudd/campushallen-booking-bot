from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from campushallen.api import CampushallenHttpClient
from campushallen.database.models import (
    AuditEvent,
    CampushallenAccount,
    User,
    utcnow,
)
from campushallen.database.session import session_factory
from campushallen.services.account_auth import AccountAuthentication
from campushallen.services.account_management import connect_account
from campushallen.services.accounts import AccountGateway
from campushallen.services.credentials import credential_for, forget, remember
from campushallen.services.secrets import CredentialVault, ReconnectRequired, TokenVault
from campushallen.settings import Settings


@pytest.fixture
def authenticated_gateway(database, tmp_path, monkeypatch):
    sessions = session_factory(database)
    master = Fernet.generate_key().decode()
    tokens, credentials = TokenVault(master), CredentialVault(master)
    gateway = AccountGateway(Settings(state_dir=tmp_path, _env_file=None), tokens)
    gateway.auth_service = AccountAuthentication(sessions, gateway, tokens, credentials)
    server = SimpleNamespace(
        login_status=200,
        login_calls=0,
        reads=0,
        fail_validation=False,
        login_exception=None,
        login_body=None,
        identity_status=200,
    )

    def respond(request):
        if request.url.path.endswith("/auth/login"):
            server.login_calls += 1
            if server.login_exception:
                raise server.login_exception("private-network-error", request=request)
            return httpx.Response(
                server.login_status,
                json=server.login_body
                or {"username": "100", "access_token": "fresh-token", "expires_in": 3600},
            )
        assert request.method == "GET", "Only mocked authentication may issue POST in this fixture"
        server.reads += 1
        if server.identity_status != 200:
            return httpx.Response(server.identity_status, json={})
        if server.fail_validation or request.headers.get("Authorization") not in (
            "Bearer fresh-token",
            "Bearer valid-token",
        ):
            return httpx.Response(401, json={"message": "sensitive API response"})
        return httpx.Response(200, json={"id": 100})

    original = CampushallenHttpClient.__init__

    def init(self, config, **kwargs):
        original(self, config, **kwargs)
        self.client.close()
        self.client = httpx.Client(
            base_url="https://example.test", transport=httpx.MockTransport(respond)
        )

    monkeypatch.setattr(CampushallenHttpClient, "__init__", init)
    with sessions() as db:
        user = User(username="reauth-user", password_hash="unused")
        db.add(user)
        db.commit()
        account = CampushallenAccount(user_id=user.id, display_name="Account", username="external")
        db.add(account)
        db.commit()
        account_id = account.id
    return SimpleNamespace(
        gateway=gateway,
        tokens=tokens,
        credentials=credentials,
        sessions=sessions,
        account_id=account_id,
        server=server,
    )


def expire(b, *, save=True):
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        account.auth_state = b.tokens.seal(
            {
                "access_token": "old-token",
                "customer_id": 100,
                "expires_at": (utcnow() - timedelta(hours=1)).timestamp(),
            }
        )
        if save:
            remember(db, b.credentials, account, "private-recoverable-password")
        db.commit()


def use(b):
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
    with b.gateway.client(account) as (client, customer_id):
        assert customer_id == 100
        return client.client.headers["Authorization"]


def test_opt_in_encryption_replacement_removal_and_no_plaintext(authenticated_gateway, caplog):
    b = authenticated_gateway
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        connect_account(
            db, b.gateway, b.credentials, account, "used-once-only", save_password=False
        )
        assert credential_for(db, account.id) is None
        connect_account(
            db, b.gateway, b.credentials, account, "private-first-password", save_password=True
        )
        row = credential_for(db, account.id)
        assert b.credentials.open(account.id, row.encrypted_password) == "private-first-password"
        assert "private-first-password" not in row.encrypted_password
        connect_account(
            db, b.gateway, b.credentials, account, "private-replacement", save_password=True
        )
        row = credential_for(db, account.id)
        assert b.credentials.open(account.id, row.encrypted_password) == "private-replacement"
        from pathlib import Path

        database_path = Path(b.sessions.kw["bind"].url.database)
        for path in (database_path, Path(str(database_path) + "-wal")):
            if path.exists():
                for secret in (
                    b"used-once-only",
                    b"private-first-password",
                    b"private-replacement",
                ):
                    assert secret not in path.read_bytes()
        current_token = account.auth_state
        forget(db, account)
        db.commit()
        assert credential_for(db, account.id) is None and account.auth_state == current_token
    assert "private-first-password" not in caplog.text and "private-replacement" not in caplog.text
    with b.sessions() as db:
        codes = list(db.scalars(select(AuditEvent.code)))
        assert "STORED_CREDENTIAL_ENABLED" in codes and "STORED_CREDENTIAL_REPLACED" in codes
        assert "STORED_CREDENTIAL_REMOVED" in codes


def test_credential_key_purpose_account_binding_and_version():
    master = Fernet.generate_key().decode()
    vault = CredentialVault(master)
    sealed = vault.seal(1, "private-password")
    assert vault.open(1, sealed) == "private-password"
    for other, version in [(2, 1), (1, 99)]:
        with pytest.raises(ReconnectRequired):
            vault.open(other, sealed, version)
    with pytest.raises(ReconnectRequired):
        CredentialVault(Fernet.generate_key().decode()).open(1, sealed)
    with pytest.raises(ReconnectRequired):
        TokenVault(master).open(sealed)


def test_valid_token_never_uses_saved_password(authenticated_gateway):
    b = authenticated_gateway
    expire(b)
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        account.auth_state = b.tokens.seal(
            {
                "access_token": "valid-token",
                "customer_id": 100,
                "expires_at": (utcnow() + timedelta(hours=1)).timestamp(),
            }
        )
        db.commit()
    assert use(b) == "Bearer valid-token"
    assert b.server.login_calls == 0 and b.server.reads == 1


def test_expired_token_reauth_once_persists_state_and_continues(authenticated_gateway):
    b = authenticated_gateway
    expire(b)
    assert use(b) == "Bearer fresh-token"
    assert use(b) == "Bearer fresh-token"
    assert b.server.login_calls == 1
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        assert account.auth_error_code == "REAUTH_SUCCESS" and not account.requires_reconnect
        assert account.last_auth_success_at and account.auth_lease_until is None
        assert b.tokens.open(account.auth_state)["access_token"] == "fresh-token"
        assert {"TOKEN_EXPIRED", "REAUTH_SUCCESS"} <= set(db.scalars(select(AuditEvent.code)))


def test_server_rejection_of_unexpired_token_triggers_one_reauth(authenticated_gateway):
    b = authenticated_gateway
    expire(b)
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        account.auth_state = b.tokens.seal(
            {
                "access_token": "revoked-token",
                "customer_id": 100,
                "expires_at": (utcnow() + timedelta(hours=1)).timestamp(),
            }
        )
        db.commit()
    assert use(b) == "Bearer fresh-token"
    assert b.server.login_calls == 1


def test_no_saved_credential_requires_manual_reconnection(authenticated_gateway):
    b = authenticated_gateway
    expire(b, save=False)
    for _ in range(3):
        with pytest.raises(ReconnectRequired):
            use(b)
    assert b.server.login_calls == 0
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        assert account.requires_reconnect and account.auth_status == "reconnect_required"


def test_rejected_password_latches_and_never_enters_poll_loop(authenticated_gateway):
    b = authenticated_gateway
    expire(b)
    b.server.login_status = 401
    for _ in range(4):
        with pytest.raises(ReconnectRequired):
            use(b)
    assert b.server.login_calls == 1
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        assert account.requires_reconnect and account.auth_error_code == "REAUTH_FAILED"
        assert account.last_auth_failure_at and account.auth_failure_count == 1


def test_temporary_errors_back_off_without_invalid_password_latch(authenticated_gateway):
    b = authenticated_gateway
    expire(b)
    b.server.login_status = 503
    for attempt in range(5):
        with pytest.raises(ReconnectRequired):
            use(b)
        with b.sessions() as db:
            account = db.get(CampushallenAccount, b.account_id)
            assert account.auth_failure_count == attempt + 1
            if attempt < 4:
                assert account.auth_retry_at > utcnow()
                # A normal poll before that timestamp does not reauthenticate.
                with pytest.raises(ReconnectRequired):
                    use(b)
                assert b.server.login_calls == attempt + 1
                account.auth_retry_at = utcnow() - timedelta(seconds=1)
                db.commit()
            else:
                assert not account.requires_reconnect and account.auth_retry_at > utcnow()
    assert b.server.login_calls == 5
    with pytest.raises(ReconnectRequired):
        use(b)
    assert b.server.login_calls == 5


def test_wrong_master_key_fails_without_remote_login(authenticated_gateway):
    b = authenticated_gateway
    expire(b)
    b.gateway.auth_service.credentials = CredentialVault(Fernet.generate_key().decode())
    with pytest.raises(ReconnectRequired):
        use(b)
    assert b.server.login_calls == 0
    with b.sessions() as db:
        assert db.get(CampushallenAccount, b.account_id).requires_reconnect


def test_token_encryption_is_purpose_separated_and_legacy_tokens_remain_readable():
    import json

    master = Fernet.generate_key().decode()
    state = {"access_token": "legacy-private-token", "customer_id": 100}
    legacy = Fernet(master).encrypt(json.dumps(state).encode()).decode()
    vault = TokenVault(master)
    assert vault.open(legacy) == state
    current = vault.seal(state)
    assert current.startswith("v1:") and vault.open(current) == state
    with pytest.raises(ReconnectRequired):
        CredentialVault(master).open(1, current[3:])


def test_disable_during_reauth_does_not_resurrect_account(authenticated_gateway, monkeypatch):
    b = authenticated_gateway
    expire(b)
    original = b.gateway.connect

    def connect(account, password):
        original(account, password)
        with b.sessions() as db:
            row = db.get(CampushallenAccount, b.account_id)
            row.enabled = False
            row.auth_lease_until = None
            db.commit()

    monkeypatch.setattr(b.gateway, "connect", connect)
    with pytest.raises(ReconnectRequired):
        use(b)
    with b.sessions() as db:
        row = db.get(CampushallenAccount, b.account_id)
        assert not row.enabled
        assert b.tokens.open(row.auth_state)["access_token"] == "old-token"


def test_concurrent_reauthentication_uses_one_credential_attempt(
    authenticated_gateway, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    b = authenticated_gateway
    expire(b)
    started, release = Event(), Event()
    original = b.gateway.connect

    def connect(account, password):
        started.set()
        assert release.wait(timeout=5)
        original(account, password)

    monkeypatch.setattr(b.gateway, "connect", connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(use, b)
        assert started.wait(timeout=5)
        try:
            with pytest.raises(ReconnectRequired):
                use(b)
        finally:
            release.set()
        assert first.result(timeout=5) == "Bearer fresh-token"
    assert b.server.login_calls == 1
