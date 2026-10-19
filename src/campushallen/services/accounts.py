"""Account-specific API clients never load the legacy global auth files."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import httpx

from campushallen.api import CampushallenHttpClient
from campushallen.config import AppConfig, AuthConfig, BookingConfig, LoggingConfig, ScheduleConfig
from campushallen.database.models import CampushallenAccount
from campushallen.settings import Settings

from .secrets import ReconnectRequired, TokenVault


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

    def connect(self, account: CampushallenAccount, password: str) -> None:
        client = CampushallenHttpClient(api_config(self.settings), load_saved_auth=False)
        try:
            data = client.login(account.username, password)
            if not data.get("access_token"):
                raise ReconnectRequired("Authentication failed; reconnect this account.")
            try:
                customer_id = int(data["username"])
            except (ValueError, TypeError, KeyError):
                raise ReconnectRequired("Login returned no customer identifier.") from None
            # Verify the identity before keeping any state. Never store password.
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
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise ReconnectRequired(
                "Campushallen login failed. Check your credentials and try again."
            ) from None
        finally:
            client.close()

    @contextmanager
    def client(self, account: CampushallenAccount) -> Iterator[tuple[CampushallenHttpClient, int]]:
        state: dict[str, Any] = self.vault.open(account.auth_state)
        if (
            state.get("expires_at")
            and float(state["expires_at"]) <= datetime.now(timezone.utc).timestamp()
        ):
            raise ReconnectRequired("Campushallen authentication expired; reconnect this account.")
        client = CampushallenHttpClient(api_config(self.settings), load_saved_auth=False)
        try:
            client.client.headers["Authorization"] = f"Bearer {state['access_token']}"
            yield client, int(state["customer_id"])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise ReconnectRequired(
                    "Campushallen authentication expired; reconnect this account."
                ) from None
            raise
        finally:
            client.close()
