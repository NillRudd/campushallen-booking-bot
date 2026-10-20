"""Controlled operator grants and explicit administrator approval."""

from sqlalchemy import func, select

from campushallen.database.models import BookingClaim, CampushallenAccount, User

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
    used = (
        db.scalar(
            select(func.count())
            .select_from(BookingClaim)
            .where(BookingClaim.campushallen_account_id == account.id)
        )
        or 0
    )
    return {
        "account_id": account.id,
        "user_id": account.user_id,
        "live_booking_enabled": account.live_booking_enabled,
        "max_claims": account.live_booking_claim_limit,
        "used_claims": used,
        "activity_id": account.live_booking_activity_id,
    }
