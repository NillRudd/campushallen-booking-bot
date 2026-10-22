"""Bounded account reauthentication with a durable lease, latch, and backoff.

Read authentication may recover once anywhere within an operation. This module
does not replay mutations; the booking engine owns its explicit limit-only policy.
"""

import logging
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
from .provider_reads import authentication_rejected
from .secrets import CredentialVault, ReconnectRequired, TokenVault

LOGGER = logging.getLogger(__name__)


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

    def ensure(
        self, account_id: int, client: CampushallenHttpClient, *, force: bool = False
    ) -> dict[str, Any]:
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
                # Another operation may already have persisted a replacement token.
                # Validate that newer cache before spending this operation's login.
                if (
                    not force
                    or client.client.headers.get("Authorization")
                    != f"Bearer {state['access_token']}"
                ):
                    client.client.headers["Authorization"] = f"Bearer {state['access_token']}"
                    # Avoid recursive read-auth recovery while validating the token itself.
                    policy = getattr(client, "read_policy", None)
                    callback = policy.reauthenticate if policy is not None else None
                    if policy is not None:
                        policy.reauthenticate = None
                    try:
                        client.get_customer(int(state["customer_id"]))
                    except (ValueError, TypeError, KeyError):
                        self._network_failure(db, account)
                        raise TemporaryAuthenticationError(
                            "Campushallen returned an invalid identity response."
                        ) from None
                    finally:
                        if policy is not None:
                            policy.reauthenticate = callback
                    LOGGER.info(
                        "Cached token validated",
                        extra={"account_id": account_id, "phase": "cached_token_used"},
                    )
                    if account.auth_error_code == "TEMPORARY_AUTH_FAILURE":
                        # Clear outage telemetry only if this exact cache and account
                        # are still current; never undo disconnect/disable or a lease.
                        db.execute(
                            update(CampushallenAccount)
                            .where(
                                CampushallenAccount.id == account_id,
                                CampushallenAccount.auth_state == account.auth_state,
                                CampushallenAccount.enabled.is_(True),
                                CampushallenAccount.requires_reconnect.is_(False),
                                CampushallenAccount.auth_error_code == "TEMPORARY_AUTH_FAILURE",
                            )
                            .values(
                                auth_status="connected",
                                auth_error_code=None,
                                auth_failure_count=0,
                                auth_retry_at=None,
                                last_auth_success_at=utcnow(),
                            )
                        )
                        db.commit()
                    return state
            except TemporaryAuthenticationError:
                raise
            except httpx.HTTPStatusError as exc:
                if not authentication_rejected(exc.response):
                    self._network_failure(db, account)
                    raise ReconnectRequired(
                        "Campushallen is temporarily unavailable.",
                        http_status=exc.response.status_code,
                        endpoint_category="customer",
                    ) from None
            except httpx.HTTPError:
                self._network_failure(db, account)
                raise ReconnectRequired("Campushallen is temporarily unavailable.") from None
            except (ReconnectRequired, ValueError, KeyError, TypeError):
                pass
            # A client represents one operation. Its login budget includes preflight
            # refreshes and recovery after the mutation; never try a password twice.
            if getattr(client, "_operation_reauth_used", False):
                raise ReconnectRequired(
                    "Automatic reconnection already attempted for this operation."
                )
            setattr(client, "_operation_reauth_used", True)
            LOGGER.info(
                "Cached token rejected or expired",
                extra={"account_id": account_id, "phase": "token_rejected"},
            )
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
            LOGGER.info(
                "Password reauthentication started",
                extra={"account_id": account_id, "phase": "reauth_started"},
            )
            password = self.credentials.open(account_id, ciphertext, version)
            self.gateway.connect(account, password)
            del password
        except ReconnectRequired as exc:
            self._failed(
                account_id, lease_until, permanent=not isinstance(exc, TemporaryAuthenticationError)
            )
            raise ReconnectRequired(
                "Automatic reconnection failed. Check account status and reconnect if needed.",
                http_status=exc.http_status,
                endpoint_category=exc.endpoint_category,
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
        LOGGER.info(
            "Password reauthentication succeeded",
            extra={"account_id": account_id, "phase": "reauth_succeeded"},
        )
        # Apply the persisted token to this operation too, before its original GET resumes.
        refreshed = self.tokens.open(account.auth_state)
        client.client.headers["Authorization"] = f"Bearer {refreshed['access_token']}"
        return refreshed

    def _network_failure(self, db, account) -> None:
        account.auth_failure_count += 1
        account.auth_status = "authentication_error"
        account.auth_error_code = "TEMPORARY_AUTH_FAILURE"
        account.last_auth_failure_at = utcnow()
        # Temporary outages never imply a known-invalid saved password.
        account.requires_reconnect = False
        account.auth_retry_at = utcnow() + timedelta(
            seconds=min(5 * 2 ** min(account.auth_failure_count - 1, 4), 60)
        )
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
