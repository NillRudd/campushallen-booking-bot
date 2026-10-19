import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from campushallen.database.models import User, WebSession, utcnow

PASSWORDS = PasswordHasher()
# Used for equal-cost checks when the username does not exist.
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


def create_user(db: Session, username: str, password: str) -> User:
    username = username.strip().casefold()
    if not 1 <= len(username) <= 254 or not 12 <= len(password) <= 1024:
        raise ValueError("Use a username and a password of 12–1024 characters.")
    user = User(username=username, password_hash=PASSWORDS.hash(password))
    db.add(user)
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
        user.password_hash = PASSWORDS.hash(password)
    return user


def token_hash(secret: str, token: str) -> str:
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def new_session(db: Session, user: User, secret: str, hours: int) -> str:
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
