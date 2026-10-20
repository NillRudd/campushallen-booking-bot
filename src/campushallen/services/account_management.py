"""Local account and credential actions; only explicit connect authenticates remotely."""

from datetime import timedelta

from sqlalchemy.orm import Session

from campushallen.database.models import CampushallenAccount, utcnow

from .account_auth import TemporaryAuthenticationError
from .accounts import AccountGateway
from .audit import Event, audit
from .credentials import credential_for, forget, remember
from .secrets import CredentialVault, ReconnectRequired


def connect_account(
    db: Session,
    gateway: AccountGateway,
    vault: CredentialVault,
    account: CampushallenAccount,
    password: str,
    *,
    save_password: bool,
) -> None:
    previous = account.auth_state is not None
    try:
        gateway.connect(account, password)
    except ReconnectRequired as exc:
        account.last_auth_failure_at = utcnow()
        account.auth_failure_count += 1
        account.auth_lease_until = None
        if isinstance(exc, TemporaryAuthenticationError):
            account.auth_status = "authentication_error"
            account.auth_error_code = "TEMPORARY_AUTH_FAILURE"
            account.auth_retry_at = utcnow() + timedelta(seconds=60)
        else:
            account.auth_state = None
            account.auth_status = "reconnect_required"
            account.auth_error_code = "RECONNECT_REQUIRED"
            account.requires_reconnect = True
        audit(
            db,
            Event.RECONNECT_REQUIRED,
            user_id=account.user_id,
            actor_id=account.user_id,
            account_id=account.id,
        )
        db.commit()
        raise
    if save_password:
        remember(db, vault, account, password)
    elif credential_for(db, account.id):
        forget(db, account)
    audit(
        db,
        Event.CAMPUSHALLEN_RECONNECTED if previous else Event.CAMPUSHALLEN_CONNECTED,
        user_id=account.user_id,
        actor_id=account.user_id,
        account_id=account.id,
    )
    db.commit()


def disconnect(db: Session, account: CampushallenAccount) -> None:
    forget(db, account)
    # Approval belongs to the reviewed identity, never a replacement email/login.
    account.live_booking_enabled = False
    account.live_booking_claim_limit = 0
    account.live_booking_activity_id = None
    account.auth_state = None
    account.auth_lease_until = None
    account.auth_retry_at = None
    account.auth_status = "disconnected"
    account.requires_reconnect = True
    audit(
        db,
        Event.ACCOUNT_DISCONNECTED,
        user_id=account.user_id,
        actor_id=account.user_id,
        account_id=account.id,
    )
    db.commit()
