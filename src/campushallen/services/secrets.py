"""Authenticated token encryption; keys never live in SQLite."""

import json
import os
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from campushallen.settings import Settings


class ReconnectRequired(Exception):
    pass


def runtime_key(settings: Settings, name: str) -> str:
    configured = getattr(settings, name)
    if configured:
        value = configured.get_secret_value()
        if name == "secret_key" and len(value) < 32:
            raise ValueError("SECRET_KEY must contain at least 32 characters")
        return value
    if settings.app_env != "development":
        raise ValueError(f"{name.upper()} is required outside development")
    settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = settings.state_dir / name
    value = (
        Fernet.generate_key().decode() if name == "encryption_key" else secrets.token_urlsafe(48)
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    with os.fdopen(fd, "w") as stream:
        stream.write(value)
    return value


class TokenVault:
    def __init__(self, key: str):
        self.cipher = Fernet(key.encode())

    def seal(self, state: dict[str, Any]) -> str:
        return self.cipher.encrypt(json.dumps(state).encode()).decode()

    def open(self, ciphertext: str | None) -> dict[str, Any]:
        if not ciphertext:
            raise ReconnectRequired("Reconnect this Campushallen account.")
        try:
            return json.loads(self.cipher.decrypt(ciphertext.encode()))
        except (InvalidToken, ValueError, TypeError) as from_error:
            raise ReconnectRequired(
                "Token state is unavailable; reconnect this account."
            ) from from_error
