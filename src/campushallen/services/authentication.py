import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from campushallen.database.models import User, WebSession, utcnow

PASSWORDS = PasswordHasher()
# Used for equal-cost checks when the username does not exist.
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


def prepare_user(username: str, password: str, *, is_admin: bool = False) -> User:
    username = username.strip().casefold()
    if not 1 <= len(username) <= 254 or any(c.isspace() or ord(c) < 32 for c in username):
        raise ValueError("Use a username of 1–254 characters without spaces or control characters.")
    validate_password(password)
    return User(username=username, password_hash=PASSWORDS.hash(password), is_admin=is_admin)


def validate_password(password: str) -> None:
    if not 12 <= len(password) <= 4096:
        raise ValueError("Use a website password of 12–4096 characters.")


def create_user(db: Session, username: str, password: str, *, is_admin: bool = False) -> User:
    from .audit import Event, audit

    user = prepare_user(username, password, is_admin=is_admin)
    db.add(user)
    db.flush()
    audit(db, Event.USER_CREATED, user_id=user.id)
    db.commit()
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username.strip().casefold()))
    try:
        PASSWORDS.verify(user.password_hash if user else DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return None
    if not user or not user.is_active:
        return None
    if PASSWORDS.check_needs_rehash(user.password_hash):
        changed = db.execute(
            update(User)
            .where(User.id == user.id, User.password_hash == user.password_hash)
            .values(password_hash=PASSWORDS.hash(password))
            .returning(User.id)
        ).scalar_one_or_none()
        if changed is None:
            db.rollback()
            return None
    return user


def token_hash(secret: str, token: str) -> str:
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def new_session(db: Session, user: User, secret: str, hours: int) -> str:
    # Serialize session creation against password changes and account deletion.
    allowed = db.execute(
        update(User)
        .where(
            User.id == user.id, User.password_hash == user.password_hash, User.is_active.is_(True)
        )
        .values(updated_at=User.updated_at)
        .returning(User.id)
    ).scalar_one_or_none()
    if allowed is None:
        db.rollback()
        raise ValueError("Login state changed; sign in again.")
    token = secrets.token_urlsafe(48)
    db.execute(delete(WebSession).where(WebSession.expires_at <= utcnow()))
    db.add(
        WebSession(
            token_hash=token_hash(secret, token),
            user_id=user.id,
            expires_at=utcnow() + timedelta(hours=hours),
        )
    )
    db.commit()
    return token


class LoginLimiter:
    """Bounded in-process limiter for a single-process development server.

    Replace this adapter with shared storage before running multiple web processes.
    """

    def __init__(self, limit: int = 10, window: float = 300):
        self.limit, self.window = limit, window
        self.entries: OrderedDict[str, list[float]] = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.entries.pop(key, []) if now - t < self.window]
            allowed = len(recent) < self.limit
            if allowed:
                recent.append(now)
            self.entries[key] = recent
            while len(self.entries) > 4096:
                self.entries.popitem(last=False)
            return allowed
