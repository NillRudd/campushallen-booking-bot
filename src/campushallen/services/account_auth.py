"""Bounded account reauthentication with a durable lease, latch, and backoff.

Only the initial token validation GET may be retried after login. Booking requests
are never replayed, even if an authentication error happens during the operation.
"""

from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.orm import sessionmaker

from campushallen.api import CampushallenHttpClient
from campushallen.database.models import CampushallenAccount, CampushallenCredential, User, utcnow

from .audit import Event, audit
from .credentials import credential_for
from .notifications import (
    NoopNotifier,
    Notification,
    NotificationKind,
    NotificationService,
    deliver,
)
from .secrets import CredentialVault, ReconnectRequired, TokenVault


class AccountAuthentication:
    def __init__(
        self,
        sessions: sessionmaker,
        gateway: Any,
        tokens: TokenVault,
        credentials: CredentialVault,
        notifier: NotificationService | None = None,
    ):
        self.sessions, self.gateway, self.tokens, self.credentials = (
            sessions,
            gateway,
            tokens,
            credentials,
        )
        self.notifier = notifier or NoopNotifier()

    def ensure(self, account_id: int, client: CampushallenHttpClient) -> dict[str, Any]:
        now = utcnow()
        with self.sessions() as db:
            account = db.get(CampushallenAccount, account_id)
            if not account or not account.enabled or not db.get(User, account.user_id).is_active:
                raise ReconnectRequired("Account is disabled or unavailable.")
            if account.requires_reconnect:
                raise ReconnectRequired("Reconnect this account or replace its saved password.")
            if account.auth_retry_at and account.auth_retry_at > now:
                raise ReconnectRequired(
                    "Authentication is temporarily paused; retry after the displayed time."
                )
            try:
                state = self.tokens.open(account.auth_state)
                expired = bool(
                    state.get("expires_at") and float(state["expires_at"]) <= now.timestamp()
                )
                if not expired:
                    client.client.headers["Authorization"] = f"Bearer {state['access_token']}"
                    client.get_customer(int(state["customer_id"]))
                    return state
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (401, 403):
                    self._network_failure(db, account)
                    raise ReconnectRequired("Campushallen is temporarily unavailable.") from None
            except httpx.HTTPError:
                self._network_failure(db, account)
                raise ReconnectRequired("Campushallen is temporarily unavailable.") from None
            except (ReconnectRequired, ValueError, KeyError, TypeError):
                pass
            credential = credential_for(db, account_id)
            if not credential:
                account.requires_reconnect = True
                account.auth_status = "reconnect_required"
                account.auth_error_code = "RECONNECT_REQUIRED"
                account.last_auth_failure_at = now
                audit(db, Event.RECONNECT_REQUIRED, user_id=account.user_id, account_id=account_id)
                db.commit()
                deliver(
                    self.notifier,
                    Notification(NotificationKind.REAUTH_REQUIRED, account.user_id, account_id),
                )
                raise ReconnectRequired("Token expired or unavailable; reconnect this account.")
            ciphertext, version = credential.encrypted_password, credential.encryption_version
            original_state, owner = account.auth_state, account.user_id
            lease_until = now + timedelta(seconds=90)
            # Compare the old ciphertext too: another process may already have refreshed it.
            claimed = db.execute(
                update(CampushallenAccount)
                .where(
                    CampushallenAccount.id == account_id,
                    CampushallenAccount.auth_state == original_state,
                    CampushallenAccount.requires_reconnect.is_(False),
                    or_(
                        CampushallenAccount.auth_lease_until.is_(None),
                        CampushallenAccount.auth_lease_until <= now,
                    ),
                    or_(
                        CampushallenAccount.auth_retry_at.is_(None),
                        CampushallenAccount.auth_retry_at <= now,
                    ),
                )
                .values(
                    auth_lease_until=lease_until,
                    auth_error_code="TOKEN_EXPIRED",
                    auth_status="token_expired",
                )
            )
            if claimed.rowcount != 1:
                db.rollback()
                raise ReconnectRequired("Authentication state changed; try again shortly.")
            audit(db, Event.TOKEN_EXPIRED, user_id=owner, account_id=account_id)
            db.commit()
            # Never let the login helper autoflush changes to a concurrently disabled account.
            db.expunge(account)
        try:
            password = self.credentials.open(account_id, ciphertext, version)
            self.gateway.connect(account, password)
            del password
        except ReconnectRequired as exc:
            self._failed(
                account_id, lease_until, permanent=not isinstance(exc, TemporaryAuthenticationError)
            )
            raise ReconnectRequired(
                "Automatic reconnection failed. Check account status and reconnect if needed."
            ) from None
        with self.sessions() as db:
            credential_unchanged = (
                select(CampushallenCredential.id)
                .where(
                    CampushallenCredential.campushallen_account_id == account_id,
                    CampushallenCredential.encrypted_password == ciphertext,
                )
                .exists()
            )
            active_owner = (
                select(User.id).where(User.id == owner, User.is_active.is_(True)).exists()
            )
            changed = db.execute(
                update(CampushallenAccount)
                .where(
                    CampushallenAccount.id == account_id,
                    CampushallenAccount.auth_lease_until == lease_until,
                    CampushallenAccount.enabled.is_(True),
                    credential_unchanged,
                    active_owner,
                )
                .values(
                    auth_state=account.auth_state,
                    auth_status="connected",
                    requires_reconnect=False,
                    last_auth_success_at=utcnow(),
                    auth_failure_count=0,
                    auth_retry_at=None,
                    auth_lease_until=None,
                    auth_error_code="REAUTH_SUCCESS",
                )
            )
            if changed.rowcount != 1:
                db.rollback()
                raise ReconnectRequired("Account settings changed during reconnection; try again.")
            audit(db, Event.REAUTH_SUCCESS, user_id=owner, account_id=account_id)
            db.commit()
        return self.tokens.open(account.auth_state)

    def _network_failure(self, db, account) -> None:
        account.auth_failure_count += 1
        account.auth_status = "authentication_error"
        account.auth_error_code = "TEMPORARY_AUTH_FAILURE"
        account.last_auth_failure_at = utcnow()
        account.requires_reconnect = account.auth_failure_count >= 5
        account.auth_retry_at = (
            None
            if account.requires_reconnect
            else utcnow() + timedelta(seconds=min(60 * 2 ** (account.auth_failure_count - 1), 900))
        )
        if account.requires_reconnect:
            audit(db, Event.RECONNECT_REQUIRED, user_id=account.user_id, account_id=account.id)
        db.commit()

    def _failed(self, account_id, lease_until, *, permanent: bool) -> None:
        with self.sessions() as db:
            account = db.get(CampushallenAccount, account_id)
            if not account or account.auth_lease_until != lease_until:
                return
            account.auth_lease_until = None
            if permanent:
                account.auth_failure_count += 1
                account.requires_reconnect = True
                account.auth_status = "reauth_failed"
                account.auth_error_code = "REAUTH_FAILED"
                account.last_auth_failure_at = utcnow()
                account.auth_retry_at = None
            else:
                self._network_failure(db, account)
            audit(db, Event.REAUTH_FAILED, user_id=account.user_id, account_id=account.id)
            db.commit()
            if account.requires_reconnect:
                deliver(
                    self.notifier,
                    Notification(NotificationKind.REAUTH_REQUIRED, account.user_id, account.id),
                )


class TemporaryAuthenticationError(ReconnectRequired):
    pass
