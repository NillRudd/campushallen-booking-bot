"""Website-password, session and local deletion operations; no Campushallen calls."""

from typing import cast

from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from campushallen.database.models import AuditEvent, Invitation, User, WebSession

from .audit import Event, audit
from .authentication import PASSWORDS, validate_password


def check_password(user: User, password: str) -> None:
    if len(password) > 4096:
        raise ValueError("Website password is incorrect.")
    try:
        PASSWORDS.verify(user.password_hash, password)
    except (VerificationError, InvalidHashError):
        raise ValueError("Website password is incorrect.") from None


def logout_all(db: Session, user: User) -> None:
    db.execute(delete(WebSession).where(WebSession.user_id == user.id))
    audit(db, Event.SESSIONS_REVOKED, user_id=user.id, actor_id=user.id)
    db.commit()


def change_password(db: Session, user: User, current: str, new: str, confirmation: str) -> None:
    check_password(user, current)
    if new != confirmation:
        raise ValueError("The new website passwords do not match.")
    validate_password(new)
    new_hash = PASSWORDS.hash(new)
    old_hash = user.password_hash
    # Concurrent changes cannot authorize a stale old password.
    changed = db.execute(
        update(User)
        .where(User.id == user.id, User.password_hash == old_hash)
        .values(password_hash=new_hash)
    )
    if cast(CursorResult, changed).rowcount != 1:
        db.rollback()
        raise ValueError("Password changed in another session; log in again.")
    db.execute(delete(WebSession).where(WebSession.user_id == user.id))
    audit(db, Event.PASSWORD_CHANGED, user_id=user.id, actor_id=user.id)
    db.commit()


def active_admin_count():
    return (
        select(func.count(User.id))
        .where(User.is_admin.is_(True), User.is_active.is_(True))
        .scalar_subquery()
    )


def set_admin(db: Session, user: User, *, enabled: bool) -> None:
    conditions = [User.id == user.id]
    if not enabled:
        conditions.append(
            or_(User.is_admin.is_(False), User.is_active.is_(False), active_admin_count() > 1)
        )
    result = db.execute(update(User).where(*conditions).values(is_admin=enabled))
    if cast(CursorResult, result).rowcount != 1:
        db.rollback()
        raise ValueError("At least one active admin must remain.")
    audit(db, Event.ADMIN_PROMOTED if enabled else Event.ADMIN_DEMOTED, user_id=user.id)
    db.commit()


def delete_user(db: Session, user: User, password: str, confirmation: str) -> None:
    check_password(user, password)
    if confirmation != "DELETE":
        raise ValueError("Type DELETE to confirm local data removal.")
    # Acquire the write boundary with the admin invariant; works across processes.
    allowed = db.execute(
        update(User)
        .where(
            User.id == user.id,
            User.password_hash == user.password_hash,
            or_(User.is_admin.is_(False), active_admin_count() > 1),
        )
        .values(is_active=False)
    )
    if cast(CursorResult, allowed).rowcount != 1:
        db.rollback()
        raise ValueError(
            "The last active admin cannot be deleted. Create or promote another admin first."
        )
    db.execute(
        delete(AuditEvent).where(
            or_(AuditEvent.user_id == user.id, AuditEvent.actor_user_id == user.id)
        )
    )
    # Keep the consumed tombstone, but remove its potentially identifying operator label.
    db.execute(update(Invitation).where(Invitation.used_by_user_id == user.id).values(label=""))
    db.delete(user)
    audit(db, Event.USER_DELETED)
    db.commit()
