import json
from datetime import timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from campushallen.api import CampushallenHttpClient
from campushallen.database.models import CampushallenAccount, utcnow
from campushallen.services.accounts import AccountGateway, api_config
from campushallen.services.secrets import ReconnectRequired, TokenVault, runtime_key
from campushallen.settings import Settings


def settings(tmp_path):
    return Settings(state_dir=tmp_path, _env_file=None)


def test_authentication_stores_encrypted_tokens_and_discards_password(tmp_path, monkeypatch):
    vault = TokenVault(Fernet.generate_key().decode())
    gateway = AccountGateway(settings(tmp_path), vault)
    account = CampushallenAccount(username="external", display_name="Example", user_id=1)
    seen = []

    def respond(request):
        seen.append(request)
        if request.url.path.endswith("auth/login"):
            assert json.loads(request.content) == {
                "username": "external",
                "password": "never-save-this",
            }
            return httpx.Response(
                200,
                json={
                    "username": "123",
                    "access_token": "private-token",
                    "refresh_token": "private-refresh",
                    "expires_in": 3600,
                },
            )
        assert request.headers["Authorization"] == "Bearer private-token"
        return httpx.Response(200, json={"id": 123})

    original = CampushallenHttpClient.__init__

    def init(self, config, **kwargs):
        assert kwargs == {"load_saved_auth": False}
        original(self, config, **kwargs)
        self.client.close()
        self.client = httpx.Client(
            base_url="https://example.test", transport=httpx.MockTransport(respond)
        )

    monkeypatch.setattr(CampushallenHttpClient, "__init__", init)
    gateway.connect(account, "never-save-this")
    assert account.auth_status == "connected"
    assert "private-token" not in account.auth_state
    assert "never-save-this" not in account.auth_state
    assert vault.open(account.auth_state)["customer_id"] == 123
    with gateway.client(account) as (client, customer_id):
        assert customer_id == 123
        assert client.client.headers["Authorization"] == "Bearer private-token"
    assert len(seen) == 2


def test_expired_tokens_and_wrong_keys_require_reconnection(tmp_path):
    vault = TokenVault(Fernet.generate_key().decode())
    gateway = AccountGateway(settings(tmp_path), vault)
    account = CampushallenAccount(
        auth_state=vault.seal(
            {
                "access_token": "token",
                "customer_id": 1,
                "expires_at": (utcnow() - timedelta(hours=1)).timestamp(),
            }
        )
    )
    with pytest.raises(ReconnectRequired):
        with gateway.client(account):
            pytest.fail("Expired token was accepted")
    with pytest.raises(ReconnectRequired):
        TokenVault(Fernet.generate_key().decode()).open(account.auth_state)


def test_account_clients_do_not_read_global_cli_tokens(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "auth-token.json").write_text('{"access_token":"legacy-private"}')
    (state / "storage-state.json").write_text(
        '{"cookies":[{"name":"secret", "value":"cookie", "domain":"example.test"}]}'
    )
    client = CampushallenHttpClient(api_config(settings(tmp_path)), load_saved_auth=False)
    try:
        assert "Authorization" not in client.client.headers
        assert not list(client.client.cookies)
    finally:
        client.close()
    legacy = CampushallenHttpClient(api_config(settings(tmp_path)))
    try:
        assert legacy.client.headers["Authorization"] == "Bearer legacy-private"
    finally:
        legacy.close()


def test_development_keys_are_private_and_persistent(tmp_path):
    config = settings(tmp_path)
    secret = runtime_key(config, "secret_key")
    assert runtime_key(config, "secret_key") == secret
    assert (tmp_path / "secret_key").stat().st_mode & 0o777 == 0o600
    assert len(secret) >= 32
    prod = Settings(app_env="production", state_dir=tmp_path, _env_file=None)
    with pytest.raises(ValueError):
        runtime_key(prod, "secret_key")
    with pytest.raises(ValueError):
        runtime_key(Settings(secret_key=SecretStr("short"), _env_file=None), "secret_key")
