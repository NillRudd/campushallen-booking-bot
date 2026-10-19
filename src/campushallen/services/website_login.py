"""Persistent website password protection. Never used for Campushallen credentials.

SQLite's write boundary serializes the check, password verification and counter
update. Single-process web deployment plus this boundary limits concurrent Argon2
memory usage and prevents lost failure increments, including separate connections.
No raw usernames, IPs or passwords are recorded in the buckets.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from campushallen.database.models import User, WebsiteLoginBucket, utcnow
from campushallen.services.audit import Event, audit
from campushallen.services.authentication import authenticate, token_hash


@dataclass(frozen=True)
class LoginDecision:
    user: User | None = None
    limited: bool = False


def bucket_key(secret: str, kind: str, value: str) -> str:
    return token_hash(secret, f"website-login:{kind}:{value}")


def _bucket(db: Session, key: str, now: datetime, window: timedelta) -> WebsiteLoginBucket:
    row = db.get(WebsiteLoginBucket, key)
    if row is None:
        row = WebsiteLoginBucket(key=key, attempts=0, window_started_at=now, updated_at=now)
        db.add(row)
    elif row.locked_until and row.locked_until <= now:
        audit(db, Event.WEBSITE_LOGIN_LOCK_EXPIRED)
        row.attempts, row.window_started_at, row.locked_until = 0, now, None
    elif not row.locked_until and row.window_started_at + window <= now:
        row.attempts, row.window_started_at = 0, now
    return row


def password_login(
    db: Session,
    secret: str,
    username: str,
    password: str,
    source_ip: str,
    *,
    now: datetime | None = None,
) -> LoginDecision:
    """Call with a fresh session; commits safe audit events and limits on every path."""
    now = now or utcnow()
    # Acquire before reading counters. No external network call occurs in this transaction.
    db.execute(text("BEGIN IMMEDIATE"))
    db.execute(
        delete(WebsiteLoginBucket).where(WebsiteLoginBucket.updated_at < now - timedelta(days=1))
    )
    ip = _bucket(db, bucket_key(secret, "ip", source_ip), now, timedelta(minutes=15))
    if ip.attempts >= 20:
        db.commit()
        return LoginDecision(limited=True)
    ip.attempts += 1
    ip.updated_at = now
    account = _bucket(
        db, bucket_key(secret, "account", username.strip().casefold()), now, timedelta(minutes=30)
    )
    if account.locked_until and account.locked_until > now:
        db.commit()
        return LoginDecision(limited=True)
    # Oversized passwords consume the same counters but are never passed to Argon2.
    user = authenticate(db, username, password) if len(password) <= 4096 else None
    if user:
        account.attempts, account.locked_until, account.window_started_at = 0, None, now
        account.updated_at = now
        audit(db, Event.WEBSITE_LOGIN_SUCCESS, user_id=user.id, actor_id=user.id)
        db.commit()
        return LoginDecision(user=user)
    account.attempts += 1
    account.updated_at = now
    audit(db, Event.LOGIN_FAILED)
    audit(db, Event.WEBSITE_LOGIN_FAILED)
    if account.attempts >= 10:
        account.locked_until = now + timedelta(minutes=30)
        audit(db, Event.WEBSITE_LOGIN_LOCKED)
    limited = account.locked_until is not None
    db.commit()
    return LoginDecision(limited=limited)
