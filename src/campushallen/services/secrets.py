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


class CredentialVault:
    """HKDF-SHA256 purpose separation, plus account binding inside the encrypted payload.

    Legacy token Fernet keys remain unchanged so upgrades preserve existing tokens.
    This derived key is exclusively for credential encryption version 1.
    """

    def __init__(self, master_key: str):
        import base64

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        # Fernet validates the master format before it is used as key material.
        Fernet(master_key.encode())
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"campushallen-booker/credentials/v1",
        ).derive(base64.urlsafe_b64decode(master_key))
        self.cipher = Fernet(base64.urlsafe_b64encode(derived))

    def seal(self, account_id: int, password: str) -> str:
        return self.cipher.encrypt(
            json.dumps({"account_id": account_id, "password": password}).encode()
        ).decode()

    def open(self, account_id: int, ciphertext: str, version: int = 1) -> str:
        try:
            if version != 1:
                raise ValueError("Unsupported version")
            data = json.loads(self.cipher.decrypt(ciphertext.encode()))
            if data["account_id"] != account_id or not isinstance(data["password"], str):
                raise ValueError("Credential belongs to another account")
            return data["password"]
        except (InvalidToken, ValueError, TypeError, KeyError):
            raise ReconnectRequired(
                "Saved credential is unavailable; replace it or reconnect."
            ) from None
