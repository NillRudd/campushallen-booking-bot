"""Account-specific API clients never load the legacy global auth files."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterator
from zoneinfo import ZoneInfo

import httpx

from campushallen.api import CampushallenHttpClient
from campushallen.config import AppConfig, AuthConfig, BookingConfig, LoggingConfig, ScheduleConfig
from campushallen.database.models import CampushallenAccount
from campushallen.settings import Settings

from .provider_reads import ProviderReads, authentication_rejected
from .secrets import ReconnectRequired, TokenVault

if TYPE_CHECKING:
    from .account_auth import AccountAuthentication


def api_config(settings: Settings) -> AppConfig:
    return AppConfig(
        timezone=ZoneInfo(settings.campushallen_timezone),
        base_url="https://campushallen.goactivebooking.com/",
        auth=AuthConfig(),
        booking=BookingConfig(),
        schedule=ScheduleConfig(),
        bookings=[],
        logging=LoggingConfig(),
        path=Path("config.yaml"),
    )


class AccountGateway:
    def __init__(self, settings: Settings, vault: TokenVault):
        self.settings = settings
        self.vault = vault
        self.auth_service: AccountAuthentication | None = None

    def connect(self, account: CampushallenAccount, password: str) -> None:
        client = CampushallenHttpClient(api_config(self.settings), load_saved_auth=False)
        client.read_policy = ProviderReads(account.id)
        endpoint = "login"
        try:
            data = client.login(account.username, password)
            if not data.get("access_token"):
                from .account_auth import TemporaryAuthenticationError

                raise TemporaryAuthenticationError("Login returned an invalid response.")
            try:
                customer_id = int(data["username"])
            except (AttributeError, ValueError, TypeError, KeyError):
                from .account_auth import TemporaryAuthenticationError

                raise TemporaryAuthenticationError(
                    "Login returned no customer identifier."
                ) from None
            # Verify the identity before keeping any state. Never store password.
            endpoint = "customer"
            client.get_customer(customer_id)
            state = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token"),
                "customer_id": customer_id,
            }
            if data.get("expires_in"):
                state["expires_at"] = datetime.now(timezone.utc).timestamp() + int(
                    data["expires_in"]
                )
            account.auth_state = self.vault.seal(state)
            account.auth_status = "connected"
            account.requires_reconnect = False
            account.last_auth_success_at = datetime.now(timezone.utc)
            account.auth_retry_at = None
            account.auth_lease_until = None
            account.auth_failure_count = 0
            account.auth_error_code = None
        except httpx.HTTPStatusError as exc:
            from .account_auth import TemporaryAuthenticationError

            if endpoint != "login" or not authentication_rejected(exc.response):
                raise TemporaryAuthenticationError(
                    "Campushallen is temporarily unavailable.",
                    http_status=exc.response.status_code,
                    endpoint_category=endpoint,
                ) from None
            raise ReconnectRequired(
                "Campushallen login failed. Reconnect to update credentials.",
                http_status=exc.response.status_code,
                endpoint_category=endpoint,
            ) from None
        except httpx.HTTPError:
            from .account_auth import TemporaryAuthenticationError

            raise TemporaryAuthenticationError("Campushallen is temporarily unavailable.") from None
        except (AttributeError, ValueError, TypeError, KeyError):
            from .account_auth import TemporaryAuthenticationError

            raise TemporaryAuthenticationError(
                "Campushallen returned an invalid login response."
            ) from None
        finally:
            client.close()

    @contextmanager
    def client(self, account: CampushallenAccount) -> Iterator[tuple[CampushallenHttpClient, int]]:
        if self.auth_service:
            client = CampushallenHttpClient(api_config(self.settings), load_saved_auth=False)
            reads = ProviderReads(account.id)
            client.read_policy = reads
            try:
                state = self.auth_service.ensure(account.id, client)
                client.client.headers["Authorization"] = f"Bearer {state['access_token']}"
                customer_id = int(state["customer_id"])

                def recover() -> None:
                    refreshed = self.reauthenticate(account.id, client)
                    if refreshed != customer_id:
                        raise ReconnectRequired(
                            "Provider identity changed; reconnect this account."
                        )

                reads.reauthenticate = recover
                yield client, int(state["customer_id"])
            finally:
                client.close()
            return
        state = self.vault.open(account.auth_state)
        if (
            state.get("expires_at")
            and float(state["expires_at"]) <= datetime.now(timezone.utc).timestamp()
        ):
            raise ReconnectRequired("Campushallen authentication expired; reconnect this account.")
        client = CampushallenHttpClient(api_config(self.settings), load_saved_auth=False)
        try:
            client.client.headers["Authorization"] = f"Bearer {state['access_token']}"
            yield client, int(state["customer_id"])
        finally:
            client.close()

    def reauthenticate(self, account_id: int, client: CampushallenHttpClient) -> int:
        if self.auth_service is None:
            raise ReconnectRequired("Automatic reconnection is unavailable.")
        state = self.auth_service.ensure(account_id, client, force=True)
        client.client.headers["Authorization"] = f"Bearer {state['access_token']}"
        return int(state["customer_id"])
