"""Exclusive live ownership keyed by verified provider customer identity.

All mutations take SQLite's writer lock first. Other installations cannot be
inventoried remotely: the admin explicitly attests that inventory at activation.
"""

from sqlalchemy import select, update

from campushallen.database.models import BookingIdentity, CampushallenAccount, User, utcnow

from .secrets import ReconnectRequired


def identity_for(account, tokens) -> str:
    return tokens.identity_digest(int(tokens.open(account.auth_state)["customer_id"]))


def identity_status(db, account, tokens) -> str | None:
    try:
        digest = identity_for(account, tokens)
    except (ReconnectRequired, ValueError, KeyError, TypeError):
        return "Reconnect before identity review."
    row = db.get(BookingIdentity, digest)
    if row and row.external_active:
        return "Legacy booking system active — new live booking not enabled."
    if row and row.account_id not in (None, account.id):
        other = db.get(CampushallenAccount, row.account_id)
        if other and other.live_booking_enabled:
            return "This Campushallen identity already has another live connection."
    # Also detect live flags left by a previous release/operator before the
    # registry existed. A display name or login email is never the identity key.
    for other in db.scalars(
        select(CampushallenAccount).where(
            CampushallenAccount.id != account.id,
            CampushallenAccount.live_booking_enabled.is_(True),
        )
    ):
        try:
            if identity_for(other, tokens) == digest:
                return "This Campushallen identity already has another live connection."
        except (ReconnectRequired, ValueError, KeyError, TypeError):
            continue  # Its unreviewed identity cannot pass the worker claim guard.
    return None


def reserve_identity(db, account, tokens, actor_id: int, *, expected_digest: str) -> None:
    # Called in the same write transaction as enabling live. Do not commit here.
    db.execute(
        update(CampushallenAccount)
        .where(CampushallenAccount.id == account.id)
        .values(live_booking_enabled=CampushallenAccount.live_booking_enabled)
    )
    db.expire_all()
    db.refresh(account)
    if identity_for(account, tokens) != expected_digest:
        raise ValueError("Campushallen identity changed during review; review again.")
    actor = db.get(User, actor_id)
    if not actor or not actor.is_active or (not actor.is_admin and actor.id != account.user_id):
        raise ValueError("Only the account owner or an active admin can enable automatic booking.")
    message = identity_status(db, account, tokens)
    if message:
        raise ValueError(message)
    digest = identity_for(account, tokens)
    # A changed identity does not inherit its earlier reservation.
    previous = db.scalar(select(BookingIdentity).where(BookingIdentity.account_id == account.id))
    if previous and previous.digest != digest:
        previous.account_id = None
        db.flush()
    row = db.get(BookingIdentity, digest)
    if row is None:
        row = BookingIdentity(digest=digest)
        db.add(row)
    row.account_id, row.reviewed_at, row.reviewed_by = account.id, utcnow(), actor_id
    db.flush()


def claim_identity_allowed(db, account_id: int, digest: str | None) -> bool:
    if not digest:
        return False
    row = db.get(BookingIdentity, digest)
    return bool(row and not row.external_active and row.account_id == account_id)


def register_external(db, tokens, customer_id: int, label: str, *, active: bool) -> None:
    """Trusted local inventory, never changes legacy config or credentials."""
    if label not in ("niklas_legacy", "friend_legacy", "external_legacy"):
        raise ValueError("Choose an allowlisted legacy mechanism label")
    digest = tokens.identity_digest(customer_id)
    # Take the same writer lock even if the registry is initially empty.
    db.execute(update(CampushallenAccount).values(enabled=CampushallenAccount.enabled))
    row = db.get(BookingIdentity, digest)
    if row and row.account_id and active:
        account = db.get(CampushallenAccount, row.account_id)
        if account and account.live_booking_enabled:
            raise ValueError("Stop new-worker live approval before marking legacy active")
    if row is None:
        row = BookingIdentity(digest=digest)
        db.add(row)
    row.external_label, row.external_active, row.reviewed_at = label, active, utcnow()
    db.flush()
