"""One-time, hashed invitations; all consumption state changes share one transaction."""

import hashlib
import re
import secrets
from datetime import datetime
from typing import cast

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from campushallen.database.models import Invitation, User, utcnow

from .audit import Event, audit
from .authentication import prepare_user

INVALID_INVITE = "This invitation is unavailable. Ask the owner for a new invitation."


class InvalidInvitation(ValueError):
    pass


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def usable(token: str):
    return (
        Invitation.token_hash == digest(token),
        Invitation.used_at.is_(None),
        Invitation.revoked_at.is_(None),
        or_(Invitation.expires_at.is_(None), Invitation.expires_at > utcnow()),
    )


def validate_invite(db: Session, token: str) -> Invitation:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise InvalidInvitation(INVALID_INVITE)
    invite = db.scalar(select(Invitation).where(*usable(token)))
    if not invite:
        raise InvalidInvitation(INVALID_INVITE)
    return invite


def create_invite(
    db: Session,
    *,
    label: str = "",
    expires_at: datetime | None = None,
    created_by: int | None = None,
) -> tuple[Invitation, str]:
    if len(label) > 120 or (expires_at and expires_at <= utcnow()):
        raise ValueError("Use a label of at most 120 characters and a future expiry.")
    token = secrets.token_urlsafe(32)
    row = Invitation(
        token_hash=digest(token), label=label.strip(), expires_at=expires_at, created_by=created_by
    )
    db.add(row)
    audit(db, Event.INVITE_CREATED, actor_id=created_by)
    db.flush()
    return row, token


def consume_invite(db: Session, token: str, username: str, password: str) -> User:
    validate_invite(db, token)
    user = prepare_user(username, password)
    # The conditional UPDATE, not the earlier SELECT, is the single-use boundary.
    claimed = db.execute(update(Invitation).where(*usable(token)).values(used_at=utcnow()))
    if cast(CursorResult, claimed).rowcount != 1:
        db.rollback()
        raise InvalidInvitation(INVALID_INVITE)
    try:
        db.add(user)
        db.flush()
        db.execute(
            update(Invitation)
            .where(Invitation.token_hash == digest(token))
            .values(used_by_user_id=user.id)
        )
        audit(db, Event.USER_REGISTERED, user_id=user.id, actor_id=user.id)
        audit(db, Event.INVITE_USED, user_id=user.id, actor_id=user.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError("Choose a different username.") from None
    return user


def revoke_invite(db: Session, invite_id: int, actor_id: int | None = None) -> bool:
    result = db.execute(
        update(Invitation)
        .where(
            Invitation.id == invite_id,
            Invitation.used_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    )
    if cast(CursorResult, result).rowcount:
        audit(db, Event.INVITE_REVOKED, actor_id=actor_id)
    db.commit()
    return bool(cast(CursorResult, result).rowcount)


def invite_status(invite: Invitation) -> str:
    if invite.used_at:
        return "Used"
    if invite.revoked_at:
        return "Revoked"
    if invite.expires_at and invite.expires_at <= utcnow():
        return "Expired"
    return "Available"
