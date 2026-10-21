"""Controlled operator grants and explicit administrator approval."""

from datetime import timedelta

from sqlalchemy import func, select

from campushallen.database.models import (
    BookingClaim,
    BookingReconciliation,
    CampushallenAccount,
    User,
    utcnow,
)

from .audit import Event, audit


def configure_live(
    db, account_id: int, username: str, *, enabled=None, limit=None, activity_id=None
) -> dict:
    account = db.scalar(
        select(CampushallenAccount)
        .join(User)
        .where(
            CampushallenAccount.id == account_id,
            User.username == username.strip().casefold(),
            User.is_active.is_(True),
        )
    )
    if account is None:
        raise ValueError("Connection does not belong to the expected active user")
    if enabled is True:
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("Enabling live eligibility requires a positive --max-claims")
        if not isinstance(activity_id, int) or activity_id < 1:
            raise ValueError("Enabling live eligibility requires the reviewed --activity-id")
        if not account.enabled or account.requires_reconnect or not account.auth_state:
            raise ValueError("Connect and enable the account before enabling live eligibility")
        account.live_booking_ongoing = False
        account.live_booking_enabled = True
        account.live_booking_claim_limit = limit
        account.live_booking_activity_id = activity_id
    elif enabled is False:
        account.live_booking_enabled = False
    if enabled is not None:
        audit(
            db,
            Event.LIVE_BOOKING_ENABLED if enabled else Event.LIVE_BOOKING_DISABLED,
            user_id=account.user_id,
            account_id=account.id,
        )
        db.commit()
    used = used_claims(db, account.id)
    return {
        "account_id": account.id,
        "user_id": account.user_id,
        "live_booking_enabled": account.live_booking_enabled,
        "max_claims": account.live_booking_claim_limit,
        "used_claims": used,
        "activity_id": account.live_booking_activity_id,
    }


def used_claims(db, account_id: int) -> int:
    ordinary = (
        db.scalar(
            select(func.count())
            .select_from(BookingClaim)
            .where(BookingClaim.campushallen_account_id == account_id)
        )
        or 0
    )
    reconciled = (
        db.scalar(
            select(func.count())
            .select_from(BookingReconciliation)
            .where(
                BookingReconciliation.campushallen_account_id == account_id,
                BookingReconciliation.consumed_at.is_not(None),
            )
        )
        or 0
    )
    return ordinary + reconciled


def approve_reconciliation(
    db,
    account_id: int,
    username: str,
    activity_id: int,
    operator: str,
    *,
    confirm_remote_absent: bool,
) -> int:
    """Trusted local operator only; no automatic or website approval path."""
    status = configure_live(db, account_id, username)
    account = db.get(CampushallenAccount, account_id)
    actor = db.scalar(
        select(User).where(
            User.username == operator.strip().casefold(),
            User.is_admin.is_(True),
            User.is_active.is_(True),
        )
    )
    claim = db.get(BookingClaim, (account_id, activity_id))
    if (
        not confirm_remote_absent
        or not actor
        or not claim
        or claim.created_at > utcnow() - timedelta(minutes=5)
        or not account.enabled
        or account.requires_reconnect
        or not account.auth_state
        or not status["live_booking_enabled"]
        or account.live_booking_ongoing
        or status["activity_id"] != activity_id
        or status["used_claims"] >= status["max_claims"]
    ):
        raise ValueError(
            "Explicit remote reconciliation, active admin, exact finite live scope and remaining allowance required"
        )
    if db.scalar(
        select(BookingReconciliation.id).where(
            BookingReconciliation.campushallen_account_id == account_id,
            BookingReconciliation.activity_id == activity_id,
        )
    ):
        raise ValueError("An approval already exists; it cannot be reset or issued again")
    approval = BookingReconciliation(
        campushallen_account_id=account_id,
        activity_id=activity_id,
        approved_by_user_id=actor.id,
        original_claim_created_at=claim.created_at,
        expires_at=utcnow() + timedelta(minutes=10),
    )
    db.add(approval)
    audit(
        db,
        Event.RECONCILIATION_APPROVED,
        actor_id=actor.id,
        user_id=account.user_id,
        account_id=account_id,
    )
    db.commit()
    return approval.id


def activate_connected(db, account, tokens, *, actor_id: int) -> None:
    """Ongoing live is the connected-account default; no discovery/POST here.

    Explicit connect, resume or admin activation calls this. Background token
    refresh never calls it, so a stop is not undone by automatic reauthentication.
    A paused account stays paused; users retain per-rule and account controls.
    """
    from .identities import identity_for, reserve_identity

    if not account.enabled or account.requires_reconnect or not account.auth_state:
        raise ValueError("Connect and enable the account before activation.")
    expected = identity_for(account, tokens)
    db.flush()
    reserve_identity(db, account, tokens, actor_id, expected_digest=expected)
    owner = db.get(User, account.user_id)
    if (
        not account.enabled
        or account.requires_reconnect
        or not account.auth_state
        or not owner
        or not owner.is_active
    ):
        raise ValueError("Connect and enable the account before activation.")
    changed = not account.live_booking_enabled or not account.live_booking_ongoing
    account.live_booking_enabled = True
    account.live_booking_ongoing = True
    # Historical finite scope/quota and all durable claims are retained. Ongoing
    # explicitly bypasses their scope/limit; existing claims still prevent replay.
    if changed:
        audit(
            db,
            Event.LIVE_BOOKING_ENABLED,
            actor_id=actor_id,
            user_id=account.user_id,
            account_id=account.id,
        )
